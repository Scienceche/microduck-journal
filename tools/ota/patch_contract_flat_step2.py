#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Mjlab-StandUp-Flat-Backlash-MicroDuck 加进固件契约解析器的任务白名单(2026-11-26)。

为什么: run15@2000 是在 Flat-Backlash 起身任务上训练的。解析器只认白名单里的任务名, 这个名字
        不在其中, 所以按真实任务名打包的候选, 固件会拒绝加载("requires an adapted HD1910
        contract")。此前的做法只能是把包标成别的任务名, 那是不实的溯源。
依据: 训练仓库 tasks/__init__.py 的 _BACKLASH_TASKS 里, Flat 与 Rough 两个 StandUp-Backlash
        变体用同一个环境工厂、同一个 RL 配置、同一个机器人模型, 只差 rough=True;
        Contract::parse 里没有任何按地形分派的逻辑。Training Studio 的任务目录(official_spec.py)
        同样注册了这个任务名。
改动: 两处名单各加一行(backlash_observations 与 official_supported), 外加一段注释说明来由。
配套测试: flat_backlash_recovery_task_parses_like_the_rough_one_and_only_by_its_exact_name
        (step1 先加, 改之前应失败; 本步之后应通过)。
每处替换都 assert 命中, 写完读回复核。deployment.rs 是 LF。
"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\duck-control\src\deployment.rs")

src = P.read_bytes().decode("utf-8")
assert "\r" not in src, "deployment.rs 应为 LF"
orig = src


def replace_once(s, old, new, what):
    n = s.count(old)
    assert n == 1, f"{what}: 锚点应为 1 处, 实际 {n}"
    return s.replace(old, new)


# 前置: step1 的测试已经在
assert src.count("fn flat_backlash_recovery_task_parses_like_the_rough_one_and_only_by_its_exact_name()") == 1, "先跑 step1"
assert src.count('"Mjlab-StandUp-Flat-Backlash-MicroDuck"') == 2, "白名单看起来已经改过了(或 step1 没跑), 中止"

# 1) 注释: 为什么 + 日期 + 依据
src = replace_once(
    src,
    "        // Target-filter format and encoder-side backlash observations are independent.\n",
    "        // `Mjlab-StandUp-Flat-Backlash-MicroDuck` is listed beside its Rough twin (2026-11-26).\n"
    "        // The training source registers both from one factory, one RL config and one robot,\n"
    "        // differing only in `rough`, and nothing below reads the terrain: they take this same\n"
    "        // branch. Unlisted, a checkpoint trained on the Flat one could not be deployed under\n"
    "        // its own name.\n"
    "        //\n"
    "        // Target-filter format and encoder-side backlash observations are independent.\n",
    "注释",
)

# 2) backlash_observations 名单
src = replace_once(
    src,
    "                    | \"Mjlab-StandUp-Rough-Backlash-MicroDuck\"\n"
    "                    | \"Mjlab-VelStand-Rough-Backlash-MicroDuck\"\n"
    "                    | \"Mjlab-GroundPick-Flat-Backlash-MicroDuck\"\n",
    "                    | \"Mjlab-StandUp-Rough-Backlash-MicroDuck\"\n"
    "                    | \"Mjlab-StandUp-Flat-Backlash-MicroDuck\"\n"
    "                    | \"Mjlab-VelStand-Rough-Backlash-MicroDuck\"\n"
    "                    | \"Mjlab-GroundPick-Flat-Backlash-MicroDuck\"\n",
    "backlash_observations 名单",
)

# 3) official_supported 名单
src = replace_once(
    src,
    "                        | \"Mjlab-StandUp-Rough-Backlash-MicroDuck\"\n"
    "                        | \"Mjlab-VelStand-Rough-Backlash-MicroDuck\"\n"
    "                        | \"Mjlab-BallKick-Flat-MicroDuck\"\n",
    "                        | \"Mjlab-StandUp-Rough-Backlash-MicroDuck\"\n"
    "                        | \"Mjlab-StandUp-Flat-Backlash-MicroDuck\"\n"
    "                        | \"Mjlab-VelStand-Rough-Backlash-MicroDuck\"\n"
    "                        | \"Mjlab-BallKick-Flat-MicroDuck\"\n",
    "official_supported 名单",
)

assert src != orig
assert "\r" not in src
P.write_bytes(src.encode("utf-8"))

chk = P.read_bytes().decode("utf-8")
assert "\r" not in chk
# 测试里 2 处 + 解析器两处名单各 1 处 = 4
assert chk.count('"Mjlab-StandUp-Flat-Backlash-MicroDuck"') == 4, chk.count('"Mjlab-StandUp-Flat-Backlash-MicroDuck"')
# 两条名单行用"整行"比对。不用子串计数: 20 空格那条作为子串也出现在 24 空格那一行里, 会数出 2。
# (2026-11-26 实际执行时这里用的是子串计数 == 1, 文件已正确落盘但这条复核断言报错;
#  随后用 grep -n 按行号独立复核: 第 232 行与第 266 行各 1 处。)
lines = chk.split("\n")
assert lines.count('                    | "Mjlab-StandUp-Flat-Backlash-MicroDuck"') == 1
assert lines.count('                        | "Mjlab-StandUp-Flat-Backlash-MicroDuck"') == 1
assert "(2026-11-26)" in chk
print("step2 落盘 ✓ (白名单 +2 行, 注释 +1 段)  行数 %d -> %d" % (orig.count("\n"), chk.count("\n")))
