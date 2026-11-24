#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stand 槽位部署预检 (阶段 3)。任何一项不过即退出非零, 不部署。

四项检查:
  1. ONNX 形状      : 必须 61 -> 14 (复用 publish/manifest.check_onnx, 即固件加载时的同一道门)
  2. ONNX 冒烟      : 合理输入跑 50 步, 输出出现 NaN/Inf/饱和即拒 (复用 smoke_run_onnx)
  3. PT↔ONNX 保真   : 同一条观测喂 .pt 策略和 ONNX, 动作最大差 < 1e-4
                      (导出烘焙出错时, 形状对但数值错, 前两项查不出来)
  4. 契约           : 照固件 Policy::load / deployment.rs 的规则——
                      task 前缀与槽位匹配、slot 字段、哈希链、ONNX 与包内记录一致

只校验, 不上传。不调用 publish 的上传路径。

用法:
  python preflight.py --onnx <policy.onnx> --task <训练任务名> [--ckpt <model.pt>] [--pkg <包目录>]
输出: JSON 报告到 stdout, 退出码 0=全过 1=有失败
"""
import argparse
import copy
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

RL = Path(r"C:\duck_work\microduck_rl")
sys.path.insert(0, str(RL / "src"))
sys.path.insert(0, str(RL))

# 固件认的槽位与 task 前缀的对应 (robotd: task_slot() 按 task 前缀推导槽位)
SLOT_PREFIXES = {
    "stand": "Mjlab-StandUp-",
    "walk": ("Mjlab-VelStand-", "Mjlab-Velocity-", "Mjlab-VelStand"),
    "sitstand": "Mjlab-SitStand-",
}
OBS_LEN, ACTION_LEN = 61, 14
FIDELITY_TOL = 1e-4


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check_shape_and_smoke(onnx_path):
    """复用仓库里现成的两道门, 不自己写。"""
    from mjlab_microduck.publish.manifest import check_onnx, smoke_run_onnx
    out = {}
    try:
        shape = check_onnx(Path(onnx_path))
        out["shape"] = {"ok": True, "obs": shape.obs_len, "actions": shape.action_len,
                        "input": shape.input_name, "output": shape.output_name}
    except Exception as exc:
        out["shape"] = {"ok": False, "error": str(exc)[:300]}
        return out
    try:
        smoke_run_onnx(Path(onnx_path))
        out["smoke"] = {"ok": True}
    except Exception as exc:
        out["smoke"] = {"ok": False, "error": str(exc)[:300]}
    return out


def check_fidelity(onnx_path, ckpt, task):
    """同一条观测喂两边, 比动作。ONNX 图定死 batch=1, 逐条喂。"""
    import numpy as np
    import torch
    import onnxruntime as ort
    from mjlab.envs import ManagerBasedRlEnv
    from mjlab.rl import RslRlVecEnvWrapper, MjlabOnPolicyRunner
    from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls

    cfg = copy.deepcopy(load_env_cfg(task, play=True))
    cfg.scene.num_envs = 8
    cfg.curriculum = {}
    for ev in ("push_robot", "topple_push"):
        cfg.events.pop(ev, None)
    ag = copy.deepcopy(load_rl_cfg(task))
    env = ManagerBasedRlEnv(cfg=cfg, device="cuda:0")
    env = RslRlVecEnvWrapper(env, clip_actions=ag.clip_actions)
    rc = (load_runner_cls(task) or MjlabOnPolicyRunner)(env, asdict(ag), device="cuda:0")
    rc.load(ckpt, load_cfg={"actor": True}, strict=True, map_location="cuda:0")
    policy = rc.get_inference_policy(device="cuda:0")
    # 展平观测: 普通模型 obs["actor"] 就是; 双专家模型要用它自己的 _get_flat_obs
    inner = rc.alg.get_policy()
    get_flat = getattr(inner, "_get_flat_obs", None)
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    obs, _ = env.reset()
    diffs = []
    for _ in range(200):
        with torch.no_grad():
            a_pt = policy(obs)
            flat = get_flat(obs) if get_flat else (
                obs["actor"] if not torch.is_tensor(obs) else obs)
            d = 0.0
            for e in range(flat.shape[0]):
                a_ox = sess.run(None, {iname: flat[e:e + 1].detach().cpu().numpy().astype(np.float32)})[0]
                d = max(d, float(np.abs(a_pt[e].detach().cpu().numpy() - a_ox[0]).max()))
        diffs.append(d)
        obs, _, _, _ = env.step(a_pt)
    env.unwrapped.close()
    mx = float(np.max(diffs))
    return {"ok": mx < FIDELITY_TOL, "max_abs_diff": mx, "tol": FIDELITY_TOL, "steps": len(diffs)}


def check_contract(onnx_path, slot, pkg):
    """照固件规则。pkg 给了就查包内文件; 没给只查 ONNX 自带的元数据。"""
    out = {"slot": slot}
    want_prefix = SLOT_PREFIXES.get(slot)
    if not want_prefix:
        return {"ok": False, "error": "未知槽位 %s" % slot, "slot": slot}

    onnx_sha = sha256_file(onnx_path)
    if pkg is None:
        # 只看 ONNX 元数据里的 task
        import onnx
        meta = {p.key: p.value for p in onnx.load(str(onnx_path)).metadata_props}
        task = meta.get("task_id", "")
        out["task"] = task
        out["task_ok"] = task.startswith(want_prefix)
        out["note"] = "未给 --pkg, 只校验了 ONNX 元数据里的 task"
        out["ok"] = out["task_ok"]
        return out

    pkg = Path(pkg)
    files = {n: (pkg / n) for n in ("policy.onnx", "manifest.json", "deployment-runtime.json",
                                    "deployment-contract.json")}
    missing = [n for n, p in files.items() if not p.is_file()]
    if missing:
        return {"ok": False, "slot": slot, "error": "包内缺文件: %s" % missing}

    m = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
    r = json.loads(files["deployment-runtime.json"].read_text(encoding="utf-8"))
    c = json.loads(files["deployment-contract.json"].read_text(encoding="utf-8"))
    pkg_sha = sha256_file(files["policy.onnx"])

    out.update({
        "manifest_task": m.get("task"),
        "contract_task": c.get("task"),
        "contract_slot": c.get("slot"),
        "manifest_slot": m.get("slot"),
    })
    problems = []
    # 哈希链: manifest == runtime == 包内 onnx 实际哈希 == 待部署 onnx
    if not (m.get("onnx_sha256") == r.get("model_sha256") == pkg_sha):
        problems.append("包内哈希链断裂")
    if pkg_sha != onnx_sha:
        problems.append("包内 policy.onnx 与待部署 ONNX 不是同一个文件")
    # task: manifest 与 contract 一致, 且前缀与槽位匹配
    if not m.get("task") or m.get("task") != c.get("task"):
        problems.append("manifest.task 与 contract.task 不一致")
    task = c.get("task") or ""
    if not task.startswith(want_prefix):
        problems.append("task %r 的前缀不属于 %s 槽 (固件会整体拒载)" % (task, slot))
    if c.get("slot") != slot:
        problems.append("contract.slot=%r, 目标槽位是 %s" % (c.get("slot"), slot))
    out["problems"] = problems
    out["ok"] = not problems
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--task", required=True, help="训练用的任务名")
    ap.add_argument("--slot", default="stand", choices=list(SLOT_PREFIXES))
    ap.add_argument("--ckpt", help="对应的 .pt 检查点, 给了才做保真检查")
    ap.add_argument("--pkg", help="打包后的目录, 给了才做完整契约检查")
    ap.add_argument("--skip-fidelity", action="store_true")
    a = ap.parse_args()

    report = {"onnx": str(Path(a.onnx).resolve()), "onnx_sha256": sha256_file(a.onnx),
              "task": a.task, "slot": a.slot,
              "time": __import__("time").strftime("%Y-%m-%d %H:%M:%S"), "checks": {}}

    print("[1/4] 形状 + 冒烟 ...", flush=True)
    r = check_shape_and_smoke(a.onnx)
    report["checks"]["shape"] = r["shape"]
    report["checks"]["smoke"] = r["smoke"]

    if a.skip_fidelity or not a.ckpt:
        report["checks"]["fidelity"] = {"ok": None, "skipped": True,
                                        "reason": "未给 --ckpt 或指定 --skip-fidelity"}
    else:
        print("[2/4] PT↔ONNX 保真 ...", flush=True)
        try:
            report["checks"]["fidelity"] = check_fidelity(a.onnx, a.ckpt, a.task)
        except Exception as exc:
            report["checks"]["fidelity"] = {"ok": False, "error": repr(exc)[:400]}

    print("[3/4] 契约 ...", flush=True)
    report["checks"]["contract"] = check_contract(a.onnx, a.slot, a.pkg)

    print(json.dumps(report, ensure_ascii=False, indent=1))
    failed = [k for k, v in report["checks"].items() if v.get("ok") is False]
    print("\n===== %s =====" % ("PASS 全部通过" if not failed else "FAIL: " + ", ".join(failed)),
          flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
