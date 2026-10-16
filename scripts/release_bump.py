"""发布版本同步脚本（CI 在 tag 构建前调用）。

把 tag（如 v7）解析成整数 build，写回两处单一事实源，避免版本漂移：
  1. core/constants.py  APP_VERSION = N      ← 决定运行二进制自报版本
  2. bootstrap.json      latest_build / display / min_build / rollout / notes

用法：
    python scripts/release_bump.py v7
    python scripts/release_bump.py 7 --min-build 5 --rollout 20 --notes "灰度修复"
    python scripts/release_bump.py v7 --check   # 仅校验一致性，不写入

退出码：0 成功 / 2 参数错误 / 3 一致性校验失败（--check）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Windows CI 控制台默认非 UTF-8（cp1252/gbk），打印 → 或中文会 UnicodeEncodeError。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
CONSTANTS = ROOT / "core" / "constants.py"
BOOTSTRAP = ROOT / "bootstrap.json"

_VERSION_RE = re.compile(r"^(APP_VERSION\s*=\s*)(\d+)\s*$", re.MULTILINE)


def parse_build(raw: str) -> int:
    """v7 / 7 → 7；非法格式抛 ValueError。"""
    m = re.fullmatch(r"v?(\d+)", raw.strip())
    if not m:
        raise ValueError(f"无法解析为整数 build：{raw!r}（应形如 v7 或 7）")
    return int(m.group(1))


def read_app_version(constants: Path | None = None) -> int:
    constants = CONSTANTS if constants is None else constants
    m = _VERSION_RE.search(constants.read_text(encoding="utf-8"))
    if not m:
        raise RuntimeError(f"未在 {constants} 找到 APP_VERSION 赋值")
    return int(m.group(2))


def write_app_version(build: int, constants: Path | None = None) -> None:
    constants = CONSTANTS if constants is None else constants
    text = constants.read_text(encoding="utf-8")
    new_text, n = _VERSION_RE.subn(rf"\g<1>{build}", text)
    if n != 1:
        raise RuntimeError(f"APP_VERSION 替换命中 {n} 次（应为 1）")
    constants.write_text(new_text, encoding="utf-8")


def update_bootstrap(build: int, min_build: int | None,
                     rollout: int | None, notes: str | None,
                     bootstrap: Path | None = None) -> dict:
    bootstrap = BOOTSTRAP if bootstrap is None else bootstrap
    data = json.loads(bootstrap.read_text(encoding="utf-8"))
    data["latest_build"] = build
    data["display"] = f"v{build}"
    if min_build is not None:
        data["min_build"] = min_build
    if rollout is not None:
        data["rollout"] = rollout
    if notes is not None:
        data["notes"] = notes
    bootstrap.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    return data


def check_consistency(build: int, constants: Path | None = None,
                      bootstrap: Path | None = None) -> list[str]:
    errs: list[str] = []
    if read_app_version(constants) != build:
        errs.append(f"APP_VERSION={read_app_version(constants)} 与 tag build={build} 不一致")
    bootstrap = BOOTSTRAP if bootstrap is None else bootstrap
    data = json.loads(bootstrap.read_text(encoding="utf-8"))
    if data.get("latest_build") != build:
        errs.append(f"bootstrap.latest_build={data.get('latest_build')} 与 build={build} 不一致")
    if data.get("min_build", 0) > build:
        errs.append(f"min_build={data.get('min_build')} 大于 latest_build={build}")
    rollout = data.get("rollout", 0)
    if not (0 <= rollout <= 100):
        errs.append(f"rollout={rollout} 超出 0-100")
    return errs


def preflight(build: int, constants: Path, bootstrap: Path,
              min_build: int | None, rollout: int | None) -> None:
    """Validate both version sources and the proposed manifest before mutation."""
    data = json.loads(bootstrap.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("bootstrap must be an object")
    latest = data.get("latest_build")
    if type(latest) is not int or latest < 0:
        raise ValueError("latest_build must be a nonnegative integer")
    if build <= 0:
        raise ValueError("release build must be positive")
    if build < max(read_app_version(constants), latest):
        raise ValueError("refusing release downgrade")
    minimum = data.get("min_build", 0) if min_build is None else min_build
    percentage = data.get("rollout", 0) if rollout is None else rollout
    if type(minimum) is not int or not 0 <= minimum <= build:
        raise ValueError("min_build must be an integer within 0..build")
    if type(percentage) is not int or not 0 <= percentage <= 100:
        raise ValueError("rollout must be an integer within 0..100")


def main() -> int:
    ap = argparse.ArgumentParser(description="发布版本同步")
    ap.add_argument("tag", help="tag 或整数 build，如 v7 / 7")
    ap.add_argument("--min-build", type=int, default=None, help="强制升级下限")
    ap.add_argument("--rollout", type=int, default=None, help="灰度放量 0-100")
    ap.add_argument("--notes", default=None, help="更新说明")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="仅校验一致性，不写入")
    mode.add_argument("--preflight", action="store_true", help="只读验证待发布版本及配置，拒绝降级")
    ap.add_argument("--root", type=Path, help="待更新的独立检出目录，默认脚本所属仓库")
    args = ap.parse_args()

    constants, bootstrap = CONSTANTS, BOOTSTRAP
    if args.root is not None:
        root = args.root.resolve(strict=True)
        targets = (root / "core/constants.py", root / "bootstrap.json")
        for target in targets:
            if (target.resolve(strict=True) != target or not target.is_file()
                    or target.stat().st_nlink != 1):
                raise ValueError(f"release target must be an unlinked regular file: {target}")
        constants, bootstrap = targets

    try:
        build = parse_build(args.tag)
    except ValueError as e:
        print(f"[bump] 参数错误：{e}", file=sys.stderr)
        return 2

    if args.rollout is not None and not (0 <= args.rollout <= 100):
        print(f"[bump] 参数错误：rollout={args.rollout} 须在 0-100", file=sys.stderr)
        return 2

    if args.check:
        errs = check_consistency(build, constants, bootstrap)
        if errs:
            for e in errs:
                print(f"[bump] 校验失败：{e}", file=sys.stderr)
            return 3
        print(f"[bump] 一致性校验通过 build={build}")
        return 0

    try:
        preflight(build, constants, bootstrap, args.min_build, args.rollout)
    except (ValueError, RuntimeError) as exc:
        print(f"[bump] preflight failed: {exc}", file=sys.stderr)
        return 2
    if args.preflight:
        print(f"[bump] preflight passed build={build}")
        return 0

    write_app_version(build, constants)
    data = update_bootstrap(build, args.min_build, args.rollout, args.notes, bootstrap)
    print(f"[bump] APP_VERSION → {build}")
    print(f"[bump] bootstrap → latest_build={data['latest_build']} "
          f"min_build={data['min_build']} rollout={data['rollout']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
