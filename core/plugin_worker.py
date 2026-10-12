"""Container-only adapter for Python/exec plugins and the JSON service protocol."""
from __future__ import annotations

import base64
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

PREFIX = "OPSKIT_RPC:"
STATE_LIMIT = 16 * 1024**2


def broker(method, *args, **kwargs):
    print(PREFIX + json.dumps({"type": "call", "method": method, "args": args, "kwargs": kwargs}), flush=True)
    reply = json.loads(sys.stdin.readline(1024 * 1024))
    if reply.get("cancel"):
        from core.prompt import UserCancel
        raise UserCancel
    if reply.get("error"):
        raise PermissionError(reply["error"])
    return reply.get("value")


class RemoteService:
    service_api_version = 2

    def __init__(self, name):
        self.name = name

    def __getattr__(self, method):
        if method.startswith("_"):
            raise AttributeError(method)
        return lambda *args, **kwargs: broker("service.call", self.name, method, list(args), kwargs)


def _adapt_sdk(name: str):
    from core import sdk, prompt
    from core import plugin_services
    from core import paths
    import core.i18n as i18n
    import core.theme as theme
    i18n.init()
    theme.init()
    # paged_select evaluates plugin callbacks locally and calls adapted select.
    for method in ("select", "multi_select", "confirm", "text_input", "pause", "clear_screen", "print_header"):
        def call(*args, _method=method, **kwargs):
            return broker(_method, *args, **kwargs)
        setattr(sdk, method, call)
        setattr(prompt, method, call)

    def owned_data(namespace: str) -> Path:
        # Legacy namespace is an alias only; it cannot select another owner.
        if namespace not in {name, name.replace("_", "-")}:
            raise PermissionError("plugin namespace belongs to another identity")
        return Path("/data/state")

    paths.plugin_data_dir = sdk.plugin_data_dir = owned_data
    paths.plugins_dir = sdk.plugins_dir = lambda: Path("/plugin")
    sdk.get_service = plugin_services.get_service = lambda service: RemoteService(service)
    def open_menu(service, breadcrumb, context=None, source=""):
        # Installing a new provider requires the platform's explicit lifecycle.
        broker("service.call", service, "open_menu", [breadcrumb, context or {}], {})
        return True
    sdk.open_service_menu = plugin_services.open_service_menu = open_menu
    def dependency(package, import_name=""):
        import importlib.util
        return importlib.util.find_spec(import_name or package) is not None
    sdk.ensure_python_package = dependency
    def denied(*args, **kwargs):
        raise PermissionError("host software operations are not granted to plugins")
    sdk.ensure_software = sdk.software_bin = denied


def _pack_state() -> str:
    root = Path("/data")
    files = list(root.rglob("*"))
    size = 0
    if len(files) > 4096:
        raise ValueError("state file count exceeded")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:") as archive:
        for path in files:
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("state links and special files forbidden")
            if path.is_file():
                size += path.stat().st_size
                if size > STATE_LIMIT:
                    raise ValueError("persistent state quota exceeded")
                archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    if buffer.tell() > STATE_LIMIT + 4096 * 2048 + 10240:
        raise ValueError("state archive metadata quota exceeded")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _value(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError("service results must be JSON serializable")


def _decode(value):
    if isinstance(value, dict):
        if set(value) == {"__opskit_path__"}:
            path = Path(value["__opskit_path__"]).resolve()
            path.relative_to(Path("/data"))
            return path
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def main() -> None:
    if os.environ.get("OPSKIT_SANDBOX") != "1":
        raise RuntimeError("plugin worker must run in a platform sandbox")
    request = _decode(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    shutil.copytree("/seed", "/data", dirs_exist_ok=True)
    Path("/data/state").mkdir(parents=True, exist_ok=True)
    _adapt_sdk(request["name"])
    sys.path.append("/plugin")
    value = None
    try:
        if request["kind"] == "exec":
            entry = request["entry"]
            if isinstance(entry, dict):
                entry = entry.get("linux")
            path = (Path("/plugin") / entry).resolve()
            path.relative_to(Path("/plugin"))
            value = subprocess.call([str(path), *request["args"]], cwd="/plugin")
        else:
            module = importlib.import_module(request["entry"])
            Path(module.__file__).resolve().relative_to(Path("/plugin"))
            action = request["action"]
            if action == "entry":
                value = module.register().entry()
            elif action == "cli":
                entry = getattr(module, "cli", None)
                if not callable(entry):
                    raise ValueError("plugin must export cli(argv) to use plugin-owned commands")
                value = entry(request["args"])
            else:
                service = module.provide_service(request["service"])
                if service is None:
                    raise ValueError("service unavailable")
                if action == "service.describe":
                    value = {"service_api_version": getattr(service, "service_api_version", 1)}
                elif action == "service.call":
                    method = request["method"]
                    if method.startswith("_") or not method.isidentifier():
                        raise ValueError("private service methods forbidden")
                    args = request["args"]
                    kwargs = request["kwargs"]
                    if method == "open_menu":
                        context = dict(args[1] if len(args) > 1 else {})
                        context["caller"] = request["caller"]
                        args = [args[0], context]
                    value = getattr(service, method)(*args, **kwargs)
                else:
                    raise ValueError("unknown worker action")
        print(PREFIX + json.dumps({"type": "result", "value": value, "state": _pack_state()}, default=_value), flush=True)
    except SystemExit as exc:
        print(PREFIX + json.dumps({"type": "result", "value": int(exc.code or 0), "state": _pack_state()}), flush=True)
    except BaseException as exc:
        # Never emit exception values: they can contain cloud credentials.
        print(PREFIX + json.dumps({"type": "result", "error": type(exc).__name__}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
