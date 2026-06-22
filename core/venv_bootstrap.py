"""venv 自举 — 首次运行时自动创建 .venv 并安装依赖，然后 exec 重入"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def ensure_venv(project_root: Path | None = None) -> None:
    """
    确保当前进程运行在项目 .venv 内。

    - 已在 .venv 内 → 直接返回
    - .venv 存在但未在其中 → exec 重入
    - .venv 不存在 → 创建 venv → 安装 requirements → exec 重入

    注意：此函数在 Rich/i18n 初始化之前调用，只用 print() 输出。
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent.resolve()

    venv_dir = project_root / ".venv"
    venv_python = venv_dir / "bin" / "python"

    # ── 已在 .venv 内，直接返回 ──────────────────────────────────────────────
    # 方法 1：VIRTUAL_ENV 环境变量（最可靠）
    if os.environ.get("VIRTUAL_ENV"):
        return

    # 方法 2：sys.prefix 指向 venv 目录
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            return
    except Exception:
        pass

    # 方法 3：sys.executable 路径前缀匹配（兜底）
    try:
        exe = Path(sys.executable).resolve()
        venv_resolved = venv_dir.resolve()
        if str(exe).startswith(str(venv_resolved)):
            return
    except Exception:
        pass

    # ── .venv 已存在但未在其中，直接重入 ────────────────────────────────────
    if venv_python.exists():
        _exec_reenter(venv_python)
        return  # exec 后不会到达

    # ── .venv 不存在，首次初始化 ─────────────────────────────────────────────
    print("[OpsKit] 首次运行，初始化虚拟环境...")

    python_bin = _find_python("3.10")
    if python_bin is None:
        print("[OpsKit] 未找到 Python 3.10+，尝试安装...")
        python_bin = _install_python(project_root)
        if python_bin is None:
            print("[OpsKit ERROR] 无法找到或安装满足要求的 Python，请手动安装 python3.10+")
            sys.exit(1)

    _create_venv(python_bin, venv_dir, project_root)
    _exec_reenter(venv_python)


def _find_python(min_ver: str) -> str | None:
    """按版本号从高到低扫描系统中可用的 Python 3.10+"""
    import shutil

    min_tuple = tuple(int(x) for x in min_ver.split(".")[:2])
    candidates = [f"python3.{minor}" for minor in range(13, min_tuple[1] - 1, -1)]
    candidates += ["python3", "python"]

    for cmd in candidates:
        full = shutil.which(cmd)
        if not full:
            continue
        try:
            r = subprocess.run(
                [full, "--version"],
                capture_output=True, text=True, timeout=5
            )
            line = r.stdout.strip() or r.stderr.strip()
            if "Python" not in line:
                continue
            ver_str = line.split()[-1]
            if not ver_str.startswith("3."):
                continue
            ver_tuple = tuple(int(x) for x in ver_str.split(".")[:2])
            if ver_tuple >= min_tuple:
                return full
        except Exception:
            pass
    return None


def _install_python(project_root: Path) -> str | None:
    """尝试通过系统包管理器安装 Python 3.11，支持 apt/yum/dnf/apk/brew"""
    import shutil

    # 按优先级检测包管理器
    _PM_CMDS: list[tuple[list[str], list[str]]] = [
        (["apt-get", "install", "-y", "python3.11", "python3.11-venv"],
         ["python3.11", "python3"]),
        (["dnf", "install", "-y", "python3.11"],
         ["python3.11", "python3"]),
        (["yum", "install", "-y", "python3"],
         ["python3"]),
        (["apk", "add", "--no-cache", "python3", "py3-pip"],
         ["python3"]),
        (["brew", "install", "python@3.11"],
         ["python3.11", "python3"]),
    ]

    for install_cmd, candidates in _PM_CMDS:
        pm = install_cmd[0]
        if not shutil.which(pm):
            continue
        print(f"[OpsKit] {pm} install python3 ...")
        try:
            subprocess.run(install_cmd, check=True, capture_output=True)
            for c in candidates:
                found = shutil.which(c)
                if found:
                    return found
        except Exception as e:
            print(f"[OpsKit] {pm} 安装失败：{e}")

    return None


def _ensure_venv_pkg(python_bin: str) -> None:
    """
    确保 python3.X-venv 包已安装（Debian/Ubuntu 特有问题）。
    如果 venv 模块存在但 ensurepip 缺失，尝试通过 apt 安装。
    """
    import shutil

    # 检测 ensurepip 是否可用
    try:
        r = subprocess.run(
            [python_bin, "-m", "ensurepip", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            return  # ensurepip 正常，无需处理
    except Exception:
        pass

    # ensurepip 缺失，尝试安装对应 python3.X-venv 包（Debian/Ubuntu 特有问题）
    if shutil.which("apt-get"):
        try:
            r = subprocess.run(
                [python_bin, "--version"],
                capture_output=True, text=True, timeout=5
            )
            ver_str = (r.stdout.strip() or r.stderr.strip()).split()[-1]
            major_minor = ".".join(ver_str.split(".")[:2])
            pkg = f"python{major_minor}-venv"
            print(f"[OpsKit] apt install {pkg} ...")
            subprocess.run(
                ["apt-get", "install", "-y", pkg],
                check=False, capture_output=True
            )
        except Exception:
            pass
    elif shutil.which("apk"):
        # Alpine 的 ensurepip 通过 py3-pip 提供
        try:
            subprocess.run(
                ["apk", "add", "--no-cache", "py3-pip"],
                check=False, capture_output=True
            )
        except Exception:
            pass


def _bootstrap_pip(venv_dir: Path) -> None:
    """
    venv 创建后如果 pip 不存在，通过 get-pip.py 安装。
    Debian/Ubuntu 系统 Python 的 ensurepip 被禁用时会发生此情况。
    """
    pip_bin = venv_dir / "bin" / "pip"
    if pip_bin.exists():
        return

    venv_python = venv_dir / "bin" / "python"
    print("[OpsKit] pip 不存在，尝试 bootstrap pip...")

    # 方法 1：通过系统 pip 复制到 venv
    import shutil
    sys_pip = shutil.which("pip3") or shutil.which("pip")
    if sys_pip:
        try:
            subprocess.run(
                [str(venv_python), "-m", "ensurepip"],
                capture_output=True, check=False
            )
            if pip_bin.exists():
                return
        except Exception:
            pass

    # 方法 2：下载 get-pip.py
    try:
        import urllib.request
        from software.recipes.python.constants import GET_PIP_URL
        get_pip_url = GET_PIP_URL
        get_pip_path = venv_dir / "get-pip.py"
        print(f"[OpsKit] 下载 get-pip.py from {get_pip_url}")
        urllib.request.urlretrieve(get_pip_url, str(get_pip_path))
        subprocess.run(
            [str(venv_python), str(get_pip_path)],
            capture_output=True, check=True
        )
        get_pip_path.unlink(missing_ok=True)
        print("[OpsKit] pip bootstrap 完成")
    except Exception as e:
        print(f"[OpsKit ERROR] pip bootstrap 失败：{e}")
        raise


def _create_venv(python_bin: str, venv_dir: Path, project_root: Path) -> None:
    """创建 venv 并安装 requirements"""
    # 确保 python3.X-venv 包存在（Debian 特有）
    _ensure_venv_pkg(python_bin)

    print(f"[OpsKit] 创建虚拟环境 {venv_dir} (使用 {python_bin})")
    try:
        result = subprocess.run(
            [python_bin, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            # Debian ensurepip 缺失时 venv 创建失败，改用 --without-pip
            if "ensurepip" in result.stdout + result.stderr:
                print("[OpsKit] ensurepip 不可用，使用 --without-pip 创建 venv...")
                subprocess.run(
                    [python_bin, "-m", "venv", "--without-pip", str(venv_dir)],
                    check=True, capture_output=True, timeout=60
                )
                _bootstrap_pip(venv_dir)
            else:
                print(f"[OpsKit ERROR] venv 创建失败：{result.stderr}")
                sys.exit(1)
    except Exception as e:
        print(f"[OpsKit ERROR] venv 创建异常：{e}")
        sys.exit(1)

    # 安装 requirements
    pip_bin = venv_dir / "bin" / "pip"
    req_file = project_root / "requirements.txt"
    if req_file.exists() and pip_bin.exists():
        print("[OpsKit] 安装依赖包...")
        try:
            subprocess.run(
                [str(pip_bin), "install", "-q", "-r", str(req_file)],
                check=True, timeout=300
            )
            print("[OpsKit] 虚拟环境初始化完成，重新启动...")
        except Exception as e:
            print(f"[OpsKit ERROR] 依赖安装失败：{e}")
            sys.exit(1)


def _exec_reenter(venv_python: Path) -> None:
    """exec 重入 venv，完整传递 sys.argv"""
    python_str = str(venv_python)
    try:
        os.execv(python_str, [python_str] + sys.argv)
    except Exception as e:
        print(f"[OpsKit ERROR] exec 重入失败：{e}")
        sys.exit(1)
