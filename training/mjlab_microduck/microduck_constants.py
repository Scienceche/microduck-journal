import os
from pathlib import Path

import mujoco
from mjlab.actuator import XmlActuatorCfg
from mjlab_microduck.actuator import (
    BacklashEncoderBamActuatorCfg,
    FrictionDRBamActuatorCfg,
)
from mjlab.entity import EntityArticulationInfoCfg, EntityCfg
from mjlab.utils.spec_config import CollisionCfg


_ROBOT_DIR: Path = Path(os.path.dirname(__file__)) / "microduck"

MICRODUCK_WALK_XML: Path = _ROBOT_DIR / "robot_walk.xml"
# Ground-contact model (formerly "allcollisions"): curated collision set for
# the parts that touch the floor in body-on-ground tasks (soles, legs, trunk
# shells, head shells, jaw, battery, hips) — NOT every geom. Shared by
# standup / ground-pick / sitstand / roulade / walk-rollers tasks.
MICRODUCK_GROUNDCONTACT_XML: Path = _ROBOT_DIR / "robot_groundcontact.xml"
# TRUE all-collisions model: every part carries a collision geom (70 geoms,
# 37 meshes; power_support demoted to self_collision_only like every variant).
# No task uses it yet — exported 2026-09 for future envs needing full contact.
MICRODUCK_ALLCOLLISIONS_XML: Path = _ROBOT_DIR / "robot_allcollisions.xml"
# 70mm / 15g ball prop for the BallKick task.
MICRODUCK_BALL_XML: Path = _ROBOT_DIR / "ball.xml"
# Roller-skate model: 14 actuated joints + passive wheel hinges (passive_*wheel).
MICRODUCK_GROUNDCONTACT_ROLLERS_XML: Path = _ROBOT_DIR / "robot_groundcontact_rollers.xml"
# Backlash models: every servo joint gets an unactuated passive_<joint>_backlash
# hinge in series (±1° play, 2° total). Exported via
# config_mjcf_{groundcontact,walk}_backlash.json (add_backlash.py post-processor).
MICRODUCK_GROUNDCONTACT_BACKLASH_XML: Path = _ROBOT_DIR / "robot_groundcontact_backlash.xml"
MICRODUCK_WALK_BACKLASH_XML: Path = _ROBOT_DIR / "robot_walk_backlash.xml"
MICRODUCK_GROUNDCONTACT_ROLLERS_BACKLASH_XML: Path = _ROBOT_DIR / "robot_groundcontact_rollers_backlash.xml"
MICRODUCK_ALLCOLLISIONS_BACKLASH_XML: Path = _ROBOT_DIR / "robot_allcollisions_backlash.xml"

assert MICRODUCK_WALK_XML.exists(), f"XML not found: {MICRODUCK_WALK_XML}"
assert MICRODUCK_GROUNDCONTACT_XML.exists(), f"XML not found: {MICRODUCK_GROUNDCONTACT_XML}"
assert MICRODUCK_ALLCOLLISIONS_XML.exists(), f"XML not found: {MICRODUCK_ALLCOLLISIONS_XML}"
assert MICRODUCK_BALL_XML.exists(), f"XML not found: {MICRODUCK_BALL_XML}"
assert MICRODUCK_GROUNDCONTACT_ROLLERS_XML.exists(), f"XML not found: {MICRODUCK_GROUNDCONTACT_ROLLERS_XML}"
assert MICRODUCK_GROUNDCONTACT_BACKLASH_XML.exists(), f"XML not found: {MICRODUCK_GROUNDCONTACT_BACKLASH_XML}"
assert MICRODUCK_WALK_BACKLASH_XML.exists(), f"XML not found: {MICRODUCK_WALK_BACKLASH_XML}"
assert MICRODUCK_GROUNDCONTACT_ROLLERS_BACKLASH_XML.exists(), f"XML not found: {MICRODUCK_GROUNDCONTACT_ROLLERS_BACKLASH_XML}"
assert MICRODUCK_ALLCOLLISIONS_BACKLASH_XML.exists(), f"XML not found: {MICRODUCK_ALLCOLLISIONS_BACKLASH_XML}"


def get_walk_spec() -> mujoco.MjSpec:
    return adapt_spec_to_replica(mujoco.MjSpec.from_file(str(MICRODUCK_WALK_XML)))


# ---------------------------------------------------------------------------
# Replica adaptation (Feetech HD1910 build).
# ---------------------------------------------------------------------------
# The official model is 737.2 g with its battery (~150 g) inside the head
# shell (jaw_soft totals 188.8 g). Our replica is 919 g measured, with a
# 190 g 2S pouch mounted at the BOTTOM REAR of the trunk — the single
# biggest dynamic difference between the two robots (our CoM is lower and
# further back). Re-mass the CAD model so training sees our robot:
#   1. strip the in-head battery share from jaw_soft,
#   2. weld our bottom-rear battery onto trunk_base (no joint, no geoms),
#   3. scale every other body so the total hits the measured 919 g
#      (heavier HD1910 servos are roughly uniformly distributed).
# Inertias scale with mass (geometrically-similar scaling).
REPLICA_TOTAL_MASS = 0.936  # kg, measured 2026-10-30 (919g + 电池底座 12g + 组装增量)
REPLICA_BATTERY_MASS = 0.202  # kg, 2S pouch 190g + BMS + 电池底座 12g (合体安装)
REPLICA_BATTERY_POS = (-0.024, 0.0, -0.004)  # trunk_base frame: rear cavity, vertical pack
#   - settlement scan correction (2026-11-08): back face at the shell rear line
#     (-0.047, i.e. center -0.024) is the model-consistent limit — pushing to the
#     T12-spacer position (-0.059) intersects the hip housings during settling
#     (sim has no plate mesh; the real 12 mm protrusion lives where the plate is).
#     Verified: standing 0.1155 m, pitch 0.0 deg, zero battery contacts.
# 2026-11-08 derivation from the actual 3D model + official mount recipe:
#   - ray-cast survey of the trunk shells (world, HOME): shells span x -0.047..0.035,
#     z 0.076..0.162; the bay is an OPEN shell (no plate mesh, open floor/rear).
#   - official mount (硬件资料/电池托架/official): pack stands VERTICAL —
#     NP-F970 38.6w x 20.6t x 70.8h against the 83.5-tall power_support back plate;
#     our 38 mm thickness vs 28.3 mm official depth -> T12 spacers push the plate
#     12 mm back, so the pack back face sits ~12 mm behind the shell rear line.
#   - derived: back face x = -0.047 (shell rear) - 0.012 (T12) = -0.059,
#     center x = -0.059 + 0.019 = -0.040; floor z = 0.076 (shell bottom line),
#     center z = 0.076 + 0.035 = 0.111 (world) = -0.009 local; y centered.
#   - orientation VERTICAL (70 along z, 38 along x, 35 along y); inertia recomputed
#     for that box below. The earlier horizontal read came from mistaking the
#     control box for the battery in the CAD screenshot.
OFFICIAL_HEAD_BATTERY_MASS = 0.150  # kg, battery share stripped from jaw_soft


def adapt_spec_to_replica(spec: mujoco.MjSpec) -> mujoco.MjSpec:
    jaw = spec.body("jaw_soft")
    keep_ratio = (jaw.mass - OFFICIAL_HEAD_BATTERY_MASS) / jaw.mass
    jaw.fullinertia = [v * keep_ratio for v in jaw.fullinertia]
    jaw.mass = jaw.mass - OFFICIAL_HEAD_BATTERY_MASS

    battery = spec.body("trunk_base").add_body(
        name="replica_battery", pos=REPLICA_BATTERY_POS
    )
    battery.mass = REPLICA_BATTERY_MASS
    # VERTICAL pack 38(X) x 35(Y) x 70(Z): I = m/12*(y^2+z^2, x^2+z^2, x^2+y^2)
    battery.fullinertia = [2.58e-5, 2.66e-5, 1.12e-5, 0.0, 0.0, 0.0]

    # 2026-11-06: real collision box. The battery was welded in as mass-only
    # (no geoms), so no policy ever felt it — on the robot the feet drive into
    # it during combined hip/knee/ankle motions and jam. Default
    # contype/conaffinity (1/1) collides with class="collision" leg/foot geoms;
    # being jointless under trunk_base keeps torso self-contacts excluded.
    # mass=0 preserves the explicit body mass/inertia above.
    battery_geom = battery.add_geom(name="replica_battery_collision")
    battery_geom.type = mujoco.mjtGeom.mjGEOM_BOX
    battery_geom.size = [0.019, 0.0175, 0.035]  # half-extents: 38x35x70 mm (vertical)
    battery_geom.mass = 0.0
    battery_geom.rgba = [0.9, 0.45, 0.1, 0.35]
    battery_geom.group = 3

    rest = sum(
        b.mass for b in spec.bodies if b.name != "replica_battery"
    )
    scale = (REPLICA_TOTAL_MASS - REPLICA_BATTERY_MASS) / rest
    for b in spec.bodies:
        if b.name in ("replica_battery", "world") or b.mass == 0:
            continue
        if any(v != 0.0 for v in b.fullinertia):
            b.fullinertia = [v * scale for v in b.fullinertia]
        else:
            b.inertia = [v * scale for v in b.inertia]
        b.mass = b.mass * scale
    return spec


def get_standup_spec() -> mujoco.MjSpec:
    return mujoco.MjSpec.from_file(str(MICRODUCK_GROUNDCONTACT_XML))


def get_ground_pick_spec() -> mujoco.MjSpec:
    return mujoco.MjSpec.from_file(str(MICRODUCK_GROUNDCONTACT_XML))


def get_walk_rollers_spec() -> mujoco.MjSpec:
    # NOTE: was loading robot_groundcontact.xml (no wheels) — the roller env
    # silently ran on the wheel-less standup model.
    return mujoco.MjSpec.from_file(str(MICRODUCK_GROUNDCONTACT_ROLLERS_XML))


SERVO_MESH_NAME = "xl330"
SERVO_GEOM_SUFFIX = "_servo_collision"


def name_servo_collision_geoms(spec: mujoco.MjSpec) -> mujoco.MjSpec:
    """Name every collision geom that is an XL330 housing mesh.

    The onshape-to-robot export leaves collision geoms unnamed (only the two
    foot soles are named by the export config). The protective-fall tasks need a
    contact sensor on the servo housings specifically — a servo hitting the floor
    is the event that strips gears — so this names each housing collision geom
    ``<body>_<k>_servo_collision``. The ``_collision`` suffix puts them under
    FULL_COLLISION (condim 1) like the other named collision geoms.
    """
    counts: dict[str, int] = {}
    for geom in spec.geoms:
        if geom.meshname != SERVO_MESH_NAME or (geom.contype == 0 and geom.conaffinity == 0):
            continue
        body = geom.parent.name
        k = counts.get(body, 0)
        counts[body] = k + 1
        geom.name = f"{body}_{k}{SERVO_GEOM_SUFFIX}"
    return spec


def get_allcollisions_spec() -> mujoco.MjSpec:
    return name_servo_collision_geoms(mujoco.MjSpec.from_file(str(MICRODUCK_ALLCOLLISIONS_XML)))


def get_allcollisions_backlash_spec() -> mujoco.MjSpec:
    return name_servo_collision_geoms(mujoco.MjSpec.from_file(str(MICRODUCK_ALLCOLLISIONS_BACKLASH_XML)))


def get_ball_spec() -> mujoco.MjSpec:
    return mujoco.MjSpec.from_file(str(MICRODUCK_BALL_XML))


def get_backlash_spec() -> mujoco.MjSpec:
    return mujoco.MjSpec.from_file(str(MICRODUCK_GROUNDCONTACT_BACKLASH_XML))


def get_walk_backlash_spec() -> mujoco.MjSpec:
    return mujoco.MjSpec.from_file(str(MICRODUCK_WALK_BACKLASH_XML))


def get_rollers_backlash_spec() -> mujoco.MjSpec:
    return mujoco.MjSpec.from_file(str(MICRODUCK_GROUNDCONTACT_ROLLERS_BACKLASH_XML))


HOME_FRAME = EntityCfg.InitialStateCfg(
    joint_pos={
        # Lower body — STAND3 pose (2026-11-06, replica lean). STAND2 already
        # leaned the official robot forward once (hip 30°→26.24°, CoM ~5mm
        # ahead of the ankle) for its in-head battery. Our replica carries the
        # 202 g pack at the rear-bottom (-0.027, 0, -0.055): at STAND2 the CoM
        # falls behind the support polygon and the passive HOME hold topples
        # backward — confirmed in sim (rigid-joint tipping test) AND on the
        # robot (temp zero-shift test 2026-11-06: STAND2 falls back, this pose
        # stands unaided). Sim sweep boundary was ~21°; this is 19.2° with
        # margin. hip_pitch 26.24°→19.18°, ankle 25.95°→19.00°, knee 0.28°.
        r".*hip_yaw.*": 0.0,
        r".*left_hip_roll.*": -0.0873,
        r".*right_hip_roll.*": 0.0873,
        r".*left_hip_pitch.*": -0.3350,
        r".*right_hip_pitch.*": 0.3350,
        r".*left_knee.*": -0.0049,
        r".*right_knee.*": 0.0049,
        r".*left_ankle.*": 0.3315,
        r".*right_ankle.*": -0.3315,
        # Head
        r".*neck_pitch.*": 0.3491,
        r".*head_pitch.*": 0.3491,
        r".*head_yaw.*": 0.0,
        r".*head_roll.*": 0.0,
    },
    joint_vel={".*": 0.0},
)

FULL_COLLISION = CollisionCfg(
    geom_names_expr=[".*_collision"],
    condim={r"^(left|right)_foot_collision$": 3, ".*_collision": 1},
    priority={r"^(left|right)_foot_collision$": 1},
    friction={r"^(left|right)_foot_collision$": (1.0,)},
)

# -- Old actuator (XML position, MuJoCo built-in PD + friction) --
# actuators = DelayedActuatorCfg(
    # delay_min_lag=0,
    # delay_max_lag=3,
    # base_cfg=XmlPositionActuatorCfg(joint_names_expr=(r".*",)),
# )

# -- BAM M6 actuator (full voltage control + load-dependent friction) --
# Exclude passive_* joints (jaw linkage in the new model has no XML actuator).
# Voltage domain randomization (mirrors mjlab_microban):
#   - vin_range: per-env battery voltage sampled at startup (replaces fixed vin)
#   - vin_drop_gain_range: load-dependent voltage sag V_drop = gain * sum(|tau|)
#   - vin_min: hard floor on the effective voltage after sag
# Replica: HD1910 identified params. Two independent identifications exist:
#   - ftservo-bam (Feetech official, gitee): feetech_hd1910_7_4V, kp defaults 32
#   - kissqy/Microduck-kissqy (community, proven walking on real HD1910):
#     hd1910_kissqy + kp_fw=5.0 + STS3215-style rate-limited firmware model.
# Round 4 (2026-10-23) uses the kissqy set — their policy demonstrably walks on
# the real servo. vin_range matches their config (7.4–8.0, floor 7.0).
_BAM_ACTUATOR_KWARGS = dict(
    motor_name="hd1910_kissqy",
    model="m6",
    target_names_expr=(r"^(?!passive_).*",),
    kp_fw=5.0,
    vin_range=(7.4, 8.0),
    vin_drop_gain_range=(0.0, 0.2),
    vin_min=7.0,
    delay_min_lag=3,
    delay_max_lag=6,
)
actuators = FrictionDRBamActuatorCfg(**_BAM_ACTUATOR_KWARGS)

# Same BAM actuator, but the firmware position loop reads the encoder THROUGH
# the passive_<joint>_backlash hinges (the real encoder is on the output side
# of the gear play). Only for the backlash model; the target regex already
# excludes the passive_* backlash joints from actuation.
backlash_actuators = BacklashEncoderBamActuatorCfg(**_BAM_ACTUATOR_KWARGS)

# -- BAM M4 actuator
# actuators = DelayedActuatorCfg(
    # delay_min_lag=0,
    # delay_max_lag=3,
    # base_cfg=make_bam_m4_actuator_cfg(),
# )

# HOME frame for the backlash model. HOME_FRAME's unanchored patterns
# (e.g. r".*left_hip_roll.*") would also match passive_left_hip_roll_backlash
# and try to initialize it at -0.0873 rad — outside its ±1° range. Pattern
# matching is first-match-wins in declaration order, so the anchored backlash
# rule placed FIRST pins every backlash joint at 0 and the servo joints fall
# through to the normal HOME values.
BACKLASH_HOME_FRAME = EntityCfg.InitialStateCfg(
    joint_pos={r".*_backlash$": 0.0, **HOME_FRAME.joint_pos},
    joint_vel={".*": 0.0},
)

MICRODUCK_WALK_ROBOT_CFG = EntityCfg(
    spec_fn=get_walk_spec,
    init_state=HOME_FRAME,
    collisions=(FULL_COLLISION,),
    articulation=EntityArticulationInfoCfg(
        actuators=(actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

MICRODUCK_STANDUP_ROBOT_CFG = EntityCfg(
    spec_fn=get_standup_spec,
    init_state=HOME_FRAME,
    collisions=(FULL_COLLISION,),
    articulation=EntityArticulationInfoCfg(
        actuators=(actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

# True full-collision model (every part collides, servo housings named — see
# name_servo_collision_geoms). For tasks where the robot falls onto arbitrary
# parts and the reward must know WHICH part hit: VelStand / protective fall.
MICRODUCK_ALLCOLLISIONS_ROBOT_CFG = EntityCfg(
    spec_fn=get_allcollisions_spec,
    init_state=HOME_FRAME,
    collisions=(FULL_COLLISION,),
    articulation=EntityArticulationInfoCfg(
        actuators=(actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

MICRODUCK_GROUND_PICK_ROBOT_CFG = EntityCfg(
    spec_fn=get_ground_pick_spec,
    init_state=HOME_FRAME,
    collisions=(FULL_COLLISION,),
    articulation=EntityArticulationInfoCfg(
        actuators=(actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

# Backlash robots: base model + ±1° serial backlash hinge per servo.
# Encoder reads through the backlash (BacklashEncoderBamActuator feedback +
# joint_pos/vel_rel_backlash observations — see tasks/backlash.py).
# Groundcontact variant → VelStand/StandUp backlash tasks (mirrors
# MICRODUCK_STANDUP_ROBOT_CFG); walk variant → Velocity backlash
# tasks (mirrors MICRODUCK_WALK_ROBOT_CFG, keeps backlash-vs-base comparisons
# unconfounded by the collision model).
MICRODUCK_BACKLASH_ROBOT_CFG = EntityCfg(
    spec_fn=get_backlash_spec,
    init_state=BACKLASH_HOME_FRAME,
    collisions=(FULL_COLLISION,),
    articulation=EntityArticulationInfoCfg(
        actuators=(backlash_actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

MICRODUCK_WALK_BACKLASH_ROBOT_CFG = EntityCfg(
    spec_fn=get_walk_backlash_spec,
    init_state=BACKLASH_HOME_FRAME,
    collisions=(FULL_COLLISION,),
    articulation=EntityArticulationInfoCfg(
        actuators=(backlash_actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

# All-collisions backlash robot → VelStand backlash tasks (mirrors
# MICRODUCK_ALLCOLLISIONS_ROBOT_CFG).
MICRODUCK_ALLCOLLISIONS_BACKLASH_ROBOT_CFG = EntityCfg(
    spec_fn=get_allcollisions_backlash_spec,
    init_state=BACKLASH_HOME_FRAME,
    collisions=(FULL_COLLISION,),
    articulation=EntityArticulationInfoCfg(
        actuators=(backlash_actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

# Roller-skate backlash robot: wheels stay free (passive_*wheel untouched by
# add_backlash.py). collisions=() mirrors MICRODUCK_WALK_ROLLERS_ROBOT_CFG —
# roller wheel collision geoms have no explicit names; XML defaults apply.
MICRODUCK_ROLLERS_BACKLASH_ROBOT_CFG = EntityCfg(
    spec_fn=get_rollers_backlash_spec,
    init_state=BACKLASH_HOME_FRAME,
    collisions=(),
    articulation=EntityArticulationInfoCfg(
        actuators=(backlash_actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

# Free-floating, non-articulated ball prop for the BallKick task. Position is
# set each episode by the reset_ball_in_front_of_foot event; the init pos here
# only matters for the pristine pre-first-reset state.
MICRODUCK_BALL_CFG = EntityCfg(
    spec_fn=get_ball_spec,
    init_state=EntityCfg.InitialStateCfg(pos=(0.3, 0.0, 0.035)),
)

# Roller skate robot: the 4 passive wheel joints (passive_*wheel) have no XML
# actuators; the BAM cfg's target regex already excludes them, so the action
# space stays 14-dimensional. Uses the SAME canonical BAM actuator as every
# other variant (was a plain XmlActuatorCfg PD — an actuator-physics mismatch
# vs the rest of the family, and joint-friction DR was impossible).
MICRODUCK_WALK_ROLLERS_ROBOT_CFG = EntityCfg(
    spec_fn=get_walk_rollers_spec,
    init_state=HOME_FRAME,
    collisions=(),  # roller wheel collision geoms have no explicit names; XML defaults apply
    articulation=EntityArticulationInfoCfg(
        actuators=(actuators,),
        soft_joint_pos_limit_factor=0.9,
    ),
)

if __name__ == "__main__":
    import mujoco.viewer as viewer
    from mjlab.scene import Scene, SceneCfg
    from mjlab.terrains import TerrainImporterCfg

    SCENE_CFG = SceneCfg(
        terrain=TerrainImporterCfg(terrain_type="plane"),
        entities={"robot": MICRODUCK_WALK_ROBOT_CFG},
    )

    scene = Scene(SCENE_CFG, device="cuda:0")
    viewer.launch(scene.compile())
