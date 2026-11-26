#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修影子线程降优先级的链接错误 + 未用导入警告(2026-11-26)。

现象(第一次真正链接 aarch64 release 才暴露, 之前的"零错误"是类型检查):
  ld.lld: error: undefined symbol: gettid
      >>> referenced by robotd::shadow::worker::<ShadowPolicy>
原因: libc::gettid() 是 glibc 2.30 才有的封装函数, 交叉链接的 libc 没有它。标准库自己也
      引用 gettid, 但那是弱引用(找不到就用 syscall 兜底), 我们这里是强引用 → 链接失败。
修法: setpriority(PRIO_PROCESS, who=0, NICE)。Linux 上 nice 是线程属性, 内核对 who=0 的
      解释是"调用者所在线程"(probe_setpriority_who0.py 实测), 不需要先取线程号。
顺带: Frame.obs 改成 Observation 后, 顶层的 OBS_LEN 导入在非测试构建里没人用了(警告),
      从顶层导入里去掉, 测试模块里自己导入。
每处替换都 assert 命中, 写完读回复核。shadow.rs 是 LF。
"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\shadow.rs")

src = P.read_bytes().decode("utf-8")
assert "\r" not in src, "shadow.rs 应为 LF"
orig = src


def replace_once(s, old, new, what):
    n = s.count(old)
    assert n == 1, f"{what}: 锚点应为 1 处, 实际 {n}"
    return s.replace(old, new)


# 1. 降优先级: 不再调用 gettid
src = replace_once(
    src,
    "fn lower_own_priority() -> Result<(), String> {\n"
    "    // SAFETY: gettid and setpriority are plain syscalls taking and returning integers.\n"
    "    unsafe {\n"
    "        let tid = libc::gettid();\n"
    "        // PRIO_PROCESS: `who` is a thread id on Linux, so this touches this thread only.\n"
    "        if libc::setpriority(libc::PRIO_PROCESS, tid as libc::id_t, NICE) != 0 {\n",
    "fn lower_own_priority() -> Result<(), String> {\n"
    "    // SAFETY: `setpriority` is a plain syscall taking and returning integers.\n"
    "    //\n"
    "    // `PRIO_PROCESS` with `who` = 0 is the calling thread. On Linux the nice value is a\n"
    "    // per-thread attribute and the kernel takes 0 to mean whoever is calling, so this\n"
    "    // lowers the shadow thread and nothing else — not the control thread, not the process.\n"
    "    //\n"
    "    // No `gettid()` here, on purpose. It is a glibc 2.30 wrapper, and the libc this daemon\n"
    "    // links against is older than that: `std` gets away with naming it because its\n"
    "    // reference is weak, where a strong one — which this was — fails the link.\n"
    "    unsafe {\n"
    "        if libc::setpriority(libc::PRIO_PROCESS, 0, NICE) != 0 {\n",
    "lower_own_priority",
)

# 2. 未用导入
src = replace_once(
    src,
    "use duck_control::obs::{ACTION_LEN, OBS_LEN, Observation};\n",
    "use duck_control::obs::{ACTION_LEN, Observation};\n",
    "顶层导入",
)
src = replace_once(
    src,
    "mod tests {\n"
    "    use super::*;\n"
    "    use std::sync::atomic::AtomicUsize;\n",
    "mod tests {\n"
    "    use super::*;\n"
    "    use duck_control::obs::OBS_LEN;\n"
    "    use std::sync::atomic::AtomicUsize;\n",
    "测试模块导入",
)

assert src != orig
assert "\r" not in src

# 3. 回归测试: 影子线程确实降了优先级, 且只有它被降(直接读 /proc, 不靠文档)
TEST = '''    /// The nice value of this process's thread named `name`, read from `/proc`.
    ///
    /// Linux only, like the daemon. `/proc/<pid>/task/<tid>/stat` is `pid (comm) state …`: cut
    /// at the last `)`, and `nice` is the 17th field after it.
    fn nice_by_name(name: &str) -> Option<i32> {
        std::fs::read_dir("/proc/self/task")
            .ok()?
            .flatten()
            .filter_map(|entry| std::fs::read_to_string(entry.path().join("stat")).ok())
            .find_map(|stat| {
                let (head, tail) = stat.rsplit_once(')')?;
                let comm = head.rsplit_once('(')?.1;
                if comm != name {
                    return None;
                }
                tail.split_whitespace().nth(16)?.parse().ok()
            })
    }

    /// **The shadow runs at a lower priority, and nothing else is lowered with it.** The nice
    /// value is a per-thread attribute on Linux and `who` = 0 is the calling thread, which is
    /// what the worker passes. Read back from `/proc`, on the shadow thread and on this one,
    /// rather than taken on trust: this is the whole of "an independent, low-priority thread".
    #[test]
    fn the_shadow_thread_runs_niced_and_nothing_else_does() {
        let mine = std::thread::current().name().unwrap_or("main").to_owned();
        let before = nice_by_name(&mine);
        let (shadow, _) = start_with(ok([0.0; ACTION_LEN]));
        // The worker lowers itself as its first act; give it a moment to get there.
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if nice_by_name("shadow-policy").is_some_and(|nice| nice >= NICE) {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "the shadow thread never ran at nice {NICE}: {:?}",
                nice_by_name("shadow-policy")
            );
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(
            nice_by_name(&mine),
            before,
            "lowering the shadow must not have lowered the thread that started it"
        );
        drop(shadow);
    }

    // ---- Which candidates may be shadowed ----
'''
src = replace_once(
    src,
    "    // ---- Which candidates may be shadowed ----\n",
    TEST,
    "优先级测试插入点",
)

assert src != orig
assert "\r" not in src
P.write_bytes(src.encode("utf-8"))

chk = P.read_bytes().decode("utf-8")
assert "\r" not in chk
for probe in (
    "        if libc::setpriority(libc::PRIO_PROCESS, 0, NICE) != 0 {\n",
    "use duck_control::obs::{ACTION_LEN, Observation};\n",
    "    use duck_control::obs::OBS_LEN;\n",
    "fn nice_by_name(name: &str) -> Option<i32> {",
    "fn the_shadow_thread_runs_niced_and_nothing_else_does()",
):
    assert probe in chk, f"复核失败, 缺: {probe!r}"
assert "libc::gettid" not in chk, "还在调用 gettid"
# gettid 只应出现在那一句注释里
assert chk.count("gettid") == 1, "gettid 出现次数应为 1(仅注释), 实际 %d" % chk.count("gettid")
print("shadow.rs 落盘 ✓  行数 %d -> %d" % (orig.count("\n"), chk.count("\n")))
