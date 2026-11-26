#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 duck-control/src/deployment.rs 加一个测试: Flat-Backlash 起身任务能像 Rough-Backlash 一样被解析。

分两步做(先红后绿):
  patch_contract_flat_step1.py  只加测试。在改白名单之前跑, 应当失败 —— 证明"按真实任务名打包的
                                Flat-Backlash 检查点, 现固件加载不了"这件事是真的。
  patch_contract_flat_step2.py  再把任务名加进白名单。之后同一个测试通过。

用法: python patch_contract_flat_step1.py
每处替换都 assert 命中, 写完读回复核。deployment.rs 是 LF。
"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\duck-control\src\deployment.rs")

src = P.read_bytes().decode("utf-8")
assert "\r" not in src, "deployment.rs 应为 LF"
orig = src

TEST = '''    /// The Flat-Backlash recovery task is the Rough-Backlash one on other ground. The training
    /// source registers both from one factory, one RL config and one robot, differing only in
    /// `rough`, and nothing in this parser looks at terrain — so a contract for the Flat one
    /// parses to the same `Contract` as the Rough one. It did not until its name was listed:
    /// an honest package for a Flat-Backlash checkpoint was refused as "requires an adapted
    /// HD1910 contract", and such a checkpoint could only go on a robot under another task's
    /// name.
    #[test]
    fn flat_backlash_recovery_task_parses_like_the_rough_one_and_only_by_its_exact_name() {
        let v: Value = serde_json::from_str(include_str!(
            "../../policies/stand/deployment-contract.json"
        ))
        .unwrap();
        let rough = Contract::parse(&v).unwrap();
        let mut flat = v.clone();
        flat["task"] = serde_json::json!("Mjlab-StandUp-Flat-Backlash-MicroDuck");
        let parsed = Contract::parse(&flat)
            .expect("the Flat-Backlash recovery task is an adapted HD1910 contract");
        assert_eq!(parsed.task, "Mjlab-StandUp-Flat-Backlash-MicroDuck");
        assert_eq!(parsed.slot, "stand");
        assert_eq!(parsed.home, rough.home);
        assert_eq!(parsed.training_action_scale, rough.training_action_scale);
        assert_eq!(parsed.ema_old_weight, rough.ema_old_weight);
        assert_eq!(parsed.firmware_p, rough.firmware_p);
        assert!(parsed.target_filter_passthrough);
        // Still a list of known tasks, not a prefix match.
        for unknown in [
            "Mjlab-StandUp-Flat-Backlash-MicroDuckX",
            "Mjlab-StandUp-Gravel-Backlash-MicroDuck",
        ] {
            let mut other = v.clone();
            other["task"] = serde_json::json!(unknown);
            assert!(Contract::parse(&other).is_err(), "{unknown} was accepted");
        }
    }
'''

ANCHOR = (
    "        assert!(Contract::parse(&bad_filter).is_err());\n"
    "    }\n"
    "    #[test]\n"
    "    fn runtime_curriculum_restores_full_trained_head_commands_without_changing_home() {\n"
)
n = src.count(ANCHOR)
assert n == 1, f"插入锚点应为 1 处, 实际 {n}"
src = src.replace(
    ANCHOR,
    "        assert!(Contract::parse(&bad_filter).is_err());\n"
    "    }\n"
    + TEST
    + "    #[test]\n"
    "    fn runtime_curriculum_restores_full_trained_head_commands_without_changing_home() {\n",
)
assert src != orig
assert "\r" not in src
assert "Mjlab-StandUp-Flat-Backlash-MicroDuck" in src
P.write_bytes(src.encode("utf-8"))

chk = P.read_bytes().decode("utf-8")
assert "\r" not in chk
assert chk.count("fn flat_backlash_recovery_task_parses_like_the_rough_one_and_only_by_its_exact_name()") == 1
# 任务名在本步只出现在测试里(2 处: 赋值与断言; "…MicroDuckX" 不带右引号匹配, 不计), 不在解析器的任务列表里
assert chk.count('"Mjlab-StandUp-Flat-Backlash-MicroDuck"') == 2, chk.count('"Mjlab-StandUp-Flat-Backlash-MicroDuck"')
assert chk.count('| "Mjlab-StandUp-Flat-Backlash-MicroDuck"') == 0, "解析器名单被动过了"
print("step1 落盘 ✓ (只加了测试)  行数 %d -> %d" % (orig.count("\n"), chk.count("\n")))
