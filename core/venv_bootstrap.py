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
_SETUP_WITNESS = ".opskit-bootstrap-witness"
_MARKER_CONTENT = "OpsKit virtual environment bootstrap v1\n"
_COMPLETE_MARKER_CONTENT = "OpsKit virtual environment bootstrap v1: complete\n"
_WITNESS_CONTENT = "OpsKit virtual environment bootstrap witness v1\n"
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
    def write_and_sync(content: str) -> None:
        encoded = content.encode()
        marker.seek(0)
        if marker.write(encoded) != len(encoded):
            raise OSError("short bootstrap marker write")
        marker.truncate()
        marker.flush()
        os.fsync(marker.fileno())

    try:
        write_and_sync(_COMPLETE_MARKER_CONTENT)
    except BaseException:
        # The failed write or sync may have left a complete marker visible to
        # the next process. Restore using the held file, not its replaceable
        # pathname, so a retry refuses to enter the unconfirmed environment.
        # This is best-effort: a second I/O failure can leave an unknown state.
        try:
            write_and_sync(_MARKER_CONTENT)
        except BaseException as recovery_error:
            raise RuntimeError("bootstrap completion failed and marker restoration failed") from recovery_error
        raise


def _check_existing_marker(venv_dir: Path) -> bool:
    """Reject incomplete or unsafe setup markers without changing the directory."""
    setup_marker = venv_dir / _INCOMPLETE_MARKER
    if not setup_marker.exists() and not setup_marker.is_symlink():
        # Environments created before this witness existed remain supported.
        # Its presence proves this was a managed setup even if its primary
        # marker was removed; preserve it and refuse an unverified reentry.
        witness = venv_dir / _SETUP_WITNESS
        if witness.exists() or witness.is_symlink():
            print(_t("venv_resume", environment=venv_dir))
            raise SystemExit(1)
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


def _executable_inside_venv(venv_dir: Path) -> bool:
    """Recognize the current executable's directory, including directory aliases.

    Resolve the parent, not the executable: on POSIX a venv's python is often
    a symlink to the system interpreter and resolving it loses the venv path.
    """
    if not sys.executable:
        return False
    try:
        parent = Path(sys.executable).absolute().parent
        root = venv_dir.absolute()
        return parent.is_relative_to(root) or parent.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


@contextmanager
def _hold_setup_parent(path: Path):
    """Hold and preflight the parent used to persist the new directory entry."""
    if os.name == "nt":
        # Automatic creation is POSIX-only. Windows callers only reuse venvs.
        yield None
        return
    from core.file_identity import same_file_identity
    expected = path.stat()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISDIR(opened.st_mode) or not same_file_identity(expected, opened)
                or not same_file_identity(opened, path.stat())):
            raise RuntimeError("virtual environment parent directory changed")
        # Fail before creating .venv when the parent cannot sync directory
        # entries. Successful fsync is not a guarantee against every device
        # failure, but silently ignoring unsupported sync would not even
        # establish the intended ordering.
        _sync_setup_directory(descriptor)
        yield descriptor
    finally:
        os.close(descriptor)


def _sync_setup_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        raise RuntimeError(_t("venv_dir_sync_failed")) from None


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
    return _open_new_setup_file(venv_dir, directory_fd, _INCOMPLETE_MARKER)


def _open_new_setup_file(venv_dir: Path, directory_fd: int | None, name: str):
    if directory_fd is None:
        return (venv_dir / name).open("x+b")
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
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
    if set(names) not in ({_INCOMPLETE_MARKER}, {_INCOMPLETE_MARKER, _SETUP_WITNESS}):
        raise RuntimeError("virtual environment directory contains unexpected files")


def _validate_setup_identity(venv_dir: Path, opened_dir: os.stat_result, marker, witness=None, parent_fd=None) -> None:
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
    if witness is not None:
        witness_info = os.fstat(witness.fileno())
        current_witness = (venv_dir / _SETUP_WITNESS).lstat()
        if (not stat.S_ISREG(witness_info.st_mode) or not stat.S_ISREG(current_witness.st_mode)
                or witness_info.st_nlink != 1 or current_witness.st_nlink != 1
                or not same_file_identity(witness_info, current_witness)):
            raise RuntimeError("bootstrap witness changed during setup")
        witness.seek(0)
        if witness.read(128) != _WITNESS_CONTENT.encode():
            raise RuntimeError("bootstrap witness content changed during setup")
    if parent_fd is not None:
        if not same_file_identity(os.fstat(parent_fd), venv_dir.parent.stat()):
            raise RuntimeError("virtual environment parent directory changed")


def reenter_existing_windows_venv(project_root: Path) -> None:
    """Reuse the project's prepared environment without changing system Python."""
    venv_dir = project_root / ".venv"
    # -S in older Python versions can make prefix look like the base Python,
    # while an inherited VIRTUAL_ENV may describe a different environment.
    # Check the actual executable before either compatibility shortcut.
    if _executable_inside_venv(venv_dir):
        _check_existing_marker(venv_dir)
    if sys.prefix != sys.base_prefix:
        if _same_path(sys.prefix, venv_dir):
            _check_existing_marker(venv_dir)
        return
    if os.environ.get("VIRTUAL_ENV"):
        if _same_path(os.environ["VIRTUAL_ENV"], venv_dir):
            _check_existing_marker(venv_dir)
        return
    executable = venv_dir / "Scripts" / "python.exe"
    # A failed setup can leave the marker before creating the interpreter.
    # Inspect it even when there is no executable to re-enter.
    completed = _check_existing_marker(venv_dir)
    if completed and not executable.is_file():
        print(_t("venv_incomplete", environment=venv_dir))
        raise SystemExit(1)
    if executable.is_file():
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

    # An inherited VIRTUAL_ENV or prefix can disagree with the executable,
    # especially under -S; do not let it hide an interrupted project setup.
    if _executable_inside_venv(venv_dir):
        _check_existing_marker(venv_dir)

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
        if not venv_python.is_file():
            print(_t("venv_incomplete", environment=venv_dir))
            raise SystemExit(1)
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
    _require_dependencies_file(project_root)
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
        with _hold_setup_parent(venv_dir.parent) as parent_fd:
            # A permissive umask must not make the in-progress environment
            # writable by other accounts. This does not protect against another
            # process running as the same user or a writable project parent.
            venv_dir.mkdir(mode=0o755)
            identity = venv_dir.lstat()
            with _hold_new_directory(venv_dir, identity) as (directory_fd, opened_dir):
                # Create the witness first. If setup stops before the primary
                # marker exists, its presence still blocks accidental reentry.
                with _open_new_setup_file(venv_dir, directory_fd, _SETUP_WITNESS) as witness:
                    witness_data = _WITNESS_CONTENT.encode()
                    if witness.write(witness_data) != len(witness_data):
                        raise OSError("short bootstrap witness write")
                    witness.flush()
                    os.fsync(witness.fileno())
                    with _open_new_marker(venv_dir, directory_fd) as created_marker:
                        marker_data = _MARKER_CONTENT.encode()
                        if created_marker.write(marker_data) != len(marker_data):
                            raise OSError("short bootstrap marker write")
                        created_marker.flush()
                        os.fsync(created_marker.fileno())
                        if directory_fd is not None:
                            _sync_setup_directory(directory_fd)
                        if parent_fd is not None:
                            _sync_setup_directory(parent_fd)
                        with _hold_setup_marker(venv_dir, created_marker=created_marker) as (marker, incomplete):
                            _validate_setup_identity(venv_dir, opened_dir, marker, witness, parent_fd)
                            if incomplete:
                                _check_new_directory_contents(venv_dir, directory_fd)
                                _create_venv(python_bin, venv_dir, opened_dir, marker, witness, parent_fd)
                                _validate_setup_identity(venv_dir, opened_dir, marker, witness, parent_fd)
                                _install_dependencies(venv_dir, project_root, opened_dir, marker, witness, parent_fd)
                                _validate_setup_identity(venv_dir, opened_dir, marker, witness, parent_fd)
                                _complete_setup(marker)
                                _validate_setup_identity(venv_dir, opened_dir, marker, witness, parent_fd)
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


def _python_setup_env() -> dict[str, str]:
    """Keep setup independent of ambient Python paths, preserving pip settings.

    Setup commands also use Python's ``-I`` to exclude the working directory
    and user site; clearing environment variables alone does not exclude them.
    """
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return env


def _ensure_venv_pkg(python_bin: str) -> None:
    """
    确保 python3.X-venv 包已安装（Debian/Ubuntu 特有问题）。
    如果 venv 模块存在但 ensurepip 缺失，尝试通过 apt 安装。
    """
    import shutil

    # 检测 ensurepip 是否可用
    try:
        r = subprocess.run(
            [python_bin, "-I", "-m", "ensurepip", "--version"],
            capture_output=True, text=True, timeout=5, env=_python_setup_env()
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
    env = _python_setup_env()
    try:
        available = subprocess.run([str(venv_python), "-I", "-m", "pip", "--version"],
                                   capture_output=True, timeout=15, env=env)
        if available.returncode == 0:
            return
        print(_t("pip_bootstrap"))
        # Match the default venv bootstrap, including the unversioned pip
        # entry point, but run it only after the caller rechecks identities.
        subprocess.run([str(venv_python), "-I", "-m", "ensurepip", "--upgrade", "--default-pip"],
                       capture_output=True, check=True, timeout=60, env=env)
        subprocess.run([str(venv_python), "-I", "-m", "pip", "--version"],
                       capture_output=True, check=True, timeout=15, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(_t("pip_failed", environment=venv_dir)) from exc


def _create_venv(python_bin: str, venv_dir: Path, opened_dir=None, marker=None, witness=None, parent_fd=None) -> None:
    """创建并检查 venv；依赖安装由调用方在身份复核后执行。"""
    # Package discovery and installation can take minutes. Do not let a
    # directory or file substituted during that wait reach the venv command.
    # This check narrows the race; it cannot stop a later substitution.
    try:
        initial = opened_dir if opened_dir is not None else venv_dir.lstat()
        if marker is not None:
            _validate_setup_identity(venv_dir, initial, marker, witness, parent_fd)
        _ensure_venv_pkg(python_bin)
        if marker is not None:
            _validate_setup_identity(venv_dir, initial, marker, witness, parent_fd)
        with _hold_new_directory(venv_dir, initial) as (directory_fd, opened):
            from core.file_identity import same_file_identity
            if not same_file_identity(opened, venv_dir.lstat()):
                raise RuntimeError("virtual environment directory changed during package preparation")
            _check_new_directory_contents(venv_dir, directory_fd)
    except (OSError, RuntimeError) as exc:
        print(_t("venv_busy", error=exc))
        raise SystemExit(1) from None

    print(_t("venv_create", environment=venv_dir, python=python_bin))
    try:
        result = subprocess.run(
            [python_bin, "-I", "-m", "venv", "--without-pip", str(venv_dir)],
            capture_output=True, text=True, timeout=60, env=_python_setup_env()
        )
        if result.returncode != 0:
            # Do not rerun venv over a partially created directory: files
            # encountered on a retry cannot be proven to be ours.
            print(_t("venv_failed", error=result.stderr))
            sys.exit(1)
    except Exception as e:
        print(_t("venv_failed", error=e))
        sys.exit(1)

    if not (venv_dir / "bin" / "python").is_file() or not (venv_dir / "pyvenv.cfg").is_file():
        print(_t("venv_incomplete", environment=venv_dir))
        raise SystemExit(1)


def _require_dependencies_file(project_root: Path) -> Path:
    """Never mark a newly created environment ready without its dependency list."""
    path = project_root / "requirements.txt"
    if not path.is_file():
        print(_t("deps_manifest_missing", path=path))
        raise SystemExit(1)
    return path


def _install_dependencies(venv_dir: Path, project_root: Path, opened_dir=None, marker=None,
                          witness=None, parent_fd=None) -> None:
    """Complete setup before a managed environment can be reentered."""
    req_file = _require_dependencies_file(project_root)
    try:
        if marker is not None:
            _validate_setup_identity(venv_dir, opened_dir, marker, witness, parent_fd)
        _bootstrap_pip(venv_dir)
        # Initializing pip can take time. Do not run a second executable from
        # a directory or marker substituted while that process was running.
        # This is a stage boundary check, not a substitute for safe publication.
        if marker is not None:
            _validate_setup_identity(venv_dir, opened_dir, marker, witness, parent_fd)
        print(_t("deps_install"))
        subprocess.run(
            [str(venv_dir / "bin" / "python"), "-I", "-m", "pip", "install", "-q", "-r", str(req_file)],
            check=True, timeout=300, env=_python_setup_env()
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
