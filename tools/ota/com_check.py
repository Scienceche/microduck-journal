#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整机 CoM 准静态校验器(P2 规格实现, 2026-11-23)。

正运动学路线: MJCF 连杆质量/质心 + 15 舵机关节角 → mj_forward →
subtree_com(整机三维质心) → 水平投影; 双脚网格顶点 → 凸包支撑多边形;
判定投影点在包内 + 距边最小裕度, 阈值 1.5cm 告警。

用法:
  python com_check.py home                     # HOME 位自检(标定台 HOME 合同值)
  python com_check.py <采集.json> [--every N]  # 回放姿态采集, 输出裕度时间线
"""
import json
import sys
import tomllib
from pathlib import Path

import mujoco
import numpy as np

XML = Path(r"C:\duck_work\microduck_rl\src\mjlab_microduck\robot\microduck\robot_allcollisions.xml")
CAL = Path(r"/tmp/hd1910-calibration.toml")  # 回放模式在鸭子侧/拷贝后用
CAL_LOCAL = Path(r"C:\duck_work\ota\hd1910-calibration.toml")
SAFE_MARGIN = 0.015  # 1.5cm 安全裕度

HOME_RAD = {  # 部署合同 STAND3 HOME(与 robotd 部署包一致)
    "left_hip_yaw": 0.0, "left_hip_roll": -0.0873, "left_hip_pitch": -0.3350,
    "left_knee": -0.0049, "left_ankle": 0.3315,
    "neck_pitch": 0.3491, "head_pitch": 0.3491, "head_yaw": 0.0, "head_roll": 0.0,
    "right_hip_yaw": 0.0, "right_hip_roll": 0.0873, "right_hip_pitch": 0.3350,
    "right_knee": 0.0049, "right_ankle": -0.3315,
}


def hull_2d(pts):
    """monotone chain 凸包, pts: (N,2)。"""
    pts = sorted(map(tuple, pts))
    if len(pts) < 3:
        return np.array(pts)

    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def margin_to_hull(point, hull):
    """点 (2,) 到凸包: 在内→正裕度(到最近边距离); 在外→负距离。"""
    p = np.asarray(point, float)
    n = len(hull)
    # 在多边形内?
    inside = True
    for i in range(n):
        a, b = hull[i], hull[(i+1) % n]
        if (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0]) < 0:
            inside = False
            break
    d_min = 1e9
    for i in range(n):
        a, b = hull[i], hull[(i+1) % n]
        ab = b - a
        t = np.clip(np.dot(p-a, ab)/max(np.dot(ab, ab), 1e-12), 0, 1)
        d = np.linalg.norm(p - (a + t*ab))
        d_min = min(d_min, d)
    return d_min if inside else -d_min


class ComChecker:
    def __init__(self):
        self.m = mujoco.MjModel.from_xml_path(str(XML))
        self.d = mujoco.MjData(self.m)
        self.jname2q = {}
        for j in range(self.m.njnt):
            name = mujoco.mj_id2name(self.m, mujoco.mjtObj.mjOBJ_JOINT, j)
            if name and name != "trunk_base_freejoint":
                self.jname2q[name] = (self.m.jnt_qposadr[j],
                                      self.m.jnt_axis[j].copy())
        # 脚部网格顶点收集器: (geom id → 本地顶点)
        self.foot_geoms = []
        for b in range(self.m.nbody):
            bname = mujoco.mj_id2name(self.m, mujoco.mjtObj.mjOBJ_BODY, b) or ""
            if "ankle" not in bname:
                continue
            for g in range(self.m.body_geomadr[b],
                           self.m.body_geomadr[b] + self.m.body_geomnum[b]):
                if self.m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
                    self.foot_geoms.append(g)

    def _foot_points_world(self):
        pts = []
        for g in self.foot_geoms:
            mid = self.m.geom_dataid[g]
            va, vn = self.m.mesh_vertadr[mid], self.m.mesh_vertnum[mid]
            verts = self.m.mesh_vert[va:va+vn]
            sc = self.m.mesh_scale[mid] if hasattr(self.m, "mesh_scale") else np.ones(3)
            R = self.d.geom_xmat[g].reshape(3, 3)
            p = self.d.geom_xpos[g]
            pts.append((verts * sc) @ R.T + p)
        return np.vstack(pts)

    def _body_pos(self, bname):
        b = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_BODY, bname)
        return self.d.xpos[b].copy()

    def solve(self, joint_rad):
        """joint_rad: {关节名: 弧度} → (margin_m, com_xy, 脚min_z)。"""
        mujoco.mj_resetData(self.m, self.d)
        self.d.qpos[:] = self.m.qpos0
        for name, rad in joint_rad.items():
            if name not in self.jname2q:
                continue
            adr, _ = self.jname2q[name]
            self.d.qpos[adr] = rad
        mujoco.mj_forward(self.m, self.d)
        # 基座下沉至脚触地
        fp = self._foot_points_world()
        dz = fp[:, 2].min()
        self.d.qpos[2] -= dz
        mujoco.mj_forward(self.m, self.d)
        fp = self._foot_points_world()
        com = self.d.subtree_com[0].copy()  # 整机质心(世界系)
        hull = hull_2d(fp[:, :2])
        m = margin_to_hull(com[:2], hull)
        return m, com, hull


def load_cal():
    p = CAL_LOCAL if CAL_LOCAL.exists() else CAL
    with open(p, "rb") as f:
        return {j["name"]: (j["id"], j["zero_raw"], j["direction"])
                for j in tomllib.load(f)["joints"]}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "home"
    ck = ComChecker()
    if mode == "beak":
        # 头/喙负载偏移测试: neck×head 前探扫描 × 额定负载分级, 腿保持 HOME(最坏工况)
        print("喙部负载偏移测试 (腿=HOME 无补偿, 最坏静态)")
        print("负载: 0g / 50g / 100g 挂喙尖 | 行=neck_pitch, 列=head_pitch")
        pitches = [0.35, 0.6, 0.9, 1.2, 1.57]
        payloads = [0.0, 0.05, 0.10]
        m_robot = sum(ck.m.body_mass)
        # 先确定前探方向: head_pitch 增大是否喙尖前移(+x)
        for name in ("neck_pitch", "head_pitch"):
            jr = dict(HOME_RAD)
            m0, com0, _ = ck.solve(jr)
            beak0 = ck._body_pos("jaw_soft")
            jr[name] = jr[name] + 0.3
            mujoco.mj_forward(ck.m, ck.d)
            beak1 = ck._body_pos("jaw_soft")
            print("  %s +0.3 → 喙尖 Δx=%+.1f cm" % (name, (beak1[0]-beak0[0])*100))
        print()
        for pay in payloads:
            print("== 负载 %dg ==" % (pay*1000))
            for hp in pitches:
                row = []
                for np_ in pitches:
                    jr = dict(HOME_RAD)
                    jr["head_pitch"] = hp
                    jr["neck_pitch"] = np_
                    m, com, hull = ck.solve(jr)
                    beak = ck._body_pos("jaw_soft")
                    if pay > 0:
                        com = (m_robot*com + pay*beak) / (m_robot + pay)
                        mm = margin_to_hull(com[:2], hull)
                    else:
                        mm = m
                    row.append("%+5.1f%s" % (mm*100, "!" if mm < SAFE_MARGIN else ""))
                print("  head=%4.2f | %s" % (hp, "  ".join(row)))
            print("  (单元格=裕度cm, !=<1.5cm 越限, 列=neck 0.35→1.57)")
        return
    if mode == "curve":
        # 临界前探角-负载曲线: head=0.35(危险行), 二分 neck_pitch 求
        # margin=1.5cm(安全线)与 margin=0(静态翻倒线)的临界角。负载 0-150g。
        ck2 = ck  # noqa
        m_robot = sum(ck.m.body_mass)

        def beak_tip_x(jr):
            ck.solve(jr)
            jb = mujoco.mj_name2id(ck.m, mujoco.mjtObj.mjOBJ_BODY, "jaw_soft")
            tb = mujoco.mj_name2id(ck.m, mujoco.mjtObj.mjOBJ_BODY, "trunk_base")
            pts = []
            for g in range(ck.m.body_geomadr[jb],
                           ck.m.body_geomadr[jb] + ck.m.body_geomnum[jb]):
                if ck.m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
                    mid = ck.m.geom_dataid[g]
                    va, vn = ck.m.mesh_vertadr[mid], ck.m.mesh_vertnum[mid]
                    v = ck.m.mesh_vert[va:va+vn]
                    R = ck.d.geom_xmat[g].reshape(3, 3)
                    pts.append(v @ R.T + ck.d.geom_xpos[g])
            P = np.vstack(pts)
            # 真喙尖 = 沿"躯干→喙体"水平方向最远的顶点(方向无关, 修复±x歧义)
            u = ck.d.xpos[jb] - ck.d.xpos[tb]
            u[1] = 0
            u = u / max(np.linalg.norm(u), 1e-9)
            return P[np.argmax(P @ u)]

        def margin_at(neck, pay):
            jr = dict(HOME_RAD)
            jr["head_pitch"] = 0.35
            jr["neck_pitch"] = neck
            m0, com, hull = ck.solve(jr)
            tip = beak_tip_x(jr)
            if pay > 0:
                com = (m_robot*com + pay*tip) / (m_robot + pay)
                m0 = margin_to_hull(com[:2], hull)
            return m0

        def crit(pay, target):
            lo, hi = 0.35, 1.57
            if margin_at(lo, pay) < target:
                return lo  # 起点已越线
            if margin_at(hi, pay) >= target:
                return None  # 全程安全
            for _ in range(18):
                mid = (lo+hi)/2
                if margin_at(mid, pay) >= target:
                    lo = mid
                else:
                    hi = mid
            return (lo+hi)/2

        print("临界前探角(neck_pitch @ head=0.35, 腿=HOME 无补偿)")
        print("%6s | %10s | %8s | %8s" % ("负载", "喙尖x(cm)", "安全线1.5cm", "翻倒线0"))
        rows = []
        for pay_g in range(0, 155, 10):
            pay = pay_g/1000
            jr = dict(HOME_RAD); jr["head_pitch"] = 0.35; jr["neck_pitch"] = 0.35
            tip = beak_tip_x(jr)
            c_safe = crit(pay, SAFE_MARGIN)
            c_zero = crit(pay, 0.0)
            print("%4dg  | %8.1f  | %9s | %8s" % (
                pay_g, tip[0]*100,
                ("%.2f rad (%.0f°)" % (c_safe, np.degrees(c_safe))) if c_safe else "全程安全",
                ("%.2f rad" % c_zero) if c_zero else "不翻"))
            rows.append((pay_g, c_safe, c_zero))
        import csv
        with open(r"C:\duck_work\ota\beak_envelope.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["payload_g", "neck_safe_rad", "neck_tipover_rad"])
            for r in rows:
                w.writerow([r[0], r[1] if r[1] is not None else "", r[2] if r[2] is not None else ""])
        print("CSV: C:\\duck_work\\ota\\beak_envelope.csv")
        return
    if mode == "home":
        m, com, hull = ck.solve(HOME_RAD)
        print("HOME 位自检")
        print("  整机 CoM: x=%+.3f y=%+.3f z=%.3f (m)" % tuple(com))
        print("  支撑凸包顶点数: %d" % len(hull))
        print("  投影裕度: %+.1f cm %s" % (m*100,
              "✓ 安全" if m >= SAFE_MARGIN else "⚠️ 低于 1.5cm 裕度!"))
        return
    # 采集回放
    every = 25
    if "--every" in sys.argv:
        every = int(sys.argv[sys.argv.index("--every")+1])
    doc = json.load(open(mode, encoding="utf-8"))
    S = doc["samples"]
    cal = load_cal()
    margins = []
    print("回放 %s: %d 帧" % (Path(mode).name, len(S)))
    for i, s in enumerate(S):
        jr = {}
        for name, (sid, zero, dirn) in cal.items():
            raw = s["joints"].get(str(sid))
            if raw is not None:
                jr[name] = dirn * (raw - zero) * 2*np.pi/4096
        m, com, _ = ck.solve(jr)
        margins.append(m)
        if i % every == 0:
            print("  t=%6.2fs 裕度 %+6.1f cm %s" % (
                s["t"], m*100, "⚠️" if m < SAFE_MARGIN else ""))
    ma = np.array(margins)
    neg = (ma < 0).sum()
    print("统计: 最小 %+0.1f / 中位 %+0.1f / 最大 %+0.1f cm | 越界帧 %d/%d" % (
        ma.min()*100, np.median(ma)*100, ma.max()*100, neg, len(ma)))


if __name__ == "__main__":
    main()
