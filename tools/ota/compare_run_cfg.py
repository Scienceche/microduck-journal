#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 run15 的实际训练配置(params/env.yaml) 与现役 stand 包契约里归档的训练快照。

目的: 候选包(run15@2000)的契约要从 run15 自己的配置来。固件运行时读取的字段
(observations / actions / commands / sim 的频率)必须与 run15 的训练一致; 这里先量出
run6(现役) 与 run15 在这些字段上到底差在哪。
用法: python compare_run_cfg.py
"""
import json
import sys
from pathlib import Path

import yaml


class _Tuples(yaml.SafeLoader):
    """SafeLoader + tyro 导出的 python/tuple 标签(其余标签仍然拒绝)。"""


_Tuples.add_constructor(
    "tag:yaml.org,2002:python/tuple",
    lambda loader, node: tuple(loader.construct_sequence(node)),
)
# tyro 还会写 python/object/apply: 与 python/name: 标签(查看器配置、枚举等)。不照着文件去
# 构造对象, 只记成一个字符串占位 —— 这些段落与契约无关。
_Tuples.add_multi_constructor(
    "tag:yaml.org,2002:python/object/apply:", lambda loader, suffix, node: f"<object/apply:{suffix}>"
)
_Tuples.add_multi_constructor(
    "tag:yaml.org,2002:python/name:", lambda loader, suffix, node: f"<name:{suffix}>"
)

LIVE = Path(r"C:\duck_work\pkg_build\models\13f456ab9548ee17cf51da6f28f747913856bf9357eab212f306f0140829d166")
RUN15 = Path(r"C:\duck_work\microduck_rl\logs\rsl_rl\microduck_stand\2026-11-24_13-50-09_microduck_stand")


def brief(v, n=110):
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


def walk(x, y, path="", out=None):
    """逐叶对比, 返回 [(路径, 官方/run6, run15)]"""
    if out is None:
        out = []
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y), key=str):
            if k not in x:
                out.append((f"{path}.{k}", "<无>", brief(y[k])))
            elif k not in y:
                out.append((f"{path}.{k}", brief(x[k]), "<无>"))
            else:
                walk(x[k], y[k], f"{path}.{k}", out)
    elif isinstance(x, list) and isinstance(y, list):
        if len(x) != len(y):
            out.append((path, f"list[{len(x)}]", f"list[{len(y)}]"))
        else:
            for i, (a, b) in enumerate(zip(x, y)):
                walk(a, b, f"{path}[{i}]", out)
    elif x != y:
        out.append((path, brief(x), brief(y)))
    return out


def main():
    contract = json.loads((LIVE / "deployment-contract.json").read_text(encoding="utf-8"))
    snap = contract["training_snapshot"]
    print("现役契约快照: task =", snap.get("task"), "| provenance =", snap.get("provenance"))
    print("现役契约顶层: task =", contract["task"], "| export_contract_revision =", contract.get("export_contract_revision"))
    live_env = snap["full"]["env"]

    env15 = yaml.load((RUN15 / "params" / "env.yaml").read_text(encoding="utf-8"), Loader=_Tuples)
    print("run15 env.yaml 顶层键:", sorted(env15.keys()))
    print("现役快照 env 顶层键 :", sorted(live_env.keys()))
    print()

    # 固件运行时会读的几块
    for key in ("observations", "actions", "commands", "sim", "scene"):
        d = walk(live_env.get(key), env15.get(key), key)
        print(f"=== {key}: {len(d)} 处不同 ===")
        for row in d[:30]:
            print("   ", " | ".join(str(c)[:120] for c in row))
        if len(d) > 30:
            print("    ... 还有", len(d) - 30, "处")
    print()

    # 固件解析器点名校验的字段, 两边各打印一次
    def term_callable(env, name):
        return env["observations"]["actor"]["terms"][name]["func"]["callable"]
    checks = []
    for name in ("joint_pos", "joint_vel", "head_command", "body_command", "command"):
        checks.append((f"obs.terms.{name}.callable", term_callable(live_env, name), term_callable(env15, name)))
    for name in ("head_command", "body_command"):
        dim6 = live_env["observations"]["actor"]["terms"][name].get("params", {}).get("dim")
        dim15 = env15["observations"]["actor"]["terms"][name].get("params", {}).get("dim")
        checks.append((f"obs.terms.{name}.params.dim", dim6, dim15))
    hz = lambda e: 1.0 / (e["sim"]["mujoco"]["timestep"] * e["decimation"])
    checks.append(("控制频率 Hz", hz(live_env), hz(env15)))
    checks.append(("actions.joint_pos.scale", live_env["actions"]["joint_pos"].get("scale"), env15["actions"]["joint_pos"].get("scale")))
    checks.append(("actions.joint_pos.use_default_offset", live_env["actions"]["joint_pos"].get("use_default_offset"), env15["actions"]["joint_pos"].get("use_default_offset")))
    checks.append(("commands.twist.ramp_s", live_env["commands"]["twist"].get("ramp_s"), env15["commands"]["twist"].get("ramp_s")))
    for name in ("head_pose", "body_pose"):
        checks.append((f"commands.{name}.ranges", live_env["commands"][name].get("ranges"), env15["commands"][name].get("ranges")))
    print("=== 固件解析器逐项校验的字段: run6(现役) | run15 ===")
    same = 0
    for name, a, b in checks:
        eq = a == b
        same += eq
        print(f"   {'相同' if eq else '不同'}  {name}\n          run6 : {brief(a, 150)}\n          run15: {brief(b, 150)}")
    print(f"→ {same}/{len(checks)} 项相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
