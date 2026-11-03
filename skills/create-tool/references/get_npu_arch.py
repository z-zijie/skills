#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取当前环境的 NPU（Ascend）架构。

数据源为 CANN 自带的 asys 工具（ascend_system_advisor）::

    asys info -r hardware

其中与架构相关的两个字段：
  - Chip Info：形如 "Ascend 950DT_9584 V120"（chip_type/chip_name/chip_ver）
  - Arch Info：ACL 架构编号（aclrtGetDeviceInfo 的 ACL_DEV_ATTR_NPU_ARCH），如 "3510"

默认输出 SOC 架构名——取 Chip Info 前两段去掉空格（如 Ascend950DT_9584），
与 torch_npu 的 device properties name 一致，可直接用于构建脚本。

前置条件：已加载 CANN 环境（本机：source /home/z00611808/env.sh）。
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

ASYS_REL_PATH = os.path.join("tools", "ascend_system_advisor", "asys", "asys")
# asys 按 SO 名加载驱动/CANN 库，未 source set_env.sh 时需要补这些目录
DRIVER_LIB_DIRS = [
    "/usr/local/Ascend/driver/lib64/driver",
    "/usr/local/Ascend/driver/lib64/common",
    "/usr/local/Ascend/driver/lib64",
]
INVALID_VALUES = {"", "-", "unknown", "not support", "n/a"}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
ROW_RE = re.compile(r"^\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$")


def fail(msg):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


def find_asys():
    """定位 asys：优先 PATH，其次各 CANN 安装根目录。"""
    asys = shutil.which("asys")
    if asys:
        return asys
    roots = [
        os.environ.get("ASCEND_HOME_PATH"),
        os.environ.get("CANN_INSTALL_ROOT"),
        "/usr/local/Ascend",
    ]
    roots += sorted(glob.glob(os.path.join(os.path.expanduser("~"), "Ascend", "*")))
    for root in roots:
        if not root:
            continue
        cand = os.path.join(root, ASYS_REL_PATH)
        if os.path.isfile(cand):
            return cand
    return None


def build_asys_env():
    """子进程环境：继承当前环境，仅把缺失的 CANN/驱动库目录补进 LD_LIBRARY_PATH。"""
    env = dict(os.environ)
    libs = []
    cann = os.environ.get("ASCEND_HOME_PATH")
    if cann:
        libs.append(os.path.join(cann, "lib64"))
    libs += [d for d in DRIVER_LIB_DIRS if os.path.isdir(d)]
    cur = env.get("LD_LIBRARY_PATH", "")
    cur_set = set(filter(None, cur.split(":")))
    missing = [d for d in libs if d not in cur_set]
    if missing:
        env["LD_LIBRARY_PATH"] = ":".join(missing + ([cur] if cur else []))
    return env


def parse_table(text):
    """解析 asys 输出的两列表格行 "| key | value |"，返回有序 dict。"""
    rows = {}
    for line in ANSI_RE.sub("", text).splitlines():
        m = ROW_RE.match(line)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if not key.startswith("Group of"):  # 跳过表头行
                rows[key] = val
    return rows


def arch_name(chip_info):
    """"Ascend 950DT_9584 V120" -> "Ascend950DT_9584"。"""
    parts = chip_info.split()
    return "".join(parts[:2]) if len(parts) >= 2 else chip_info


def main():
    parser = argparse.ArgumentParser(
        prog="get_npu_arch.py",
        description="获取当前环境的 NPU（Ascend）架构，数据来自 CANN 自带的 asys 工具。",
        epilog=(
            "示例:\n"
            "  python3 get_npu_arch.py                     输出架构名，如 Ascend950DT_9584\n"
            "  python3 get_npu_arch.py --json              以 JSON 输出全部字段\n"
            "  python3 get_npu_arch.py --field 'Arch Info' 输出 ACL 架构编号，如 3510\n"
            "\n"
            "前置条件: 已加载 CANN 环境（本机: source /home/z00611808/env.sh）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出全部解析字段（npu_arch / chip_info / arch_code / asys 原始表格）",
    )
    parser.add_argument(
        "--field",
        metavar="NAME",
        help="输出 asys 表格中指定字段的原始值，如 'Chip Info'、'Arch Info'、'AI Core Count'",
    )
    args = parser.parse_args()

    asys = find_asys()
    if not asys:
        fail("找不到 asys 工具。请先加载 CANN 环境（本机: source /home/z00611808/env.sh）。")

    try:
        proc = subprocess.run(
            [asys, "info", "-r", "hardware"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=build_asys_env(),
        )
    except subprocess.TimeoutExpired:
        fail("asys info -r hardware 执行超时（120s）。")
    except OSError as e:
        fail(f"无法执行 asys（{asys}）: {e}")

    if proc.returncode != 0:
        fail(f"asys 执行失败（rc={proc.returncode}）: {(proc.stderr or '').strip()[:500]}")

    rows = parse_table(proc.stdout)
    chip = rows.get("Chip Info", "")
    if chip.lower() in INVALID_VALUES:
        fail(
            "asys 输出中未取到有效的 'Chip Info' 字段（设备未就绪或输出格式变化），原始输出开头:\n"
            + "\n".join(proc.stdout.splitlines()[:8])
        )

    if args.field:
        val = rows.get(args.field)
        if val is None:  # 大小写不敏感兜底
            val = {k.lower(): v for k, v in rows.items()}.get(args.field.lower())
        if val is None:
            fail(f"未找到字段 '{args.field}'。可用字段: {', '.join(sorted(rows))}")
        print(val)
        return

    npu_arch = arch_name(chip)
    if args.json:
        print(
            json.dumps(
                {
                    "npu_arch": npu_arch,
                    "chip_info": chip,
                    "arch_code": rows.get("Arch Info"),
                    "raw": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(npu_arch)


if __name__ == "__main__":
    main()
