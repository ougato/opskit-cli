"""Owned host reader for fresh content and authorization checks, never a permit cache."""
from pathlib import Path
from pathlib import PureWindowsPath
import json
import math
import multiprocessing
import re
import threading

REQUEST_LIMIT = 256 * 1024
MANIFEST_RESPONSE_LIMIT = 8 * 1024**2
PRIVATE_TARGET_COUNT = 256
PRIVATE_TARGET_LENGTH = 512
PRIVATE_TARGET_BYTES = 16384
NETWORK_AUTHORIZATION_COUNT = 256
NETWORK_AUTHORIZATION_BYTES = 64 * 1024
INVENTORY_ERRORS = {
    "entries": "plugin inventory entry limit exceeded",
    "manifests": "plugin inventory manifest limit exceeded",
    "linked": "linked plugin inventory root forbidden",
    "scan": "plugin inventory scan failed",
}


class ContentIntegrityError(RuntimeError):
    """A supplied checksum manifest does not match the inspected content."""


class StatePathError(ValueError):
    """A host file argument does not belong to the selected state partition."""


def _valid_resource_record_request(request):
    return (isinstance(request, dict) and set(request) == {"operation", "path"}
            and request["operation"] == "resource_record" and isinstance(request["path"], str)
            and 0 < len(request["path"]) <= 65536 and "\0" not in request["path"])


def _resource_record_result(request):
    from core.plugin_resources import _read
    try:
        return {"ok": True, "record": _read(Path(request["path"]))}
    except FileNotFoundError:
        return {"ok": False, "reason": "missing"}
    except OSError:
        return {"ok": False, "reason": "filesystem"}
    except (ValueError, RuntimeError):
        return {"ok": False, "reason": "invalid"}


def _resource_record_value(result, path):
    from core.plugin_resources import _record_value
    if result == {"ok": False, "reason": "missing"}:
        return "missing", None
    if result == {"ok": False, "reason": "filesystem"}:
        return "filesystem", None
    if result == {"ok": False, "reason": "invalid"}:
        return "invalid", None
    if (not isinstance(result, dict) or set(result) != {"ok", "record"}
            or result["ok"] is not True):
        raise RuntimeError("invalid resource journal inspection result")
    return "ok", _record_value(Path(path), result["record"])


def _valid_mount_path_request(request):
    return (isinstance(request, dict) and set(request) == {"operation", "path"}
            and request["operation"] == "mount_path" and isinstance(request["path"], str)
            and 0 < len(request["path"]) <= 65536 and "\0" not in request["path"])


def _mount_path_result(request):
    try:
        return {"ok": True, "path": str(Path(request["path"]).resolve())}
    except (OSError, ValueError, RuntimeError):
        return {"ok": False}


def _mount_path_value(result):
    if (not isinstance(result, dict) or set(result) != {"ok", "path"} or result["ok"] is not True
            or not isinstance(result["path"], str) or not 0 < len(result["path"]) <= 65536
            or "\0" in result["path"] or not Path(result["path"]).is_absolute()
            or ".." in Path(result["path"]).parts):
        raise RuntimeError("invalid plugin mount path result")
    return Path(result["path"])


def _valid_state_path_request(request):
    return (isinstance(request, dict) and set(request) == {"operation", "root", "path"}
            and request["operation"] == "state_path"
            and all(isinstance(request[key], str) and 0 < len(request[key]) <= 65536
                    and "\0" not in request[key] for key in ("root", "path")))


def _state_path_result(request):
    try:
        root = Path(request["root"]).resolve()
        path = Path(request["path"]).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError:
            return {"ok": False, "reason": "outside"}
        return {"ok": True, "path": "/data/" + relative.as_posix()}
    except (OSError, ValueError, RuntimeError):
        return {"ok": False}


def _state_path_value(result):
    from pathlib import PurePosixPath
    if result == {"ok": False, "reason": "outside"}:
        raise StatePathError("file path is outside the authorized plugin state")
    if (not isinstance(result, dict) or set(result) != {"ok", "path"} or result["ok"] is not True
            or not isinstance(result["path"], str) or not 0 < len(result["path"]) <= 65536
            or not result["path"].startswith("/data/") or "\0" in result["path"]
            or ".." in PurePosixPath(result["path"]).parts):
        raise RuntimeError("invalid plugin state path result")
    return result["path"]


def _inspect_result(source, verify):
    from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
    from core.plugin_trust import compute_fingerprint
    try:
        root = Path(source)
        if verify and verify_checksums(root) == CHECK_MISMATCH:
            return {"ok": False, "reason": "integrity"}
        fingerprint = compute_fingerprint(root)
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            return {"ok": False}
        return {"ok": True, "fingerprint": fingerprint}
    except (OSError, ValueError, RuntimeError):
        return {"ok": False}


def _inspect_worker(connection, source, verify):
    from network.worker import send
    try:
        send(connection, _inspect_result(source, verify))
    finally:
        connection.close()


def inspect_content(root, *, verify_checksums, timeout):
    from network.worker import run
    if type(verify_checksums) is not bool:
        raise ValueError("checksum inspection option must be boolean")
    result = run(_inspect_worker, (str(root), verify_checksums),
                 timeout=timeout, label="plugin content inspection")
    return _fingerprint(result)


def _fingerprint(result):
    if result == {"ok": False, "reason": "integrity"}:
        raise ContentIntegrityError("plugin content inspection failed")
    if (not isinstance(result, dict) or set(result) != {"ok", "fingerprint"} or result["ok"] is not True
            or not isinstance(result["fingerprint"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", result["fingerprint"])):
        raise RuntimeError("plugin content inspection failed")
    return result["fingerprint"]


def _valid_authorization_request(request):
    if (not isinstance(request, dict)
            or set(request) not in ({"operation", "config", "trust", "identities", "edges"},
                                    {"operation", "config", "trust", "identities", "edges", "networks"})
            or request["operation"] != "authorize"
            or any(not isinstance(request[key], str) or not 0 < len(request[key]) <= 65536
                   for key in ("config", "trust"))):
        return False
    identities, edges = request["identities"], request["edges"]
    if any(not isinstance(items, (list, tuple)) or len(items) > 256 for items in (identities, edges)):
        return False
    def text(value):
        return isinstance(value, str) and 0 < len(value) <= 128
    def digest(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    networks = request.get("networks", ())
    if not isinstance(networks, (list, tuple)) or len(networks) > NETWORK_AUTHORIZATION_COUNT:
        return False
    network_bytes = 0
    for item in networks:
        if (not isinstance(item, (list, tuple)) or len(item) != 2 or not text(item[0])
                or re.fullmatch(r"[a-z][a-z0-9_]*", item[0]) is None):
            return False
        try:
            if not isinstance(item[1], (list, tuple)):
                return False
            _config_setting({"plugin": {"sandbox": {"private_targets": {item[0]: list(item[1])}}}},
                            "private_targets", item[0])
        except (ValueError, RuntimeError):
            return False
        network_bytes += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        if network_bytes > NETWORK_AUTHORIZATION_BYTES:
            return False
    return (all(isinstance(item, (list, tuple)) and len(item) == 2
                and text(item[0]) and digest(item[1]) for item in identities)
            and all(isinstance(edge, (list, tuple)) and len(edge) == 7
                    and all(text(edge[index]) for index in (0, 2, 3, 6))
                    and digest(edge[1]) and digest(edge[4]) and type(edge[5]) is int
                    and 0 < edge[5] <= 2**31 - 1 for edge in edges))


def _authorization_result(request):
    from core.plugin_sandbox import _read_live_authorizations
    try:
        failure = _read_live_authorizations(request["identities"], request["edges"],
                                            config_path=request["config"], trust_path=request["trust"],
                                            networks=request.get("networks", ()))
        return {"ok": True, "failure": failure}
    except (OSError, ValueError, RuntimeError):
        return {"ok": False}


def _authorization_failure(result):
    if (not isinstance(result, dict) or set(result) != {"ok", "failure"}
            or result["ok"] is not True
            or result["failure"] not in (None, "settings", "identity", "service", "network")):
        raise RuntimeError("plugin authorization inspection failed")
    return result["failure"]


def _valid_state_quota_request(request):
    return (isinstance(request, dict) and set(request) == {"operation", "root"}
            and request["operation"] == "state_quota" and isinstance(request["root"], str)
            and 0 < len(request["root"]) <= 65536 and Path(request["root"]).is_absolute())


def _state_quota_result(request):
    from core.plugin_state import reserve_staging
    try:
        count, size = reserve_staging(Path(request["root"]))
        return {"ok": True, "count": count, "size": size}
    except (OSError, ValueError):
        return {"ok": False}


def _state_quota_value(result):
    from core.plugin_state import MAX_STAGING_DIRECTORIES, MAX_STAGING_BYTES, STAGING_RESERVATION_BYTES
    if (not isinstance(result, dict) or set(result) != {"ok", "count", "size"}
            or result["ok"] is not True or type(result["count"]) is not int
            or not 0 <= result["count"] < MAX_STAGING_DIRECTORIES
            or type(result["size"]) is not int
            or not 0 <= result["size"] <= MAX_STAGING_BYTES - STAGING_RESERVATION_BYTES):
        raise RuntimeError("plugin state staging quota inspection failed")
    return result["count"], result["size"]


def _valid_timeout_request(request):
    return (isinstance(request, dict) and set(request) == {"operation", "config"}
            and request["operation"] == "timeout" and isinstance(request["config"], str)
            and 0 < len(request["config"]) <= 65536)


def _timeout_result(request):
    from core.config import _load_config_path
    from core.plugin_sandbox import SandboxError, _sandbox_timeout
    try:
        return {"ok": True, "timeout": _sandbox_timeout(_load_config_path(Path(request["config"])))}
    except SandboxError as exc:
        reason = "timeout" if str(exc) == "invalid sandbox execution timeout" else "settings"
        return {"ok": False, "reason": reason}
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "reason": "settings"}


def _timeout_value(result):
    from core.plugin_sandbox import SandboxError, _sandbox_timeout
    if result == {"ok": False, "reason": "timeout"}:
        raise SandboxError("invalid sandbox execution timeout")
    if result == {"ok": False, "reason": "settings"}:
        raise SandboxError("invalid sandbox settings")
    if (not isinstance(result, dict) or set(result) != {"ok", "timeout"} or result["ok"] is not True
            or type(result["timeout"]) not in (int, float)):
        raise RuntimeError("invalid sandbox timeout inspection result")
    return _sandbox_timeout({"plugin": {"sandbox": {"timeout": result["timeout"]}}})


def _valid_setting_request(request):
    return (isinstance(request, dict)
            and set(request) == {"operation", "config", "field", "name"}
            and request["operation"] == "setting"
            and isinstance(request["config"], str) and 0 < len(request["config"]) <= 65536
            and ((request["field"] == "distribution" and request["name"] == "")
                 or (request["field"] in ("private_targets", "enabled") and isinstance(request["name"], str)
                     and len(request["name"]) <= 128
                     and re.fullmatch(r"[a-z][a-z0-9_]*", request["name"]) is not None)))


def _config_setting(config, field, name):
    from core.plugin_sandbox import SandboxError
    if field == "enabled":
        modules = config.get("modules", {})
        if not isinstance(modules, dict) or not isinstance(modules.get(name, {}), dict):
            raise SandboxError("invalid plugin authorization settings")
        return bool(modules.get(name, {}).get("enabled", True))
    plugin = config.get("plugin", {})
    if not isinstance(plugin, dict) or not isinstance(plugin.get("sandbox", {}), dict):
        raise SandboxError("invalid sandbox settings")
    settings = plugin.get("sandbox", {})
    if field == "distribution":
        value = settings.get("wsl_distribution", "Debian")
        if (not isinstance(value, str) or not 0 < len(value) <= 128
                or re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None):
            raise SandboxError("invalid WSL distribution")
        return value
    targets = settings.get("private_targets", {})
    if not isinstance(targets, dict):
        raise SandboxError("invalid private network grants")
    value = targets.get(name, [])
    if (not isinstance(value, list) or len(value) > PRIVATE_TARGET_COUNT
            or any(not isinstance(item, str) or not 0 < len(item) <= PRIVATE_TARGET_LENGTH for item in value)
            or len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > PRIVATE_TARGET_BYTES):
        raise SandboxError("invalid private network grants")
    return value


def _setting_result(request):
    from core.config import _load_config_path
    try:
        value = _config_setting(_load_config_path(Path(request["config"])),
                                request["field"], request["name"])
        return {"ok": True, "value": value}
    except (OSError, ValueError, RuntimeError):
        return {"ok": False}


def _setting_value(result, field, name):
    if not isinstance(result, dict) or set(result) != {"ok", "value"} or result["ok"] is not True:
        raise RuntimeError("invalid sandbox setting inspection result")
    if field == "enabled":
        if type(result["value"]) is not bool:
            raise RuntimeError("invalid plugin enablement inspection result")
        return result["value"]
    settings = ({"wsl_distribution": result["value"]} if field == "distribution"
                else {"private_targets": {name: result["value"]}})
    return _config_setting({"plugin": {"sandbox": settings}}, field, name)


def _valid_catalog_request(request):
    return (isinstance(request, dict) and set(request) == {"operation", "path"}
            and request["operation"] in ("catalog", "gateway_catalog") and isinstance(request["path"], str)
            and 0 < len(request["path"]) <= 65536)


def _catalog_result(request):
    from yaml import YAMLError
    from core.plugin_catalog import read_record, read_gateway_record
    try:
        read = read_gateway_record if request["operation"] == "gateway_catalog" else read_record
        return {"ok": True, "record": read(request["path"])}
    except (OSError, ValueError, RuntimeError, YAMLError):
        return {"ok": False}


def _catalog_value(result, *, gateway=False):
    from core.plugin_catalog import validate_record, validate_gateway_record
    if not isinstance(result, dict) or set(result) != {"ok", "record"} or result["ok"] is not True:
        raise RuntimeError("plugin runtime catalog inspection failed")
    validate = validate_gateway_record if gateway else validate_record
    record = validate(result["record"])
    if record != result["record"]:
        raise RuntimeError("invalid runtime catalog inspection result")
    return record


def _valid_manifest_request(request):
    return (isinstance(request, dict) and set(request) == {"operation", "root"}
            and request["operation"] == "manifest" and isinstance(request["root"], str)
            and 0 < len(request["root"]) <= 65536)


def _manifest_record(manifest):
    from dataclasses import asdict
    record = asdict(manifest)
    record.pop("root")
    return record


def _send_record(connection, view, requested, record):
    from network.worker import send
    try:
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if not 0 < len(encoded) <= MANIFEST_RESPONSE_LIMIT:
            raise ValueError("manifest response exceeds limit")
    except (OSError, ValueError, TypeError, RuntimeError):
        send(connection, {"ok": False})
        return False
    send(connection, {"ok": True, "size": len(encoded)})
    for offset in range(0, len(encoded), REQUEST_LIMIT):
        chunk = encoded[offset:offset + REQUEST_LIMIT]
        view[:len(chunk)] = chunk
        send(connection, {"offset": offset, "size": len(chunk)})
        if offset + len(chunk) < len(encoded):
            requested.wait()
            requested.clear()
        # No final acknowledgement: it could consume the next request's event.
    return True


def _send_manifest(connection, view, requested, root):
    from core.plugin import load_manifest
    from network.worker import send
    try:
        manifest = load_manifest(Path(root))
        if manifest is None:
            raise ValueError("invalid plugin manifest")
        record = _manifest_record(manifest)
    except (OSError, ValueError, TypeError, RuntimeError):
        send(connection, {"ok": False})
        return
    _send_record(connection, view, requested, record)


def _valid_inventory_request(request):
    return (isinstance(request, dict) and set(request) == {"operation", "root", "select", "value"}
            and request["operation"] == "inventory" and isinstance(request["root"], str)
            and 0 < len(request["root"]) <= 65536 and isinstance(request["select"], str)
            and request["select"] in ("all", "name", "service") and isinstance(request["value"], str)
            and (request["value"] == "" if request["select"] == "all"
                 else 0 < len(request["value"]) <= 65536 and re.fullmatch(r"[a-z][a-z0-9_]*", request["value"]) is not None))


def _send_inventory(connection, view, requested, request):
    from core.plugin import _list_manifests
    from network.worker import send
    try:
        manifests = _list_manifests(Path(request["root"]), select=request["select"], value=request["value"])
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        reason = next((key for key, message in INVENTORY_ERRORS.items() if str(exc) == message), "scan")
        send(connection, {"ok": False, "reason": reason})
        return
    send(connection, {"ok": True, "count": len(manifests)})
    for index, manifest in enumerate(manifests):
        if not _send_record(connection, view, requested,
                            {"directory": manifest.root.name, "manifest": _manifest_record(manifest)}):
            return
        if index + 1 < len(manifests):
            requested.wait()
            requested.clear()


def _manifest_value(record, root):
    from dataclasses import asdict, fields
    from core.plugin import PluginManifest, _parse_manifest
    if not isinstance(record, dict) or set(record) != {f.name for f in fields(PluginManifest)} - {"root"}:
        raise RuntimeError("invalid plugin manifest inspection result")
    manifest = _parse_manifest(record, Path(root))
    if manifest is None:
        raise RuntimeError("invalid plugin manifest inspection result")
    normalized = asdict(manifest)
    normalized.pop("root")
    # Compare JSON as well: bool and integer must not compare as equal.
    if json.dumps(normalized, sort_keys=True, allow_nan=False) != json.dumps(record, sort_keys=True, allow_nan=False):
        raise RuntimeError("invalid plugin manifest inspection result")
    return manifest


def _inspection_session(connection, buffer, length, requested):
    """Fixed memory mailbox: submitting work cannot block on a pipe writer."""
    from network.worker import send
    view = memoryview(buffer).cast("B")
    try:
        while True:
            requested.wait()
            requested.clear()
            size = length.value
            if not 0 < size <= REQUEST_LIMIT:
                send(connection, {"ok": False})
                continue
            try:
                request = json.loads(bytes(view[:size]))
                if _valid_resource_record_request(request):
                    send(connection, _resource_record_result(request))
                    continue
                if _valid_mount_path_request(request):
                    send(connection, _mount_path_result(request))
                    continue
                if _valid_state_path_request(request):
                    send(connection, _state_path_result(request))
                    continue
                if _valid_state_quota_request(request):
                    send(connection, _state_quota_result(request))
                    continue
                if _valid_inventory_request(request):
                    _send_inventory(connection, view, requested, request)
                    continue
                if _valid_manifest_request(request):
                    _send_manifest(connection, view, requested, request["root"])
                    continue
                if _valid_catalog_request(request):
                    send(connection, _catalog_result(request))
                    continue
                if _valid_timeout_request(request):
                    send(connection, _timeout_result(request))
                    continue
                if _valid_setting_request(request):
                    send(connection, _setting_result(request))
                    continue
                if _valid_authorization_request(request):
                    send(connection, _authorization_result(request))
                    continue
                if (not isinstance(request, dict) or set(request) != {"source", "verify"}
                        or not isinstance(request["source"], str) or type(request["verify"]) is not bool):
                    raise ValueError("invalid inspection request")
            except (ValueError, TypeError):
                send(connection, {"ok": False})
                continue
            send(connection, _inspect_result(request["source"], request["verify"]))
    finally:
        view.release()
        connection.close()


class ContentInspector:
    """One lazy, owned reader per invocation, with no cached content or permit."""

    def __init__(self):
        self._thread = threading.get_ident()
        self._owner = self._receive = self._process = None
        self._buffer = self._length = self._requested = None
        self._closed = False
        self._busy = False

    @property
    def pid(self):
        return self._process.pid if self._process is not None else None

    def _start(self):
        if self._owner is not None:
            return
        from network.worker import owned_process
        context = multiprocessing.get_context("spawn")
        self._buffer = context.Array("B", REQUEST_LIMIT, lock=False)
        self._length = context.Value("I", 0, lock=False)
        self._requested = context.Event()
        owner = owned_process(_inspection_session, (self._buffer, self._length, self._requested),
                              label="plugin content inspection")
        self._receive, self._process = owner.__enter__()
        self._owner = owner

    def inspect(self, root, *, verify_checksums, timeout):
        if type(verify_checksums) is not bool:
            raise ValueError("invalid content inspection allowance")
        source = str(root)
        if len(source) > 65536:
            raise ValueError("plugin inspection path exceeds limit")
        return self._exchange({"source": source, "verify": verify_checksums}, timeout, _fingerprint)

    def authorize(self, config_path, trust_path, identities, edges, *, timeout, networks=()):
        request = {"operation": "authorize", "config": str(config_path), "trust": str(trust_path),
                   "identities": identities, "edges": edges}
        if networks != ():
            request["networks"] = networks
        if not _valid_authorization_request(request):
            raise ValueError("invalid plugin authorization inspection request")
        return self._exchange(request, timeout, _authorization_failure)

    def state_quota(self, root, *, timeout):
        request = {"operation": "state_quota", "root": str(root)}
        if not _valid_state_quota_request(request):
            raise ValueError("invalid state quota inspection request")
        return self._exchange(request, timeout, _state_quota_value)

    def resource_record(self, path, *, timeout):
        request = {"operation": "resource_record", "path": str(path)}
        if not _valid_resource_record_request(request):
            raise ValueError("invalid resource journal inspection request")
        status, record = self._exchange(request, timeout, lambda result: _resource_record_value(result, path))
        if status == "missing":
            raise FileNotFoundError("resource journal no longer exists")
        if status == "filesystem":
            raise OSError("resource journal inspection failed")
        if status == "invalid":
            raise ValueError("invalid resource journal record")
        return record

    def mount_path(self, path, *, timeout):
        request = {"operation": "mount_path", "path": str(path)}
        if not _valid_mount_path_request(request):
            raise ValueError("invalid mount path inspection request")
        return self._exchange(request, timeout, _mount_path_value)

    def state_path(self, root, path, *, timeout):
        request = {"operation": "state_path", "root": str(root), "path": str(path)}
        if not _valid_state_path_request(request):
            raise ValueError("invalid state path inspection request")
        return self._exchange(request, timeout, _state_path_value)

    def execution_timeout(self, config_path, *, timeout):
        request = {"operation": "timeout", "config": str(config_path)}
        if not _valid_timeout_request(request):
            raise ValueError("invalid sandbox timeout inspection request")
        return self._exchange(request, timeout, _timeout_value)

    def config_setting(self, config_path, field, name, *, timeout):
        request = {"operation": "setting", "config": str(config_path), "field": field, "name": name}
        if not _valid_setting_request(request):
            raise ValueError("invalid sandbox setting inspection request")
        return self._exchange(request, timeout, lambda result: _setting_value(result, field, name))

    def runtime_catalog(self, path, *, timeout, gateway=False):
        if type(gateway) is not bool:
            raise ValueError("invalid runtime catalog kind")
        request = {"operation": "gateway_catalog" if gateway else "catalog", "path": str(path)}
        if not _valid_catalog_request(request):
            raise ValueError("invalid runtime catalog inspection request")
        return self._exchange(request, timeout, lambda result: _catalog_value(result, gateway=gateway))

    def manifest(self, root, *, timeout):
        request = {"operation": "manifest", "root": str(root)}
        if not _valid_manifest_request(request):
            raise ValueError("invalid plugin manifest inspection request")
        return self._exchange(request, timeout, lambda record: _manifest_value(record, root), chunks=True)

    def inventory(self, root, *, timeout, select="all", value=""):
        request = {"operation": "inventory", "root": str(root), "select": select, "value": value}
        if not _valid_inventory_request(request):
            raise ValueError("invalid plugin inventory inspection request")
        return self._exchange(request, timeout, lambda records: records, inventory=request)

    def _receive_inventory(self, result, deadline, request):
        from core.plugin import MAX_MANIFESTS, PluginInventoryError
        from network import worker
        if (isinstance(result, dict) and set(result) == {"ok", "reason"} and result["ok"] is False
                and isinstance(result["reason"], str) and result["reason"] in INVENTORY_ERRORS):
            raise PluginInventoryError(INVENTORY_ERRORS[result["reason"]])
        if (not isinstance(result, dict) or set(result) != {"ok", "count"} or result["ok"] is not True
                or type(result["count"]) is not int or not 0 <= result["count"] <= MAX_MANIFESTS):
            raise RuntimeError("invalid plugin inventory inspection result")
        manifests, directories = [], set()
        for index in range(result["count"]):
            header = worker.wait_result(self._receive, deadline=deadline, label="plugin inventory inspection")
            record = self._receive_manifest(header, deadline)
            if not isinstance(record, dict) or set(record) != {"directory", "manifest"}:
                raise RuntimeError("invalid plugin inventory inspection item")
            directory = record["directory"]
            if (not isinstance(directory, str) or not directory or directory.startswith((".", "_"))
                    or any(char in directory for char in ("/", "\\", "\0"))
                    or PureWindowsPath(directory).drive or directory in directories):
                raise RuntimeError("invalid plugin inventory directory identity")
            directories.add(directory)
            manifest = _manifest_value(record["manifest"], Path(request["root"]) / directory)
            if (request["select"] == "name" and manifest.name != request["value"]
                    or request["select"] == "service" and request["value"] not in manifest.provides):
                raise RuntimeError("invalid plugin inventory selection")
            manifests.append(manifest)
            if index + 1 < result["count"]:
                self._requested.set()
        return manifests

    def _receive_manifest(self, result, deadline):
        from network import worker
        if (not isinstance(result, dict) or set(result) != {"ok", "size"} or result["ok"] is not True
                or type(result["size"]) is not int or not 0 < result["size"] <= MANIFEST_RESPONSE_LIMIT):
            raise RuntimeError("plugin manifest inspection failed")
        content = bytearray()
        while len(content) < result["size"]:
            part = worker.wait_result(self._receive, deadline=deadline, label="plugin manifest inspection")
            expected_size = min(REQUEST_LIMIT, result["size"] - len(content))
            if (not isinstance(part, dict) or set(part) != {"offset", "size"}
                    or type(part["offset"]) is not int or part["offset"] != len(content)
                    or type(part["size"]) is not int or part["size"] != expected_size):
                raise RuntimeError("invalid plugin manifest inspection chunk")
            view = memoryview(self._buffer).cast("B")
            try:
                content.extend(view[:expected_size])
            finally:
                view.release()
            if len(content) < result["size"]:
                self._requested.set()
        return json.loads(content)

    def _exchange(self, request, timeout, decode, *, chunks=False, inventory=None):
        from network import worker
        if self._closed or self._busy or threading.get_ident() != self._thread:
            raise RuntimeError("plugin content inspector is closed or belongs to another thread")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid content inspection allowance")
        deadline = worker.time.monotonic() + timeout
        encoded = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(encoded) > REQUEST_LIMIT:
            raise ValueError("plugin inspection request exceeds limit")
        self._busy = True
        try:
            self._start()
            if worker.time.monotonic() >= deadline:
                raise TimeoutError("plugin content inspection deadline exceeded")
            view = memoryview(self._buffer).cast("B")
            try:
                view[:len(encoded)] = encoded
            finally:
                view.release()
            self._length.value = len(encoded)
            self._requested.set()
            result = worker.wait_result(self._receive, deadline=deadline, label="plugin content inspection")
            if inventory is not None:
                result = self._receive_inventory(result, deadline, inventory)
            elif chunks:
                result = self._receive_manifest(result, deadline)
            value = decode(result)
            if worker.time.monotonic() >= deadline:
                raise TimeoutError("plugin content inspection deadline exceeded")
            return value
        except BaseException:
            self.close()
            raise
        finally:
            self._busy = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._owner is not None:
                self._owner.__exit__(None, None, None)
        finally:
            self._owner = self._receive = self._process = None
            self._buffer = self._length = self._requested = None
