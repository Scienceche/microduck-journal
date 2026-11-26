#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影子模式第二轮修补(2026-11-26), 两个文件, 每处替换都 assert 命中, 写完读回复核。

背景: 第一轮接线只做到"编译通过 + 假推理器单测"。对照实验证明零回归, 但主循环里的影子
路径(启动加载/投帧/发布报告/两个 IPC)没有任何测试真正执行过。本轮:
  shadow.rs
    1. Frame.obs 从 [f32; OBS_LEN] 改成 Observation: 控制环里那句 as_slice().try_into().expect(...)
       变成纯赋值 —— 主循环的影子路径上不再有任何可能 panic 的代码(用户边界2)。
       Observation 本身就是定长 [f32; 61], 原先的 expect 不可达, 这里是把它从结构上去掉。
    2. 文档注释修正: "copies 244 bytes" 不准确(Frame 不止 244 字节); admissible_before_load
       开头三行注释重复了一遍(第一轮打补丁留下的)。
    3. 追加 5 个真 ONNX 单测(用仓库自带 policies/stand 等), 覆盖 ShadowPolicy::load 真实路径。
  main.rs
    4. 投帧处随 1 改成 obs: step.saw.observation。
    5. 声明 #[cfg(test)] mod shadow_loop_tests; (新文件, 真 control_loop 集成测试)。
换行符按文件原有风格保留: shadow.rs 是 LF, main.rs 是 CRLF。
"""
import sys
from pathlib import Path

ROOT = Path(r"C:\duck_work\kissqy-firmware\robotd\src")


def load(name):
    path = ROOT / name
    src = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in src
    if crlf:
        assert src.count("\n") == src.count("\r\n"), f"{name}: 换行符不是纯 CRLF, 中止"
    else:
        assert "\r" not in src, f"{name}: 混入了孤立的 CR, 中止"
    return path, src, crlf


def save(path, src, crlf):
    assert "\r" not in src, f"{path.name}: 替换文本里不该有 CR"
    path.write_bytes((src.replace("\n", "\r\n") if crlf else src).encode("utf-8"))


def replace_once(src, old, new, what):
    n = src.count(old)
    assert n == 1, f"{what}: 锚点应为 1 处, 实际 {n}"
    return src.replace(old, new)


def sub(src, old, new, what, expect):
    n = src.count(old)
    assert n == expect, f"{what}: 应为 {expect} 处, 实际 {n}"
    return src.replace(old, new)


# ---------------------------------------------------------------- shadow.rs (LF)
path, raw, crlf = load("shadow.rs")
assert not crlf, "shadow.rs 应为 LF"
orig = raw
s = raw

# 1a. 模块文档: 244 字节不准确
s = replace_once(
    s,
    "//! The control thread hands over an observation with [`ShadowHandle::submit`], which copies\n"
    "//! 244 bytes into a bounded queue and returns whether they were taken. A full queue drops the\n",
    "//! The control thread hands over an observation with [`ShadowHandle::submit`], which copies\n"
    "//! one `Frame` into a bounded queue and returns whether it was taken. A full queue drops the\n",
    "shadow.rs 模块文档",
)

# 1b. Frame.obs 改类型(该结构没有 PartialEq 的使用方: 全文件没有按值比较 Frame)
s = replace_once(
    s,
    "/// One observation handed from the control thread, with what the driving policy did with it.\n"
    "#[derive(Debug, Clone, Copy, PartialEq)]\n"
    "pub struct Frame {\n"
    "    pub obs: [f32; OBS_LEN],\n",
    "/// One observation handed from the control thread, with what the driving policy did with it.\n"
    "#[derive(Debug, Clone, Copy)]\n"
    "pub struct Frame {\n"
    "    /// The observation the driving policy acted on, as it was. `Observation` is fixed-width\n"
    "    /// by type, so handing it over is a copy — not a conversion, and so not something that\n"
    "    /// can fail on the control thread.\n"
    "    pub obs: Observation,\n",
    "shadow.rs Frame.obs",
)

# 1c. worker 里不再转换
s = replace_once(
    s,
    "            let result = candidate.infer(&frame.obs.into());\n",
    "            let result = candidate.infer(&frame.obs);\n",
    "shadow.rs worker infer",
)

# 1d. 测试辅助 frame()
s = replace_once(
    s,
    "            obs: [0.01; OBS_LEN],\n",
    "            obs: Observation::from([0.01; OBS_LEN]),\n",
    "shadow.rs 测试 frame()",
)

# 2. 重复的文档注释
s = replace_once(
    s,
    "/// Whether a candidate may be shadowed, before anything is loaded.\n"
    "///\n"
    "/// `Err(NotConfigured)` when there is nothing to shadow, which is not a failure.\n"
    "/// Whether a candidate may be shadowed, before anything is loaded.\n"
    "///\n"
    "/// `Err(NotConfigured)` when there is nothing to shadow, which is not a failure.\n"
    "/// `Err(ShadowsItself)` when the candidate is the live policy by path.\n",
    "/// Whether a candidate may be shadowed, before anything is loaded.\n"
    "///\n"
    "/// `Err(NotConfigured)` when there is nothing to shadow, which is not a failure.\n"
    "/// `Err(ShadowsItself)` when the candidate is the live policy by path.\n",
    "shadow.rs admissible_before_load 文档",
)

# 3. 追加真 ONNX 单测到 tests 模块末尾
REAL = '''
    // ---- The real candidate, through the real loader ----
    //
    // Everything above hands the worker a stand-in. These load an actual network the way `main`
    // does on startup, so the path a robot takes on its first tick with a shadow has been run
    // somewhere before then. They need ONNX Runtime like the rest of the suite's real-model
    // tests, and like those they fail rather than skip without it.

    /// A policy shipped with the repository. `stand` is the file the stand slot loads, which is
    /// what a candidate for that slot has to be loadable as.
    fn bundled(slot: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../policies")
            .join(slot)
            .join("policy.onnx")
    }

    /// Enough different observations to see two loaders agree more than once. Not robot states:
    /// the point is only that the inputs vary from one call to the next.
    fn varied_observations() -> Vec<Observation> {
        (0..8)
            .map(|k| {
                Observation::from(std::array::from_fn(|i| {
                    ((i * 7 + k * 13) % 23) as f32 / 11.0 - 1.0
                }))
            })
            .collect()
    }

    /// The loaded candidate is identified by the SHA-256 of its file, hashed straight from disk.
    /// The deployment gate matches that figure against a package, so it has to be the file's own
    /// digest and not one of anything derived from it.
    #[test]
    fn a_real_candidate_loads_and_is_identified_by_its_file_digest() {
        let path = bundled("stand");
        let mut candidate = ShadowPolicy::load(&path).unwrap();
        assert_eq!(
            candidate.loaded_model().sha256,
            duck_control::policy::file_sha256(&path).unwrap(),
            "the report's digest must be the SHA-256 of the file itself"
        );
        let action = candidate.infer(&Observation::zeroed()).unwrap();
        assert!(action.iter().all(|v| v.is_finite()), "{action:?}");
    }

    /// What the shadow records is what the stand slot would command. Both load the same file
    /// through the same `open`, so on the same observations they must answer identically — a
    /// sequence, not one frame, so that a recurrent candidate's state is seen to track too.
    #[test]
    fn a_real_candidate_infers_what_the_stand_slot_would() {
        use duck_control::policy::{Net, Policy, PolicyPaths};
        let path = bundled("stand");
        let mut slot = Policy::load(
            &PolicyPaths {
                walk: bundled("walk"),
                stand: Some(path.clone()),
                ..PolicyPaths::default()
            },
            0.05,
        )
        .unwrap();
        let mut candidate = ShadowPolicy::load(&path).unwrap();
        assert_eq!(
            slot.loaded_model(Net::Stand).unwrap().sha256,
            candidate.loaded_model().sha256
        );
        for (n, obs) in varied_observations().into_iter().enumerate() {
            let from_slot = slot.infer(&obs, Net::Stand).unwrap();
            let from_shadow = candidate.infer(&obs).unwrap();
            assert_eq!(from_slot, from_shadow, "observation {n}");
        }
    }

    /// End to end, the way `main` starts it: the configured path loads, the worker runs, and the
    /// report carries that file's digest and counts real inferences.
    #[test]
    fn load_configured_runs_a_real_candidate() {
        let path = bundled("stand");
        let digest = duck_control::policy::file_sha256(&path).unwrap();
        let mut shadow = load_configured(Some(&path), None).unwrap();
        assert_eq!(
            shadow.handle().loaded(),
            Some((path.as_path(), digest.as_str())),
            "the report must name the file that was configured"
        );
        assert!(
            feed_until(&shadow, 10, |_| false),
            "the real network was never run"
        );
        shadow.stop();
        let r = shadow.report();
        assert!(r.inferred >= 10, "{r:?}");
        assert_eq!(r.invalid, 0, "the shipped stand policy returns 14 finite actions: {r:?}");
        assert_eq!(r.sha256.as_deref(), Some(digest.as_str()));
    }

    /// The content check on real files. A copy of the live policy under another name is the live
    /// policy, and is refused once its digest is known; a file with other bytes is admitted.
    #[test]
    fn a_copy_of_the_live_policy_is_refused_by_its_digest() {
        let live = bundled("stand");
        let temp = tempfile::tempdir().unwrap();
        let copy = temp.path().join("candidate.onnx");
        std::fs::copy(&live, &copy).unwrap();
        assert!(
            matches!(
                load_configured(Some(&copy), Some(&live)),
                Err(NotStarted::ShadowsItself(p)) if p == copy
            ),
            "a copy of the live policy must not be shadowed"
        );
        assert!(
            load_configured(Some(&bundled("sitstand")), Some(&live)).is_ok(),
            "a different file is a legitimate candidate"
        );
    }

    /// A configured path that is not a loadable policy is `WouldNotLoad`. Nothing is left
    /// running, and the reason travels with the refusal.
    #[test]
    fn a_real_path_that_is_not_a_policy_does_not_load() {
        let not_a_model = bundled("stand").with_file_name("manifest.json");
        for path in [not_a_model, Path::new("/no/such/shadow/policy.onnx").to_path_buf()] {
            let reason = match load_configured(Some(&path), None) {
                Err(NotStarted::WouldNotLoad(reason)) => reason,
                Err(other) => panic!("{}: {other}", path.display()),
                Ok(_) => panic!("{} loaded as a policy", path.display()),
            };
            assert!(!reason.is_empty(), "{}", path.display());
        }
    }
}
'''
TAIL = "\n}\n"
assert s.endswith(TAIL), "shadow.rs 末尾不是测试模块的收尾括号"
assert s.count("\n    fn a_missing_live_digest_admits_the_candidate()") == 1, "找不到测试模块最后一个测试"
s = s[: -len(TAIL)] + REAL
assert s != orig
save(path, s, crlf)

chk = path.read_bytes().decode("utf-8")
assert "\r" not in chk
for probe in (
    "    pub obs: Observation,\n",
    "let result = candidate.infer(&frame.obs);\n",
    "obs: Observation::from([0.01; OBS_LEN]),\n",
    "fn a_real_candidate_loads_and_is_identified_by_its_file_digest()",
    "fn a_real_candidate_infers_what_the_stand_slot_would()",
    "fn load_configured_runs_a_real_candidate()",
    "fn a_copy_of_the_live_policy_is_refused_by_its_digest()",
    "fn a_real_path_that_is_not_a_policy_does_not_load()",
):
    assert probe in chk, f"shadow.rs 复核失败, 缺: {probe!r}"
assert "pub obs: [f32; OBS_LEN]" not in chk, "旧类型还在"
assert "&frame.obs.into()" not in chk
assert chk.count("copies\n//! 244 bytes") == 0
assert chk.count("/// Whether a candidate may be shadowed, before anything is loaded.\n") == 1, "重复注释没去干净"
assert chk.endswith("    }\n}\n"), "文件收尾不对"
print("shadow.rs 落盘 ✓  行数 %d -> %d" % (orig.count("\n"), chk.count("\n")))

# ---------------------------------------------------------------- main.rs (CRLF)
path, raw, crlf = load("main.rs")
assert crlf, "main.rs 应为 CRLF"
orig = raw
s = raw.replace("\r\n", "\n")

# 4. 投帧处
s = replace_once(
    s,
    "                        // `submit` copies 244 bytes into a bounded queue and returns; it never\n"
    "                        // waits, and with no shadow it is one relaxed load.\n"
    "                        shadow_handle.submit(shadow::Frame {\n"
    "                            obs: step.saw.observation.as_slice().try_into().expect(\n"
    "                                \"an Observation is OBS_LEN wide, which is what Frame::obs takes\",\n"
    "                            ),\n"
    "                            main_action: Some(step.saw.action),\n",
    "                        // `submit` copies one `Frame` into a bounded queue and returns; it never\n"
    "                        // waits and allocates nothing. The observation is handed over as the\n"
    "                        // fixed-width type it already is, so nothing here can fail, and with no\n"
    "                        // shadow it is one relaxed load.\n"
    "                        shadow_handle.submit(shadow::Frame {\n"
    "                            obs: step.saw.observation,\n"
    "                            main_action: Some(step.saw.action),\n",
    "main.rs 投帧处",
)

# 5. 声明集成测试模块
s = replace_once(
    s,
    "#[cfg(test)]\nmod feetech_recovery_tests;\n",
    "#[cfg(test)]\nmod feetech_recovery_tests;\n\n"
    "#[cfg(test)]\nmod shadow_loop_tests;\n",
    "main.rs 模块声明",
)
assert s != orig.replace("\r\n", "\n")
save(path, s, crlf)

chk = path.read_bytes()
assert chk.count(b"\r\n") == chk.count(b"\n"), "main.rs 写回后换行风格变了"
chk = chk.decode("utf-8").replace("\r\n", "\n")
assert "                            obs: step.saw.observation,\n" in chk, "main.rs 复核失败: 投帧处"
assert "try_into().expect(\n                                \"an Observation is OBS_LEN wide" not in chk
assert chk.count("mod shadow_loop_tests;") == 1
assert (ROOT / "shadow_loop_tests.rs").is_file(), "集成测试文件不存在"
print("main.rs 落盘 ✓  行数 %d -> %d" % (orig.count("\r\n"), chk.count("\n")))
