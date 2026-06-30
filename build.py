"""
OpsKit 构建脚本 — 子命令模式

用法：
    python build.py test                          # 跑单元测试
    python build.py build                         # 编译（auto: Nuitka → PyInstaller）
    python build.py build --backend pyinstaller   # 指定后端
    python build.py build --upx                   # 启用 UPX 压缩
    python build.py clean                         # 清理 _build/ + dist/
    python build.py all                           # test → build → manifest
    python build.py info                          # 打印版本 / 平台 / 可用后端

产物：
    dist/opskit-{os}-{arch}[.exe]
    dist/opskit-{os}-{arch}[.exe].sha256
    dist/manifest.json   ← 供外部工具（tools）读取的产物描述
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.resolve()
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "_build"
MANIFEST_FILE = DIST_DIR / "manifest.json"
VERSION_FILE = ROOT / "core" / "constants.py"


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _get_version() -> str:
    # CI 环境下优先使用 tag 作为版本号，确保产物文件名与 tag 一致
    for env_var in ("CI_COMMIT_TAG", "GITHUB_REF_NAME"):
        tag = os.environ.get(env_var, "").strip()
        if tag.startswith("v"):
            return tag.lstrip("v")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("constants", VERSION_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return str(mod.APP_VERSION)
    except Exception:
        return "0"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _platform_info() -> tuple[str, str]:
    sys_map = {"linux": "linux", "win32": "windows", "darwin": "darwin"}
    os_name = sys_map.get(sys.platform, sys.platform)
    arch = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64",
            "aarch64": "arm64", "i686": "x86"}.get(arch, arch)
    return os_name, arch


def _output_name() -> str:
    os_name, arch = _platform_info()
    ext = ".exe" if sys.platform == "win32" else ""
    return f"opskit-{os_name}-{arch}{ext}"


def _run(cmd: list[str], **kwargs) -> int:
    print(f"[build] $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, **kwargs)
    return result.returncode


def _find_tool(name: str) -> str | None:
    """在系统 PATH 和当前 Python 同目录（venv）中查找可执行工具"""
    found = shutil.which(name)
    if found:
        return found
    venv_bin = Path(sys.executable).parent
    for suffix in ("", ".exe", ".cmd"):
        candidate = venv_bin / (name + suffix)
        if candidate.exists():
            return str(candidate)
    return None


def _available_backends() -> list[str]:
    backends = []
    if _find_tool("nuitka") or _find_tool("nuitka3"):
        backends.append("nuitka")
    if _find_tool("pyinstaller"):
        backends.append("pyinstaller")
    return backends


# ─── 构建函数 ─────────────────────────────────────────────────────────────────

def build_nuitka(output_name: str, upx: bool, packages: list[str] | None = None) -> Path | None:
    """使用 Nuitka 编译单文件可执行程序"""
    nuitka = _find_tool("nuitka") or _find_tool("nuitka3")
    if not nuitka:
        print("[build] Nuitka not found, fallback to PyInstaller")
        return None

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # onefile 固定解压目录：默认每次启动都把整包解压到随机 /tmp/onefile_<pid>_xxx，
    # 低配机/慢盘上这步要数秒且每次重来。按版本号固定到用户缓存目录后，
    # 首次解压后续启动直接复用，跳过重复解压 —— 低配启动提速的关键。
    version = _get_version()
    tempdir_spec = "{CACHE_DIR}/" + f"opskit/v{version}"

    cmd = [
        sys.executable, "-m", "nuitka",
        "--onefile",
        f"--onefile-tempdir-spec={tempdir_spec}",
        "--python-flag=no_site",
        "--python-flag=no_docstrings",
        "--python-flag=-OO",
        "--remove-output",
        f"--output-dir={DIST_DIR}",
        f"--output-filename={output_name}",
        "--include-data-dir=core/themes=core/themes",
        "--include-data-dir=core/locale=core/locale",
        "--include-data-dir=core/mirrors=core/mirrors",
        "--include-package=rich._unicode_data",
        "--include-module=_registry",
    ]
    for pkg in (packages or []):
        cmd.append(f"--include-package={pkg}")
    cmd += [
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-unittest-mode=nofollow",
        "--assume-yes-for-downloads",
        "--no-deployment-flag=self-execution",
        "--no-deployment-flag=original-argv0",
    ]
    if sys.platform == "win32":
        cmd.append("--include-windows-runtime-dlls=no")

    if upx:
        if shutil.which("upx"):
            cmd.append("--enable-plugin=upx")
        else:
            print("[build] UPX not found, skipping compression")

    if sys.platform == "win32":
        cmd.append("--windows-console-mode=force")

    cmd.append("main.py")
    rc = _run(cmd, cwd=ROOT)
    if rc != 0:
        print(f"[build] Nuitka failed (exit {rc})")
        return None

    output = DIST_DIR / output_name
    return output if output.exists() else None


def build_pyinstaller(output_name: str, upx: bool) -> Path | None:
    """使用 PyInstaller 打包单文件可执行程序"""
    if not _find_tool("pyinstaller"):
        print("[build] PyInstaller not found. Install: pip install pyinstaller")
        return None

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    spec_out = BUILD_DIR / "opskit"
    spec_out.mkdir(parents=True, exist_ok=True)

    # Windows 路径分隔符用 ; Unix 用 :
    sep = ";" if sys.platform == "win32" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--clean",
        f"--name={output_name.replace('.exe', '')}",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
        f"--specpath={spec_out}",
        f"--add-data={ROOT / 'core' / 'themes'}{sep}core/themes",
        f"--add-data={ROOT / 'core' / 'locale'}{sep}core/locale",
        f"--add-data={ROOT / 'core' / 'mirrors'}{sep}core/mirrors",
        "--hidden-import=_registry",
        "--hidden-import=software",
        "--hidden-import=software.menu",
        "--hidden-import=software.commands",
        "--hidden-import=software.base",
        "--hidden-import=software.registry",
        "--hidden-import=software.recipes",
        "--hidden-import=software.recipes.xui",
        "--hidden-import=software.recipes.tailscale",
        "--hidden-import=xui",
        "--hidden-import=tailscale",
        "--hidden-import=monitor",
        "--hidden-import=monitor.menu",
        "--hidden-import=monitor.commands",
        "--hidden-import=network",
        "--hidden-import=network.menu",
        "--hidden-import=network.commands",
        "--exclude-module=tkinter",
        "--exclude-module=matplotlib",
        "--exclude-module=numpy",
        "--exclude-module=scipy",
        "--exclude-module=PIL",
        "--exclude-module=pandas",
        "--exclude-module=test",
        "--exclude-module=unittest",
        "--exclude-module=xmlrpc",
        "--exclude-module=pydoc",
        "--exclude-module=doctest",
        "--exclude-module=distutils",
        "--exclude-module=lib2to3",
        "--exclude-module=ensurepip",
        "--exclude-module=idlelib",
        "--exclude-module=turtle",
        "--exclude-module=turtledemo",
        "--exclude-module=sqlite3",
    ]

    if upx:
        upx_bin = shutil.which("upx")
        if upx_bin:
            cmd += ["--upx-dir", str(Path(upx_bin).parent)]
        else:
            print("[build] UPX not found, skipping compression")

    if sys.platform != "win32":
        cmd.append("--strip")

    cmd.append("main.py")
    rc = _run(cmd, cwd=ROOT)
    if rc != 0:
        print(f"[build] PyInstaller failed (exit {rc})")
        return None

    candidates = [
        DIST_DIR / output_name,
        DIST_DIR / (output_name.replace(".exe", "") + ".exe"),
        DIST_DIR / output_name.replace(".exe", ""),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def write_checksum(bin_path: Path) -> str:
    checksum = _sha256(bin_path)
    sha_path = bin_path.with_suffix(bin_path.suffix + ".sha256")
    sha_path.write_text(f"{checksum}  {bin_path.name}\n", encoding="utf-8")
    print(f"[build] SHA256: {checksum}")
    return checksum


def write_manifest(bin_path: Path, checksum: str, backend: str, elapsed: float) -> None:
    """写入 manifest.json 供外部工具（tools）读取"""
    os_name, arch = _platform_info()
    manifest = {
        "name": "opskit",
        "version": _get_version(),
        "platform": os_name,
        "arch": arch,
        "binary": bin_path.name,
        "sha256": checksum,
        "size": bin_path.stat().st_size,
        "built_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backend": backend,
        "elapsed_s": round(elapsed, 1),
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[build] manifest → {MANIFEST_FILE}")


def print_report(bin_path: Path, elapsed: float) -> None:
    size_mb = bin_path.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"[build] 构建完成")
    print(f"[build] 输出: {bin_path}")
    print(f"[build] 大小: {size_mb:.2f} MB")
    print(f"[build] 耗时: {elapsed:.1f}s")
    print(f"{'=' * 60}\n")


# ─── 子命令 ──────────────────────────────────────────────────────────────────

def cmd_test(args: argparse.Namespace) -> int:
    """运行单元测试"""
    print("[build] 运行测试...")
    rc = _run([sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"], cwd=ROOT)
    if rc != 0:
        print(f"[build] 测试失败 (exit {rc})")
    return rc


def cmd_clean(args: argparse.Namespace) -> int:
    """清理构建缓存和产物"""
    for d in [BUILD_DIR, DIST_DIR]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            print(f"[build] 已清理: {d}")
    return 0


_SKIP_PKG = frozenset({
    "core", "tests", "build", "dist", "__pycache__",
    ".git", ".windsurf", "venv", ".venv", "env",
})

_REGISTRY_FILE = ROOT / "_registry.py"


def write_registry() -> list[str]:
    """扫描插件包并生成 _registry.py（打包模式静态注册表），返回包名列表"""
    entries: list[tuple[str, str]] = []
    for path in sorted(ROOT.iterdir()):
        if not path.is_dir():
            continue
        name = path.name
        if name in _SKIP_PKG or name.startswith(".") or name.startswith("_"):
            continue
        if not (path / "__init__.py").exists():
            continue
        entries.append((name, name))
    lines = ["MODULE_LIST = ["]
    for key, pkg in entries:
        lines.append(f"    ({key!r}, {pkg!r}),")
    lines.append("]")
    _REGISTRY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pkg_list = [e[0] for e in entries]
    print(f"[build] _registry.py → {pkg_list}")
    return pkg_list


def cmd_build(args: argparse.Namespace) -> int:
    """编译打包"""
    version = _get_version()
    output_name = _output_name()
    os_name, arch = _platform_info()

    print(f"[build] OpsKit v{version}  →  {output_name}")
    print(f"[build] 平台: {os_name}-{arch}  后端: {args.backend}  UPX: {args.upx}")

    packages = write_registry()

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, ignore_errors=True)

    t0 = time.monotonic()
    bin_path: Path | None = None
    used_backend = args.backend

    if args.backend in ("nuitka", "auto"):
        bin_path = build_nuitka(output_name, args.upx, packages)
        if bin_path:
            used_backend = "nuitka"

    if bin_path is None and args.backend in ("pyinstaller", "auto"):
        bin_path = build_pyinstaller(output_name, args.upx)
        if bin_path:
            used_backend = "pyinstaller"

    if bin_path is None:
        print("[build] 构建失败：所有后端均未成功")
        return 1

    elapsed = time.monotonic() - t0
    checksum = write_checksum(bin_path)
    write_manifest(bin_path, checksum, used_backend, elapsed)
    print_report(bin_path, elapsed)
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    """test → build → manifest（完整流程）"""
    rc = cmd_test(args)
    if rc != 0:
        return rc
    return cmd_build(args)


def cmd_info(args: argparse.Namespace) -> int:
    """打印版本 / 平台 / 可用后端"""
    os_name, arch = _platform_info()
    backends = _available_backends()
    print(f"[info] 版本:     {_get_version()}")
    print(f"[info] 平台:     {os_name}-{arch}")
    print(f"[info] Python:   {sys.version.split()[0]}")
    print(f"[info] 可用后端: {', '.join(backends) if backends else '无（请安装 pyinstaller）'}")
    if MANIFEST_FILE.exists():
        m = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        print(f"[info] 上次构建: v{m['version']} / {m['binary']} / {m['built_at']}")
    return 0


# ─── 入口 ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpsKit 构建脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python build.py all                        # 完整流程\n"
               "  python build.py build --backend pyinstaller\n"
               "  python build.py clean\n"
               "  python build.py info\n",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("test", help="运行单元测试")
    sub.add_parser("clean", help="清理 _build/ + dist/")
    sub.add_parser("info", help="打印版本 / 平台 / 可用后端")

    for name, help_text in [("build", "编译打包"), ("all", "test → build（完整流程）")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--backend", choices=["nuitka", "pyinstaller", "auto"],
            default="auto", help="打包后端（默认 auto：Nuitka → PyInstaller 回退）",
        )
        p.add_argument("--upx", action="store_true", help="启用 UPX 压缩")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "test": cmd_test,
        "clean": cmd_clean,
        "build": cmd_build,
        "all": cmd_all,
        "info": cmd_info,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
