"""Isolated Python/exec plugin adapter and bounded JSON service protocol."""
from __future__ import annotations

import base64
import importlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath
from core import plugin_json
from core.plugin_tree import bounded_paths

PREFIX = "OPSKIT_RPC:"
STATE_LIMIT = 16 * 1024**2


def _data_root():
    return Path(os.environ.get("OPSKIT_DATA_DIR", "/data"))


def _plugin_root():
    return Path(os.environ.get("OPSKIT_PLUGIN_ROOT", "/plugin"))


def _seed_root():
    return Path(os.environ.get("OPSKIT_SEED_ROOT", "/seed"))


def broker(method, *args, **kwargs):
    print(PREFIX + plugin_json.dumps_message({"type": "call", "method": method, "args": args, "kwargs": kwargs}), flush=True)
    reply = plugin_json.require_reply(plugin_json.loads_message(sys.stdin.readline(plugin_json.MAX_TEXT + 1).rstrip("\r\n")))
    if reply.get("cancel"):
        from core.prompt import UserCancel
        raise UserCancel
    if reply.get("exit"):
        from core.prompt import UserExit
        raise UserExit
    if reply.get("error"):
        raise PermissionError(reply["error"])
    return reply.get("value")


class RemoteService:

    @property
    def service_api_version(self):
        return broker("service.describe", self.name)["service_api_version"]

    def __init__(self, name):
        self.name = name

    def __getattr__(self, method):
        if method.startswith("_"):
            raise AttributeError(method)
        return lambda *args, **kwargs: broker("service.call", self.name, method, list(args), kwargs)


def _adapt_sdk(name: str):
    from core import sdk, prompt
    from contextlib import contextmanager
    from core import paths
    from functools import wraps
    from inspect import signature
    import core.i18n as i18n
    import core.theme as theme
    i18n.init(language=os.environ.get("OPSKIT_LANGUAGE"))
    theme.init_bundled_default()
    # paged_select evaluates plugin callbacks locally and calls adapted select.
    def adapt_ui(method, original):
        bound_signature = signature(original)
        @wraps(original)
        def call(*args, **kwargs):
            bound = bound_signature.bind(*args, **kwargs)
            try:
                result = broker(method, **bound.arguments)
            except prompt.UserCancel:
                if method in {"select", "select_input", "multi_select"}:
                    return None
                raise
            return None if method == "select_input" and result == "0" else result
        return call

    for method in ("select", "select_input", "multi_select", "confirm", "text_input", "pause", "clear_screen", "print_header"):
        call = adapt_ui(method, getattr(sdk, method))
        setattr(sdk, method, call)
        setattr(prompt, method, call)

    @contextmanager
    def waiting(message=""):
        broker("ui.wait_start", message)
        try:
            yield
        except BaseException:
            try:
                broker("ui.wait_stop")
            except Exception:
                pass
            raise
        else:
            broker("ui.wait_stop")
    sdk.waiting = waiting

    def owned_data(namespace: str) -> Path:
        if namespace != name:
            raise PermissionError("plugin namespace belongs to another identity")
        return _data_root() / "state"

    paths.plugin_data_dir = sdk.plugin_data_dir = owned_data

    if os.name == "nt":
        def save_sandbox_yaml(path, data):
            from core.yamlio import _save_yaml
            target = Path(path).resolve()
            # AppContainer can write to its private data directory using its
            # inherited DACL but cannot set a replacement DACL on the file.
            in_private_data = target.is_relative_to(_data_root().resolve())
            _save_yaml(target, data, inherit_windows_acl=in_private_data)

        sdk.save_yaml = save_sandbox_yaml
    paths.plugins_dir = sdk.plugins_dir = _plugin_root
    sdk.get_service = lambda name: RemoteService(name)
    def read_host_file(directory, relative, *, missing_ok=False):
        options = {"missing_ok": True} if missing_ok else {}
        reply = broker("host.read_file", directory, relative, **options)
        if reply is None and missing_ok:
            return None
        if not isinstance(reply, dict) or set(reply) != {"encoding", "content"} or reply["encoding"] != "base64":
            raise ValueError("invalid host file response")
        return base64.b64decode(reply["content"], validate=True)
    sdk.read_host_file = read_host_file
    def compare_write_host_file(directory, relative, expected_sha256, content):
        if not isinstance(content, bytes):
            raise TypeError("host file content must be bytes")
        return broker("host.compare_write", directory, relative, expected_sha256,
                      base64.b64encode(content).decode("ascii"))
    sdk.compare_write_host_file = compare_write_host_file
    def atomic_write_host_file(directory, relative, lock_name, expected_sha256, content):
        if not isinstance(content, bytes):
            raise TypeError("host file content must be bytes")
        return broker("host.atomic_write", directory, relative, lock_name, expected_sha256,
                      base64.b64encode(content).decode("ascii"))
    sdk.atomic_write_host_file = atomic_write_host_file
    def create_host_file(directory, relative, content):
        if not isinstance(content, bytes):
            raise TypeError("host file content must be bytes")
        return broker("host.create_file", directory, relative, base64.b64encode(content).decode("ascii"))
    sdk.create_host_file = create_host_file
    sdk.host_file_status = lambda directory, relative: broker("host.file_status", directory, relative)
    sdk.list_host_files = lambda directory, *, missing_ok=False: broker("host.list_files", directory, missing_ok=missing_ok)
    sdk.host_asar_directories = lambda directory, relative, member: broker("host.asar_directories", directory, relative, member)
    sdk.artifact_info = lambda directory, relative: broker("host.artifact_info", directory, relative)
    sdk.host_execute = lambda executable, arguments, directory, options=None: broker(
        "host.execute", executable, arguments, directory, {} if options is None else options)
    sdk.host_artifacts = lambda action, parameters: broker("host.artifacts", action, parameters)
    def host_path_info(path, directory=False, write=False, execute=False):
        if execute is not False:
            return broker("host.path_info", path, directory, write, execute)
        if write is not False:
            return broker("host.path_info", path, directory, write)
        return broker("host.path_info", path, directory)
    sdk.host_path_info = host_path_info
    sdk.host_find_executable = lambda name, preferred="": broker("host.find_executable", name, preferred)
    sdk.host_file_version = lambda path: broker("host.file_version", path)
    def https_request(url, method="GET", headers=None, body=b""):
        if not isinstance(body, bytes):
            raise TypeError("HTTPS body must be bytes")
        reply = broker("host.https", url, method, headers or {}, base64.b64encode(body).decode("ascii"))
        return {"status": reply["status"], "body": base64.b64decode(reply["body"], validate=True),
                "etag": reply.get("etag")}
    sdk.https_request = https_request
    def upload_file(directory, relative, url, sha256, headers=None, *, verify_url=None):
        args = (directory, relative, url, sha256, headers or {})
        return broker("host.upload_file", *args, *((verify_url,) if verify_url is not None else ()))
    sdk.upload_file = upload_file
    sdk.request_plugin_install = lambda provider: broker("service.install_provider", provider)

    def open_menu(name, breadcrumb, context=None):
        broker("service.call", name, "open_menu", [breadcrumb, context or {}], {})
        return True
    sdk.open_service_menu = open_menu


def _pack_state() -> str:
    root = _data_root()
    files = bounded_paths(root, max_entries=4096, skip_dirs=frozenset())
    size = 0
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
            portable = PurePosixPath(value["__opskit_path__"])
            if (not portable.is_absolute() or ".." in portable.parts
                    or "\\" in str(portable) or "\0" in str(portable)):
                raise ValueError("invalid portable state path")
            relative = portable.relative_to(PurePosixPath("/data"))
            path = (_data_root() / relative).resolve()
            path.relative_to(_data_root().resolve())
            return path
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _service_contract(contract):
    if not isinstance(contract, dict) or set(contract) != {"api_version", "methods"}:
        raise ValueError("service contract required")
    version, methods = contract["api_version"], contract["methods"]
    if (type(version) is not int or not 1 <= version <= 65535
            or not isinstance(methods, list) or not 1 <= len(methods) <= 64
            or any(not isinstance(value, str) or len(value) > 128 or not value.isascii()
                   or not value.isidentifier() or value.startswith("_") for value in methods)
            or len(set(methods)) != len(methods)):
        raise ValueError("invalid service contract")
    return version, methods


def _service_description(service, contract, method=""):
    version, methods = _service_contract(contract)
    actual_version = getattr(service, "service_api_version", 1)
    if type(actual_version) is not int or actual_version != version:
        raise ValueError("service business version mismatch")
    if method and method not in methods:
        raise PermissionError("service method not declared")
    for value in ([method] if method else methods):
        if not callable(getattr(service, value, None)):
            raise ValueError("declared service method is not callable")
    return {"service_api_version": actual_version, "methods": list(methods)}


def _request(value):
    """Validate the host envelope before decoding paths or copying state."""
    plugin_json.require_version(value)
    fields = {"rpc_version", "action", "name", "entry", "kind", "args", "kwargs",
              "service", "method", "caller", "service_contract"}
    def identity(item):
        return isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_]*", item) is not None
    if (set(value) != fields or value["action"] not in ("entry", "cli", "service.call", "service.describe")
            or value["kind"] not in ("python", "exec") or not identity(value["name"])
            or not identity(value["caller"]) or not isinstance(value["args"], list)
            or not isinstance(value["kwargs"], dict)):
        raise ValueError("invalid worker request")
    if value["kind"] == "python":
        if not identity(value["entry"]) or value["entry"] in {"core", "sys", "os", "yaml", "rich", "typer", "utils"}:
            raise ValueError("invalid worker entry")
    else:
        entry = value["entry"]
        if isinstance(entry, dict):
            if set(entry) - {"linux", "windows", "darwin"} or not all(isinstance(item, str) for item in entry.values()):
                raise ValueError("invalid worker entry")
            entry = entry.get("windows" if os.name == "nt" else sys.platform)
        if (not isinstance(entry, str) or not entry or "\x00" in entry or "\\" in entry
                or PurePosixPath(entry).is_absolute() or ".." in PurePosixPath(entry).parts):
            raise ValueError("invalid worker entry")
    if value["action"].startswith("service."):
        if value["kind"] != "python" or not identity(value["service"]):
            raise ValueError("invalid worker service")
        _, methods = _service_contract(value["service_contract"])
        if value["action"] == "service.call":
            if not isinstance(value["method"], str) or value["method"] not in methods:
                raise ValueError("invalid worker method")
        elif value["method"] != "" or value["args"] or value["kwargs"]:
            raise ValueError("invalid worker description")
    elif (value["service"] != "" or value["method"] != "" or value["service_contract"] is not None
          or value["kwargs"] or any(not isinstance(item, str) for item in value["args"])
          or (value["action"] == "entry" and value["args"])):
        raise ValueError("invalid worker action arguments")
    return value


def main() -> None:
    if os.environ.get("OPSKIT_SANDBOX") != "1":
        raise RuntimeError("plugin worker must run in a platform sandbox")
    request = _decode(_request(plugin_json.loads_message(Path(sys.argv[1]).read_text(encoding="utf-8"))))
    shutil.copytree(_seed_root(), _data_root(), dirs_exist_ok=True)
    (_data_root() / "state").mkdir(parents=True, exist_ok=True)
    _adapt_sdk(request["name"])
    sys.path.append(str(_plugin_root()))
    value = None
    try:
        if request["kind"] == "exec":
            entry = request["entry"]
            if isinstance(entry, dict):
                entry = entry.get("windows" if os.name == "nt" else sys.platform)
            path = (_plugin_root() / entry).resolve()
            path.relative_to(_plugin_root().resolve())
            value = subprocess.call([str(path), *request["args"]], cwd=str(_plugin_root()))
        else:
            module = importlib.import_module(request["entry"])
            Path(module.__file__).resolve().relative_to(_plugin_root().resolve())
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
                description = _service_description(service, request.get("service_contract"),
                                                   request.get("method", "") if action == "service.call" else "")
                if action == "service.describe":
                    value = description
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
        print(PREFIX + plugin_json.dumps_message({"type": "result", "value": value, "state": _pack_state()}, default=_value), flush=True)
    except SystemExit as exc:
        print(PREFIX + plugin_json.dumps_message({"type": "result", "value": int(exc.code or 0), "state": _pack_state()}), flush=True)
    except BaseException as exc:
        # Never emit exception values: they can contain cloud credentials.
        print(PREFIX + plugin_json.dumps_message({"type": "result", "error": type(exc).__name__}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
