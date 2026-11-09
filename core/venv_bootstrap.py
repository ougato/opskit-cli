"""venv 自举 — 首次运行时自动创建 .venv 并安装依赖，然后 exec 重入"""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

_INCOMPLETE_MARKER = ".opskit-bootstrap-incomplete"
_MARKER_CONTENT = "OpsKit virtual environment bootstrap v1\n"
_COMPLETE_MARKER_CONTENT = "OpsKit virtual environment bootstrap v1: complete\n"
_MIN_PYTHON_VERSION = "3.10"


def _t(key: str, **values: object) -> str:
    """Read the bootstrap catalogue without importing third-party packages."""
    with (Path(__file__).parent / "locale" / "bootstrap.json").open(encoding="utf-8") as file:
        messages = json.load(file)
    language = next((os.environ[name] for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")
                     if os.environ.get(name)), "en")
    selected = "zh" if language.lower().startswith("zh") else "en"
    return messages[selected][key].format(**values)


@contextmanager
def _hold_setup_marker(venv_dir: Path, *, created_marker=None):
    """Serialize resume attempts using the marker, leaving unknown files alone."""
    path = venv_dir / _INCOMPLETE_MARKER
    if path.is_symlink():
        raise RuntimeError("bootstrap marker is a symbolic link")
    if created_marker is None:
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            marker_context = os.fdopen(descriptor, "r+b")
        except BaseException:
            os.close(descriptor)
            raise
    else:
        # Keep the handle created by O_EXCL open throughout setup. Comparing a
        # closed file's inode later would permit inode reuse after replacement.
        marker_context = nullcontext(created_marker)
    with marker_context as file:
        opened = os.fstat(file.fileno())
        current = path.lstat()
        from core.file_identity import same_file_identity
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not same_file_identity(opened, current)):
            raise RuntimeError("bootstrap marker identity is unsafe")
        if os.name == "nt":
            import msvcrt
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            file.seek(0)
            content = file.read(128)
            if content not in (_MARKER_CONTENT.encode(), _COMPLETE_MARKER_CONTENT.encode()):
                raise RuntimeError("bootstrap marker content is invalid")
            yield file, content == _MARKER_CONTENT.encode()
        finally:
            if os.name == "nt":
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


def _complete_setup(marker) -> None:
    marker.seek(0)
    marker.write(_COMPLETE_MARKER_CONTENT.encode())
    marker.truncate()
    marker.flush()
    os.fsync(marker.fileno())


def _check_existing_marker(venv_dir: Path) -> bool:
    """Reject incomplete or unsafe setup markers without changing the directory."""
    setup_marker = venv_dir / _INCOMPLETE_MARKER
    if not setup_marker.exists() and not setup_marker.is_symlink():
        return False
    try:
        with _hold_setup_marker(venv_dir) as (_marker, incomplete):
            if incomplete:
                print(_t("venv_resume", environment=venv_dir))
                raise SystemExit(1)
    except (OSError, RuntimeError) as exc:
        print(_t("venv_busy", error=exc))
        raise SystemExit(1) from None
    return True


def _same_path(left: str | Path, right: Path) -> bool:
    try:
        return Path(left).resolve() == right.resolve()
    except (OSError, RuntimeError):
        return False


@contextmanager
def _hold_new_directory(path: Path, initial: os.stat_result):
    """Keep a POSIX directory inode alive while creating its setup marker."""
    from core.file_identity import same_file_identity

    if not stat.S_ISDIR(initial.st_mode) or not same_file_identity(initial, initial):
        raise RuntimeError("virtual environment directory identity is unsafe")
    if os.name == "nt":
        # The POSIX bootstrap is not used on Windows; unit tests still exercise
        # this path there without pretending Windows supplies POSIX dir_fd.
        yield None, initial
        return
    if os.open not in os.supports_dir_fd:
        raise RuntimeError("directory-relative marker creation is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not same_file_identity(initial, opened):
            raise RuntimeError("virtual environment directory changed during setup")
        yield descriptor, opened
    finally:
        os.close(descriptor)


def _open_new_marker(venv_dir: Path, directory_fd: int | None):
    """Claim a marker without overwriting an existing file or following a link."""
    if directory_fd is None:
        return (venv_dir / _INCOMPLETE_MARKER).open("x+b")
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(_INCOMPLETE_MARKER, flags, 0o600, dir_fd=directory_fd)
    try:
        return os.fdopen(descriptor, "w+b")
    except BaseException:
        os.close(descriptor)
        raise


def _check_new_directory_contents(venv_dir: Path, directory_fd: int | None) -> None:
    """Reject unknown entries already present before invoking venv.

    This is a preflight check, not protection against later concurrent writes.
    """
    if directory_fd is None:
        names = os.listdir(venv_dir)
    else:
        if os.listdir not in os.supports_fd:
            raise RuntimeError("directory-relative inspection is unavailable")
        names = os.listdir(directory_fd)
    if set(names) != {_INCOMPLETE_MARKER}:
        raise RuntimeError("virtual environment directory contains unexpected files")


def _validate_setup_identity(venv_dir: Path, opened_dir: os.stat_result, marker) -> None:
    """Check held identities before using a newly built environment.

    This narrows races between stages; it cannot prevent later path swaps.
    """
    from core.file_identity import same_file_identity

    current_dir = venv_dir.lstat()
    marker_info = os.fstat(marker.fileno())
    current_marker = (venv_dir / _INCOMPLETE_MARKER).lstat()
    if not stat.S_ISDIR(current_dir.st_mode) or not same_file_identity(opened_dir, current_dir):
        raise RuntimeError("virtual environment directory changed during setup")
    if (not stat.S_ISREG(marker_info.st_mode) or not stat.S_ISREG(current_marker.st_mode)
            or marker_info.st_nlink != 1 or current_marker.st_nlink != 1
            or not same_file_identity(marker_info, current_marker)):
        raise RuntimeError("bootstrap marker changed during setup")


def reenter_existing_windows_venv(project_root: Path) -> None:
    """Reuse the project's prepared environment without changing system Python."""
    venv_dir = project_root / ".venv"
    if sys.prefix != sys.base_prefix:
        if _same_path(sys.prefix, venv_dir):
            _check_existing_marker(venv_dir)
        return
    if os.environ.get("VIRTUAL_ENV"):
        if _same_path(os.environ["VIRTUAL_ENV"], venv_dir):
            _check_existing_marker(venv_dir)
        return
    executable = venv_dir / "Scripts" / "python.exe"
    if executable.is_file():
        _check_existing_marker(venv_dir)
        _exec_reenter(executable)


def ensure_venv(project_root: Path | None = None) -> None:
    """
    确保当前进程使用虚拟环境，尊重显式选择的外部环境。

    - 已在实际虚拟环境内 → 直接返回
    - .venv 存在但未在其中 → exec 重入
    - .venv 不存在 → 创建 venv → 安装 requirements → exec 重入

    注意：此函数在 Rich/i18n 初始化之前调用，只用 print() 输出。
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent.resolve()

    venv_dir = project_root / ".venv"
    venv_python = venv_dir / "bin" / "python"

    # An explicitly selected external virtual environment is already isolated,
    # even when its launcher did not export VIRTUAL_ENV.
    if sys.prefix != sys.base_prefix:
        if _same_path(sys.prefix, venv_dir):
            _check_existing_marker(venv_dir)
        return

    # ── 已在 .venv 内，直接返回 ──────────────────────────────────────────────
    # 兼容外部启动器声明的 VIRTUAL_ENV。
    if os.environ.get("VIRTUAL_ENV"):
        if _same_path(os.environ["VIRTUAL_ENV"], venv_dir):
            _check_existing_marker(venv_dir)
        return

    # 方法 2：sys.prefix 指向 venv 目录
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            _check_existing_marker(venv_dir)
            return
    except Exception:
        pass

    # 方法 3：检查解释器是否位于项目虚拟环境内（路径组件匹配）。
    try:
        exe = Path(sys.executable)
        if (exe.absolute().is_relative_to(venv_dir.absolute())
                or exe.resolve().is_relative_to(venv_dir.resolve())):
            _check_existing_marker(venv_dir)
            return
    except (OSError, RuntimeError):
        pass

    # A marker alone cannot establish ownership of a pre-existing directory.
    # Refuse to mutate an interrupted setup until its owner has inspected it.
    if _check_existing_marker(venv_dir):
        _exec_reenter(venv_python)
        return

    # ── .venv 已存在但未在其中，直接重入 ────────────────────────────────────
    if venv_python.exists():
        _exec_reenter(venv_python)
        return  # exec 后不会到达

    if venv_dir.exists() or venv_dir.is_symlink():
        print(_t("venv_incomplete", environment=venv_dir))
        raise SystemExit(1)

    # ── .venv 不存在，首次初始化 ─────────────────────────────────────────────
    print(_t("first_run"))

    python_bin = _find_python(_MIN_PYTHON_VERSION)
    if python_bin is None:
        print(_t("python_missing"))
        python_bin = _install_python(project_root)
        if python_bin is None:
            print(_t("python_failed"))
            sys.exit(1)

    # Claim only a newly created directory; never delete it on failure.
    try:
        venv_dir.mkdir()
        identity = venv_dir.lstat()
        with _hold_new_directory(venv_dir, identity) as (directory_fd, opened_dir):
            with _open_new_marker(venv_dir, directory_fd) as created_marker:
                created_marker.write(_MARKER_CONTENT.encode())
                created_marker.flush()
                with _hold_setup_marker(venv_dir, created_marker=created_marker) as (marker, incomplete):
                    _validate_setup_identity(venv_dir, opened_dir, marker)
                    if incomplete:
                        _check_new_directory_contents(venv_dir, directory_fd)
                        _create_venv(python_bin, venv_dir)
                        _validate_setup_identity(venv_dir, opened_dir, marker)
                        _install_dependencies(venv_dir, project_root)
                        _validate_setup_identity(venv_dir, opened_dir, marker)
                        _complete_setup(marker)
                        _validate_setup_identity(venv_dir, opened_dir, marker)
    except (OSError, RuntimeError) as exc:
        print(_t("venv_busy", error=exc))
        raise SystemExit(1) from None
    _exec_reenter(venv_python)


def _python_candidates(min_minor: int):
    """Prefer known names, then use the interpreter already running OpsKit."""
    known_max = max(14, min(sys.version_info.minor, 20))
    yield from (f"python3.{minor}" for minor in range(known_max, min_minor - 1, -1))
    yield "python3"
    yield "python"
    # The current executable remains usable even if no versioned name or
    # generic alias is present in PATH. This also supports future versions
    # without enumerating potentially remote or very large PATH directories.
    if sys.executable:
        yield sys.executable


def _find_python(min_ver: str) -> str | None:
    """Find a usable Python at or above the requested version."""
    import shutil

    min_tuple = tuple(int(x) for x in min_ver.split(".")[:2])

    for cmd in _python_candidates(min_tuple[1]):
        full = shutil.which(cmd)
        if not full:
            continue
        if _python_meets_version(full, min_tuple):
            return full
    return None


def _python_meets_version(executable: str, minimum: tuple[int, ...]) -> bool:
    """Verify the interpreter itself, including after a system install."""
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return False
        line = result.stdout.strip() or result.stderr.strip()
        if "Python" not in line:
            return False
        version = line.split()[-1]
        if not version.startswith("3."):
            return False
        actual = tuple(int(part) for part in version.split(".")[:2])
        return actual >= minimum
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _install_python(project_root: Path) -> str | None:
    """尝试通过系统包管理器安装 Python 3.11，支持 apt/yum/dnf/apk/brew"""
    import shutil

    minimum = tuple(int(part) for part in _MIN_PYTHON_VERSION.split(".")[:2])

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
        print(_t("system_install", manager=pm, package="python3"))
        try:
            subprocess.run(install_cmd, check=True, capture_output=True, timeout=300)
            for c in candidates:
                found = shutil.which(c)
                if found and _python_meets_version(found, minimum):
                    return found
        except Exception as e:
            print(_t("system_install_failed", manager=pm, error=e))

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
            print(_t("system_install", manager="apt", package=pkg))
            subprocess.run(
                ["apt-get", "install", "-y", pkg],
                check=False, capture_output=True, timeout=300
            )
        except Exception:
            pass
    elif shutil.which("apk"):
        # Alpine 的 ensurepip 通过 py3-pip 提供
        try:
            subprocess.run(
                ["apk", "add", "--no-cache", "py3-pip"],
                check=False, capture_output=True, timeout=300
            )
        except Exception:
            pass


def _bootstrap_pip(venv_dir: Path) -> None:
    """Ensure pip is usable without executing an unverified downloaded script."""
    venv_python = venv_dir / "bin" / "python"
    try:
        available = subprocess.run([str(venv_python), "-m", "pip", "--version"],
                                   capture_output=True, timeout=15)
        if available.returncode == 0:
            return
        print(_t("pip_bootstrap"))
        subprocess.run([str(venv_python), "-m", "ensurepip"],
                       capture_output=True, check=True, timeout=60)
        subprocess.run([str(venv_python), "-m", "pip", "--version"],
                       capture_output=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(_t("pip_failed", environment=venv_dir)) from exc


def _create_venv(python_bin: str, venv_dir: Path) -> None:
    """创建并检查 venv；依赖安装由调用方在身份复核后执行。"""
    # 确保 python3.X-venv 包存在（Debian 特有）
    _ensure_venv_pkg(python_bin)

    print(_t("venv_create", environment=venv_dir, python=python_bin))
    try:
        result = subprocess.run(
            [python_bin, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            # Debian ensurepip 缺失时 venv 创建失败，改用 --without-pip
            if "ensurepip" in result.stdout + result.stderr:
                print(_t("venv_without_pip"))
                subprocess.run(
                    [python_bin, "-m", "venv", "--without-pip", str(venv_dir)],
                    check=True, capture_output=True, timeout=60
                )
            else:
                print(_t("venv_failed", error=result.stderr))
                sys.exit(1)
    except Exception as e:
        print(_t("venv_failed", error=e))
        sys.exit(1)

    if not (venv_dir / "bin" / "python").is_file() or not (venv_dir / "pyvenv.cfg").is_file():
        print(_t("venv_incomplete", environment=venv_dir))
        raise SystemExit(1)


def _install_dependencies(venv_dir: Path, project_root: Path) -> None:
    """Complete setup before a managed environment can be reentered."""
    req_file = project_root / "requirements.txt"
    if req_file.exists():
        try:
            _bootstrap_pip(venv_dir)
            print(_t("deps_install"))
            subprocess.run(
                [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "-q", "-r", str(req_file)],
                check=True, timeout=300
            )
            print(_t("deps_ready"))
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            print(_t("deps_failed", error=e))
            sys.exit(1)


def _exec_reenter(venv_python: Path) -> None:
    """exec 重入 venv，完整传递 sys.argv"""
    python_str = str(venv_python)
    try:
        if sys.platform == "win32":
            # Windows execv detaches the replacement from callers waiting on
            # the original process. Preserve CLI streams and exit status.
            # Console Ctrl+C also reaches the child. A no-op handler in this
            # waiting parent lets the child handle it and finish its cleanup;
            # subprocess.call would otherwise kill it on KeyboardInterrupt.
            previous = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda signum, frame: None)
            try:
                code = subprocess.call([python_str] + sys.argv)
            except KeyboardInterrupt:
                raise SystemExit(130) from None
            finally:
                signal.signal(signal.SIGINT, previous)
            raise SystemExit(code)
        os.execv(python_str, [python_str] + sys.argv)
    except Exception as e:
        print(_t("reenter_failed", error=e))
        sys.exit(1)
