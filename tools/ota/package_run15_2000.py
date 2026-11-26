#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run15@2000 的 stand 候选包(先给影子模式用, 不驱动)。

以现役 stand 包(13f456ab, 任务 Rough-Backlash)为底:
  保持 —— 代表这台机器人适配的字段: HOME(model.joints[].home_rad)、slot=stand、role=joint、
          teacher_only=false、hardware_calibration、deployment-runtime.json 里的运行时指令范围、
          BAM 参数文件(固件只认 sha ef2d51ad…; 已验证它与 run15 训练用的 hd1910_kissqy/m6.json
          20 个字段逐个数值相同)。
  改掉 —— 任务名改成 run15 真实的 Mjlab-StandUp-Flat-Backlash-MicroDuck; 归档段落
          (training_snapshot / training_physics / commands / onnx_metadata / 溯源字段) 换成 run15
          自己的训练配置与导出元数据。
  断言 —— 固件契约解析器(duck-control/src/deployment.rs, Contract::parse / Contract::load)
          读取的每一个字段, 逐项等于 run15 的 params/env.yaml 或 ONNX 的实测值; 哈希链自洽。
  不带 —— 现役包里的 export-data.zip / training-request.json: 那是 run6 的, 留着就是错的溯源。

为什么 HOME 可以"保持": run15 训练时的默认姿态(env.yaml init_state)就是 -0.335/0.3315,
与现役包的 HOME 相同; 脚本对 14 个关节逐个断言这一点。
用法: C:\\duck_work\\microduck_rl\\.venv\\Scripts\\python.exe package_run15_2000.py
"""
import copy
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import mujoco
import onnx
import yaml
from mjlab_microduck.robot.microduck_constants import (
    MICRODUCK_BACKLASH_ROBOT_CFG,
    MICRODUCK_GROUNDCONTACT_BACKLASH_XML,
)

BASE = Path(r"C:\duck_work\pkg_build\models\13f456ab9548ee17cf51da6f28f747913856bf9357eab212f306f0140829d166")
RUN = Path(r"C:\duck_work\microduck_rl\logs\rsl_rl\microduck_stand\2026-11-24_13-50-09_microduck_stand")
ONNX = RUN / "export_2000" / "policy.onnx"
CKPT = RUN / "model_2000.pt"
BAM_TRAIN = Path(r"C:\duck_work\bam\bam\params\hd1910_kissqy\m6.json")
TRAIN_REPO = Path(r"C:\duck_work\microduck_rl")
TASK = "Mjlab-StandUp-Flat-Backlash-MicroDuck"
OUT_ROOT = Path(r"C:\duck_work\pkg_build\models")
FIRMWARE_BAM_SHA = "ef2d51adfb1cc0831b9b02ca19aa9176fceecdf725148d084378d7d9a64afceb"
JOINTS = [
    "left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee", "left_ankle",
    "neck_pitch", "head_pitch", "head_yaw", "head_roll",
    "right_hip_yaw", "right_hip_roll", "right_hip_pitch", "right_knee", "right_ankle",
]
OBS_TERMS = [
    "base_ang_vel", "projected_gravity", "joint_pos", "joint_vel",
    "actions", "command", "head_command", "body_command",
]


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def canonical(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


# ---------------------------------------------------------------- 读回 run15 的训练配置
class _Name(str):
    """tyro 写成 !!python/name: 的值(函数/类/枚举成员的点名)。"""


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("tag:yaml.org,2002:python/tuple", lambda l, n: tuple(l.construct_sequence(n, deep=True)))
_Loader.add_multi_constructor("tag:yaml.org,2002:python/name:", lambda l, s, n: _Name(s))
_Loader.add_multi_constructor(
    "tag:yaml.org,2002:python/object/apply:",
    lambda l, s, n: ("__apply__", s, l.construct_sequence(n, deep=True)),
)


def rb(v):
    """把读回值变成可 JSON 归档的形式。python 专有标签只记录, 不照着构造对象。"""
    if isinstance(v, _Name):
        return {"callable": str(v)}
    if isinstance(v, tuple) and len(v) == 3 and v[0] == "__apply__":
        _, tag, args = v
        a = rb(list(args))
        if tag == "builtins.slice":
            return {"slice": a}
        if tag == "builtins.getattr" and len(a) == 2:
            return {"getattr": a}
        # 枚举成员: TransmissionType.JOINT 这类, Studio 的快照里就记成普通字符串
        if len(a) == 1 and isinstance(a[0], (str, bool, int, float)):
            return a[0]
        return {"python/object/apply": tag, "args": a}
    if isinstance(v, (tuple, list)):
        return [rb(x) for x in v]
    if isinstance(v, dict):
        return {str(k): rb(x) for k, x in v.items()}
    return v


env_text = (RUN / "params" / "env.yaml").read_text(encoding="utf-8")
agent_text = (RUN / "params" / "agent.yaml").read_text(encoding="utf-8")
env = yaml.load(env_text, Loader=_Loader)
agent = yaml.load(agent_text, Loader=_Loader)
assert env is not None and agent is not None, "run15 的 params yaml 读不回来"
env_rb, agent_rb = rb(env), rb(agent)

# ---------------------------------------------------------------- 读回现役包与 ONNX
base_c = json.loads((BASE / "deployment-contract.json").read_text(encoding="utf-8"))
base_m = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
base_r = json.loads((BASE / "deployment-runtime.json").read_text(encoding="utf-8"))
assert sha256_file(BASE / "policy.onnx") == BASE.name, "现役包目录名应等于其 ONNX 的 sha256"
assert base_c["task"] == "Mjlab-StandUp-Rough-Backlash-MicroDuck", base_c["task"]

NEW_SHA = sha256_file(ONNX)
model = onnx.load(str(ONNX), load_external_data=False)
onnx_meta = {p.key: p.value for p in model.metadata_props}
assert onnx_meta["task_id"] == TASK, onnx_meta
assert onnx_meta["checkpoint_sha256"] == sha256_file(CKPT), "ONNX 里记的检查点哈希与 model_2000.pt 不符"
shape = lambda t: [d.dim_value or d.dim_param for d in t.type.tensor_type.shape.dim]
assert [shape(i) for i in model.graph.input] == [[1, 61]] and [shape(o) for o in model.graph.output] == [[1, 14]]
OUT = OUT_ROOT / NEW_SHA
assert not OUT.exists(), f"输出目录已存在: {OUT} (要重建先手动删除)"

# ---------------------------------------------------------------- 机器人模型: 关节顺序、整机质量
robot_spec_fn = getattr(MICRODUCK_BACKLASH_ROBOT_CFG, "spec_fn", None)
assert robot_spec_fn is not None and getattr(robot_spec_fn, "__name__", "") == "get_backlash_spec", robot_spec_fn
robot_mj = mujoco.MjModel.from_xml_path(str(MICRODUCK_GROUNDCONTACT_BACKLASH_XML))
xml_joints = [mujoco.mj_id2name(robot_mj, mujoco.mjtObj.mjOBJ_JOINT, i) for i in range(robot_mj.njnt)]
xml_order = [j for j in xml_joints if j in JOINTS]
assert xml_order == JOINTS, f"机器人 XML 的关节顺序与固件不一致: {xml_order}"
assert xml_order == base_c["runtime_joint_order"], "现役契约的 runtime_joint_order 与 XML 不一致"
total_mass = float(robot_mj.body_mass.sum())
assert abs(total_mass - 0.828) < 0.002, f"整机质量 {total_mass:.4f} kg, 不是 828g 模型"

# ---------------------------------------------------------------- 断言: 固件读取的字段 == run15
checked = []


def check(name, ok, detail=""):
    assert ok, f"固件读取的字段与 run15 不符: {name} {detail}"
    checked.append(name)


# 1. HOME: 契约 model.joints[].home_rad == run15 init_state 里该关节的值(后匹配的模式覆盖前面的)
init = env["scene"]["entities"]["robot"]["init_state"]["joint_pos"]


def resolve_init(joint):
    value = None
    for pattern, v in init.items():
        if re.fullmatch(pattern, joint):
            value = float(v)
    return value


home_by_joint = {}
for row in base_c["model"]["joints"]:
    name = row["name"]
    if name == "trunk_base_freejoint" or name.startswith("passive_"):
        continue
    home_by_joint[name] = row["home_rad"]
    want = resolve_init(name)
    check(f"HOME {name}", want is not None and abs(row["home_rad"] - want) < 1e-9, f"契约 {row['home_rad']} vs run15 {want}")
check("HOME 覆盖 14 个关节", sorted(home_by_joint) == sorted(JOINTS))
# 14 个真实关节各自只被一个模式命中, 上面的断言没有歧义。齿隙关节不同: `.*_backlash$` 与更宽的
# `.*left_hip_roll.*` 都能匹配 passive_left_hip_roll_backlash, 而 tyro 导出 yaml 时把键排序了,
# 从 yaml 看不出谁覆盖谁 —— 所以这里不从 init_state 解析, 只断言契约里齿隙关节 HOME 为 0,
# 且 init_state 里有一条把齿隙关节置 0 的模式。固件不读齿隙关节的 HOME。
for name in JOINTS:
    for row in base_c["model"]["joints"]:
        if row["name"] == f"passive_{name}_backlash":
            check(f"齿隙关节 {name} 契约 home=0", abs(row["home_rad"]) < 1e-12)
check("init_state 有齿隙关节置 0 的模式", any(k == ".*_backlash$" and abs(float(v)) < 1e-12 for k, v in init.items()))

# 2. 观测项
terms_c = base_c["observations"]["terms"]
terms_e = env["observations"]["actor"]["terms"]
check("观测项集合", sorted(terms_c) == sorted(terms_e) == sorted(OBS_TERMS), f"{sorted(terms_c)} vs {sorted(terms_e)}")
for name in OBS_TERMS:
    tc, te = terms_c[name], terms_e[name]
    fe = te["func"]
    fe_name = fe if isinstance(fe, str) else fe.get("callable")
    check(f"观测函数 {name}", tc["func"]["callable"] == fe_name, f"{tc['func']['callable']} vs {fe_name}")
    for key in ("scale", "clip", "history_length"):
        check(f"观测 {name}.{key}", tc.get(key) == te.get(key), f"{tc.get(key)} vs {te.get(key)}")
    if name in ("head_command", "body_command"):
        check(f"观测 {name}.params.dim", tc["params"].get("dim") == te["params"].get("dim"))

# 3. 动作映射
ac, ae = base_c["actions"]["joint_pos"], env["actions"]["joint_pos"]
for key in ("scale", "offset", "use_default_offset", "clip"):
    check(f"actions.joint_pos.{key}", ac.get(key) == ae.get(key), f"{ac.get(key)} vs {ae.get(key)}")
check("actions.joint_pos.actuator_names", list(ac["actuator_names"]) == list(ae["actuator_names"]))
check("trained_action_scale", base_c["trained_action_scale"] == ae["scale"])

# 4. 控制频率与滤波
hz = 1.0 / (env["sim"]["mujoco"]["timestep"] * env["decimation"])
check("控制频率 50Hz", abs(hz - 50.0) < 1e-9, hz)
check("contract.control_hz", base_c["control_hz"] == 50.0)
af_c = base_c["action_filter"]
check("action_filter 直通", af_c["enabled"] is False and af_c["head_alpha"] == 1.0 and af_c["legs_alpha"] == 1.0
      and af_c["version"] == 1 and af_c["control_hz"] == 50.0)
check("required_policy_settings", base_c["required_policy_settings"] == {"head_lowpass": 1.0, "legs_lowpass": 1.0, "action_scale": 1.0})
check("run15 动作项里没有额外滤波参数", not any(k in ae for k in ("head_lowpass", "legs_lowpass", "head_alpha", "legs_alpha")), str(list(ae)))

# 5. 指令范围(固件据此算 limits; 头部/身体训练范围必须与现役一致, 机器人行为才不变)
for name in ("head_pose", "body_pose"):
    a = [list(x) for x in base_c["commands"][name]["ranges"]]
    b = [list(x) for x in env["commands"][name]["ranges"]]
    check(f"commands.{name}.ranges", a == b, f"{a} vs {b}")
check("commands.twist.ramp_s", base_c["commands"]["twist"].get("ramp_s") is env["commands"]["twist"].get("ramp_s"))

# 6. BAM 参数: 固件认的文件 与 run15 训练用的文件 逐字段相同
pf = base_c["model"]["actuators"][0]["parameter_file"]
check("parameter_file.sha256 == 固件硬性要求", pf["sha256"] == FIRMWARE_BAM_SHA)
bamb = json.loads(BAM_TRAIN.read_text(encoding="utf-8"))
check("BAM 参数 20 字段逐一相同", pf["data"] == bamb, "Training Studio 的 hd1910_m6.json 与 hd1910_kissqy/m6.json 数值不同")
act_e = env["scene"]["entities"]["robot"]["articulation"]["actuators"]
check("BAM kp_fw", len(act_e) == 1 and float(act_e[0]["kp_fw"]) == base_c["model"]["actuators"][0]["kp_fw"] == 5.0)
check("run15 电机模型", act_e[0]["motor_name"] == "hd1910_kissqy" and act_e[0]["model"] == "m6", str(act_e[0].get("motor_name")))

# 7. 复刻机适配项
check("slot", base_c["slot"] == "stand")
check("role", base_c["role"] == "joint")
check("teacher_only", base_c["teacher_only"] is False)
check("onnx_metadata.joint_names", base_c["onnx_metadata"]["joint_names"] == ",".join(JOINTS))

# ---------------------------------------------------------------- 组装契约
c = copy.deepcopy(base_c)
c["task"] = TASK

snapshot_core = {
    "task": TASK,
    "full": {"env": env_rb, "agent": agent_rb},
    "provenance": "checkpoint_params_yaml",
}
snapshot = {
    **snapshot_core,
    "sha256": hashlib.sha256(canonical(snapshot_core).encode()).hexdigest(),
    "readback": {
        "source": ["params/env.yaml", "params/agent.yaml"],
        "params_sha256": {
            "params/env.yaml": hashlib.sha256(env_text.encode("utf-8")).hexdigest(),
            "params/agent.yaml": hashlib.sha256(agent_text.encode("utf-8")).hexdigest(),
        },
        "note": "训练运行写出的 tyro yaml。python 专有标签(函数点名/对象构造/元组)只记录为可读形式, 未重建对象。",
    },
    "model": {
        "note": "未重做 Studio 式的编译模型巡检。关节与 HOME 见本契约 model.joints; 整机质量由机器人 XML 编译后求和得到。",
        "robot_xml": str(MICRODUCK_GROUNDCONTACT_BACKLASH_XML),
        "robot_xml_sha256": sha256_file(MICRODUCK_GROUNDCONTACT_BACKLASH_XML),
        "total_mass_kg": total_mass,
        "default_joint_pos": {j: home_by_joint[j] for j in JOINTS},
    },
}
c["training_snapshot"] = snapshot
c["training_physics"] = {
    "actuators": copy.deepcopy(base_c["model"]["actuators"]),
    "gear_backlash": copy.deepcopy(base_c["gear_backlash"]),
    "total_mass_kg": total_mass,
    "randomization_events": {k: v for k, v in env_rb["events"].items() if v is not None},
    "curricula": copy.deepcopy(env_rb.get("curriculum", {})),
    "terrain": copy.deepcopy(env_rb["scene"]["terrain"]),
    "simulation": copy.deepcopy(env_rb["sim"]),
    "export_behavior": "archive_actual_training_configuration_without_runtime_switch_conversion",
}
c["commands"] = copy.deepcopy(env_rb["commands"])
c["model"] = {
    "joints": copy.deepcopy(base_c["model"]["joints"]),
    "actuators": copy.deepcopy(base_c["model"]["actuators"]),
    "gear_backlash": copy.deepcopy(base_c["model"]["gear_backlash"]),
    "total_mass_kg": total_mass,
    "training_motor": {
        "motor_name": "hd1910_kissqy",
        "model": "m6",
        "parameter_file": str(BAM_TRAIN),
        "parameter_file_sha256": sha256_file(BAM_TRAIN),
        "note": "run15 训练实际加载的电机参数。文件名与排版不同于固件认的 hd1910_m6.json(sha ef2d51ad…), "
                "但 20 个参数字段逐个数值相同(打包脚本已断言), 所以 actuators[0].parameter_file 如实可用。",
    },
    "note": "joints/actuators/gear_backlash 继承自现役包 13f456ab: 同一机器人、同一 BAM。HOME 已逐关节断言等于 run15 "
            "训练默认姿态。joints[*].body_mass_g 亦继承自现役包, 未重新巡检。bodies/collision_geoms 等 Studio 巡检段落"
            "未重做, 已省略 —— 以训练仓库的机器人 XML 为准。",
}
c["onnx_metadata"] = {
    **onnx_meta,
    "joint_names": ",".join(JOINTS),
    "default_joint_pos": ",".join(f"{home_by_joint[j]:.3f}" for j in JOINTS),
    "note": "task_id/checkpoint_*/exported_at/note 来自 ONNX 文件自身的元数据; joint_names 取自机器人 XML 的关节顺序; "
            "default_joint_pos 取自 run15 训练的 init_state(即本契约的 HOME)。",
}
c["runtime_home_rad"] = [home_by_joint[j] for j in base_c["runtime_joint_order"]]
c["model_origin"] = {
    "tool": "microduck_rl rsl_rl PPO, launcher C:/duck_work/launch_run15.py (不是 Training Studio)",
    "task": TASK,
    "warm_start": {"run": "69u48n8l_9750", "checkpoint": "model_9750.pt", "role": "alpha_stand teacher"},
    "num_envs": 2048,
    "max_iterations": 4000,
    "worktree": str(TRAIN_REPO),
}
c["training_source"] = {"repo": "pollen-robotics/microduck_rl", "worktree": str(TRAIN_REPO),
                        "note": "本地工作副本含复刻机改动(828g 机身高质量、STAND3 默认姿态、Backlash 任务)"}
try:
    head = subprocess.run(["git", "-C", str(TRAIN_REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", head):
        c["training_source"]["commit"] = head
except Exception as e:  # noqa: BLE001
    c["training_source"]["git"] = f"rev-parse 失败: {type(e).__name__}"
c["note"] = "目标固件 0.15.1(契约解析器需认识 Flat-Backlash 起身任务名)。本包先以影子模式运行: 只推理、只记录, 不驱动机器人。"
c["packaging"] = {
    "script": "C:/duck_work/ota/package_run15_2000.py",
            "built_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "base_package": BASE.name,
    "kept_from_base": ["model.joints (HOME)", "model.actuators", "hardware_calibration", "observations", "actions",
                        "action_filter", "required_policy_settings", "runtime_only_settings", "slot/role/teacher_only"],
    "replaced_with_run15": ["task", "training_snapshot", "training_physics", "commands", "onnx_metadata",
                            "model_origin", "training_source", "note"],
    "firmware_read_fields_verified_against_run15": checked,
    "eval_sim": {
        "criteria": "eval_stage2.py: 成功率% / 结束时站立%; 六场景顺序见 C:/duck_work/eval_stage2/summary.json; 每场景多种子",
        "run15@2000": {"seed1": [100, 100, 94, 100, 100, 100], "seed2": [100, 100, 84, 100, 100, 100]},
        "terrain": "Flat",
        "note": "仿真成功率不代表真机; 真机以影子期与之后的落地验收为准。",
    },
}

# ---------------------------------------------------------------- manifest / runtime / model-info
m = copy.deepcopy(base_m)
m.update({
    "name": "standup_run15_2000_flat_backlash",
    "description": "复刻机起身策略候选: Flat-Backlash StandUp, run15@2000, 828g 整机质量模型, 热启动自 alpha_stand 教师; "
                   "训练默认姿态与真机 HOME 一致(-0.335/0.3315); 先以影子模式上机, 不驱动。",
    "task": TASK,
    "checkpoint": str(CKPT),
    "onnx_sha256": NEW_SHA,
    "training_snapshot_sha256": snapshot["sha256"],
    "teacher_only": False,
    "slot": "stand",
    "deployment_verified": False,
    "model_origin": copy.deepcopy(c["model_origin"]),
    "training": {
        "task_id": TASK,
        "repo": "pollen-robotics/microduck_rl",
        "checkpoint": 2000,
        "source_file": str(CKPT),
        "source_file_sha256": sha256_file(CKPT),
        "launcher": "C:/duck_work/launch_run15.py",
        "warm_start": "69u48n8l_9750/model_9750.pt",
        "note": "microduck_rl rsl_rl 训练(非 Training Studio); 2048 envs x 24 steps/iter",
    },
})
m["command"] = copy.deepcopy(base_m["command"])
m["training_snapshot_sha256"] = snapshot["sha256"]

r = copy.deepcopy(base_r)
r.update({
    "model_sha256": NEW_SHA,
    "source_iteration": 2000,
    "training_step": 2000 * int(base_r["num_steps_per_env"]),
    "source_parameters_sha256": dict(snapshot["readback"]["params_sha256"]),
})
check("runtime 指令范围(运行时覆盖) 与现役相同", r["commands"] == base_r["commands"])

info = {
    "name": "run15@2000 Flat-Backlash 起身候选(影子模式)",
    "source_filename": CKPT.name,
    "onnx_sha256": NEW_SHA,
    "purpose": "shadow candidate: inferred alongside, never commanded",
}

# ---------------------------------------------------------------- 落盘
OUT.mkdir(parents=True)
shutil.copyfile(ONNX, OUT / "policy.onnx")
for name, data in (("deployment-contract.json", c), ("manifest.json", m), ("deployment-runtime.json", r), ("model-info.json", info)):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

# ---------------------------------------------------------------- 读回复核: 哈希链 + 任务契约
m2 = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
c2 = json.loads((OUT / "deployment-contract.json").read_text(encoding="utf-8"))
r2 = json.loads((OUT / "deployment-runtime.json").read_text(encoding="utf-8"))
real = sha256_file(OUT / "policy.onnx")
assert real == NEW_SHA == m2["onnx_sha256"] == r2["model_sha256"], "哈希链断裂"
assert m2["task"] == c2["task"] == TASK, "task 契约不符"
assert r2["schema"] == "microduck-runtime-commands/v1"
assert c2["slot"] == "stand" and c2["role"] == "joint" and c2["teacher_only"] is False
assert c2["model"]["actuators"][0]["parameter_file"]["sha256"] == FIRMWARE_BAM_SHA
assert c2["model"]["actuators"][0]["kp_fw"] == 5.0
assert {j["name"]: j["home_rad"] for j in c2["model"]["joints"]} == {j["name"]: j["home_rad"] for j in base_c["model"]["joints"]}, "HOME 被改动了"
assert m2["obs_len"] == 61 and m2["action_len"] == 14
assert {k: v["func"]["callable"] for k, v in c2["observations"]["terms"].items()} == {k: v["func"]["callable"] for k, v in base_c["observations"]["terms"].items()}

print("包目录:", OUT)
print("ONNX sha256:", NEW_SHA)
print("task:", TASK, "| slot: stand | 整机质量 %.3f kg | 训练默认姿态==HOME: %s" % (total_mass, all(abs(home_by_joint[n] - resolve_init(n)) < 1e-9 for n in JOINTS)))
print("固件读取字段逐项核对通过: %d 项" % len(checked))
print("哈希链 ✓ (文件 == manifest.onnx_sha256 == runtime.model_sha256)   task 契约 ✓ (manifest == contract)")
print("training_snapshot.sha256:", snapshot["sha256"])
print("文件:")
for p in sorted(OUT.iterdir()):
    print("   %-28s %8d 字节  %s" % (p.name, p.stat().st_size, sha256_file(p)[:16]))
