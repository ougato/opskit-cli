"""Native acquisition assembly; no plugin import and no host Git execution."""
from contextlib import contextmanager
import json
import re
import tempfile
import time

from core.native_capture import capture
from core.native_process import open_worker
from core.native_runtime import absolute_path, inventory, stage
from core.native_sandbox import NativeSandboxError, get_backend
from core.plugin_fetch_candidate import supervised_inventory
from core.plugin_runtime_inventory import is_fetch_dependency, native_fetch_snapshot
from core.private_file import temporary_private_file


@contextmanager
def local_candidate(python, git_runtime, source, *, staging_parent, deadline,
                    checkpoint, branch=None, tool_files=None, pack=None):
    """Yield a validated candidate only after the isolated Git tree is dead.

    Tool inputs must come from platform preparation, never plugin metadata.
    Snapshot the source including .git, but grant no access to the original.
    Checkout hooks, working changes and source writes are not part of acquisition.
    The lifecycle layer still owns publication, replacement and authorization.
    """
    if branch is not None and (not isinstance(branch, str) or not 0 < len(branch.encode()) <= 256
                               or branch.startswith("-") or any(ord(char) < 32 for char in branch)):
        raise ValueError("invalid native fetch branch")
    def check():
        checkpoint()
        if time.monotonic() >= deadline:
            raise NativeSandboxError("execution_timeout")

    check()
    backend = get_backend()
    backend.probe()
    # Capture identities before creating output; recursive overlap is rejected by
    # stage, and any link/reparse/hardlink in the input fails before execution.
    if tool_files is not None and (not isinstance(tool_files, dict) or "python" not in tool_files
                                   or set(tool_files) - {"python", "git"}):
        raise ValueError("invalid prepared native fetch inventories")
    snapshots = [("python", python, inventory(python) if tool_files is None else tool_files["python"]),
                 ("source", source, inventory(source))]
    check()
    with temporary_private_file() as response:
        with tempfile.TemporaryDirectory(prefix="native-fetch-", dir=absolute_path(staging_parent)) as temporary:
            base = absolute_path(temporary)
            for name, origin, files in snapshots:
                check()
                stage(origin, base / name, files)
            work = base / "work"
            work.mkdir()
            if pack is not None:
                repository = work / "repository"
                (repository / "objects" / "pack").mkdir(parents=True)
                (repository / "objects" / "info").mkdir()
                (repository / "refs" / "heads").mkdir(parents=True)
                (repository / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
                sha256 = isinstance(pack, dict) and isinstance(pack.get("commit"), str) and len(pack["commit"]) == 64
                (repository / "config").write_text(
                    "[core]\n bare = true\n repositoryformatversion = " +
                    ("1\n[extensions]\n objectFormat = sha256\n" if sha256 else "0\n"), encoding="ascii")
            code = base / "code"
            (code / "core").mkdir(parents=True)
            for name, body in native_fetch_snapshot().items():
                relative = name.removeprefix("core/") if is_fetch_dependency(name) else name
                target = code / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            executable = base / "python" / "python.exe"
            if not executable.is_file():
                raise ValueError("native fetch Python executable is missing")
            request = base / "request.json"
            request.write_text(json.dumps({"source": str(base / "source"),
                                           "work": str(work), "branch": branch, "pack": pack}), encoding="utf-8")
            bootstrap = ("import sys,runpy; sys.path.insert(0," + repr(str(code)) +
                         "); sys.argv=['core.native_fetch_worker'," + repr(str(request)) +
                         "]; runpy.run_module('core.native_fetch_worker',run_name='__main__')")
            check()
            with open_worker(backend, str(executable), ["-I", "-S", "-B", "-u", "-c", bootstrap],
                             readonly_trees=(base,), writable_trees=(work,), cwd=str(work)) as child:
                try:
                    capture(child, response, deadline=deadline, checkpoint=check)
                except NativeSandboxError as error:
                    if error.code == "worker_failed":
                        _worker_failure(response, child.poll())
                    raise
        check()
        result = supervised_inventory(response, timeout=deadline - time.monotonic())
        check()
        yield result, response


def _worker_failure(response, exit_code):
    """Accept only bounded fixed diagnostics, never render raw worker output."""
    from core.logger import get_logger
    response.seek(0)
    body = response.read(512)
    failure_prefix = b"OPSKIT_FETCH_FAILURE:"
    if not body.startswith(failure_prefix):
        response.seek(max(0, response.seek(0, 2) - 512))
        body = response.read(512)
    if body.startswith(failure_prefix):
        try:
            data = json.loads(body[len(failure_prefix):])
            if (set(data) == {"kind", "code", "phase"}
                    and isinstance(data["kind"], str)
                    and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", data["kind"])
                    and (data["code"] is None or type(data["code"]) is int)
                    and data["phase"] in {"request", "open-local", "read-local-commit", "init-object-store", "open-object-store",
                                          "read-pack", "write-pack", "index-pack", "read-selected-commit",
                                          "export-tree", "write-response", "copy-archive", "validate-pack",
                                          "load-dulwich", "read-request", "decode-request", "validate-request"}):
                get_logger("opskit.plugin.bootstrap").debug(
                    "Native fetch worker failure=%s code=%s phase=%s", data["kind"], data["code"], data["phase"])
                error = NativeSandboxError("worker_failed")
                error.phase = data["phase"]
                raise error
        except (ValueError, TypeError, KeyError):
            pass
    if type(exit_code) is int:
        get_logger("opskit.plugin.bootstrap").debug("Native fetch worker exit=%s", exit_code)
