"""Read-only XUI checks, without menu rendering or connection exports."""
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing


def _state_worker(connection, path):
    import os
    import stat
    from network.worker import send
    state = {}
    try:
        # The parent can terminate blocked filesystem access. Only regular,
        # bounded JSON files are accepted, and secrets never leave this worker.
        with open(path, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError()
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        value = json.loads(raw)
        if isinstance(value, dict):
            state = {key: value[key] for key in ("panel_port", "panel_base_path") if key in value}
            inbound = value.get("vless")
            if isinstance(inbound, dict):
                state["vless"] = {key: inbound[key] for key in ("port", "local_port") if key in inbound}
    except (OSError, ValueError):
        pass
    finally:
        send(connection, {"state": state})
        connection.close()


def read_state(*, timeout):
    from xui.constants import XUI_STATE_FILE
    from network.worker import run
    return run(_state_worker, (str(XUI_STATE_FILE),), timeout=timeout,
               label="XUI diagnostic state")["state"]


def _valid_panel(port, base_path):
    return (type(port) is int and 1 <= port <= 65535 and isinstance(base_path, str)
            and len(base_path) <= 1024 and bool(re.fullmatch(r"/?[A-Za-z0-9_/-]*", base_path)))


def _api_worker(connection, port, base_path):
    import httpx
    from network.worker import send
    healthy = False
    try:
        # Fixed loopback target; state cannot redirect probes or expose credentials.
        with httpx.Client(timeout=2, follow_redirects=False, trust_env=False,
                          headers={"Accept-Encoding": "identity"}) as client:
            with client.stream("GET", f"http://127.0.0.1:{port}{base_path}/csrf-token") as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError()
                body = bytearray()
                for chunk in response.iter_raw():
                    if len(body) + len(chunk) > 65536:
                        raise ValueError()
                    body.extend(chunk)
                data = json.loads(body)
                healthy = isinstance(data, dict) and data.get("success") is True and isinstance(data.get("obj"), str) and bool(data["obj"])
    except (httpx.HTTPError, ValueError):
        pass
    finally:
        send(connection, {"ok": healthy})
        connection.close()


def api_available(port, base_path, timeout=3):
    from network.worker import run
    if not _valid_panel(port, base_path):
        return False
    base_path = ("/" + base_path.strip("/")) if base_path.strip("/") else ""
    try:
        return run(_api_worker, (port, base_path), timeout=timeout, label="XUI API")["ok"] is True
    except (OSError, RuntimeError):
        return False


def _inbound_configured(path, port, *, timeout=1):
    # mode=ro is essential: diagnostics must never create a missing database.
    try:
        from core.deadline import Deadline
        budget = Deadline(timeout, "XUI database")
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=budget.remaining())) as database:
            import time
            database.set_progress_handler(lambda: int(time.monotonic() >= budget.end), 1000)
            rows = database.execute("select protocol, enable, stream_settings from inbounds where port = ? limit 2", (port,)).fetchall()
            budget.remaining()
            if len(rows) != 1:
                return False
            protocol, enabled, stream_settings = rows[0]
            if not isinstance(stream_settings, str) or len(stream_settings) > 65536:
                return False
            settings = json.loads(stream_settings)
            return protocol == "vless" and enabled == 1 and isinstance(settings, dict) and settings.get("security") == "reality"
    except (sqlite3.Error, OSError, ValueError):
        return False


def report(*, timeout=30):
    from xui import utils, constants
    from core.diagnostics import listener_checks
    from core.deadline import Deadline
    budget = Deadline(timeout, "XUI diagnostics")
    state = budget.call(read_state, timeout=budget.remaining())
    checks = [{"name": "version", "ok": bool(budget.call(utils.detect_xui_version, timeout=budget.remaining(), strict=True))},
              {"name": "systemd", "ok": budget.call(utils.systemd_available)},
              {"name": "service", "ok": budget.call(utils.is_service_active, timeout=budget.remaining())},
              {"name": "state", "ok": bool(state)}]
    port = state.get("panel_port")
    base_path = state.get("panel_base_path", "")
    inbound = state.get("vless", {})
    inbound_port = inbound.get("local_port", inbound.get("port")) if isinstance(inbound, dict) else None
    valid = (_valid_panel(port, base_path)
             and type(inbound_port) is int and 1 <= inbound_port <= 65535 and port != inbound_port)
    checks.append({"name": "state_config", "ok": valid})
    if not valid:
        return {"checks": checks}
    base_path = ("/" + base_path.strip("/")) if base_path.strip("/") else ""
    effective = budget.call(utils.get_panel_settings, timeout=budget.remaining())
    checks.append({"name": "panel_config", "ok": effective.get("port") == port
                   and str(effective.get("base_path", "")).strip("/") == base_path.strip("/")})
    checks.extend(budget.call(listener_checks, [("tcp", port, Path(constants.XUI_BINARY_COMMAND))], timeout=budget.remaining()))
    checks.append({"name": "vless_database", "ok": budget.call(_inbound_configured, constants.XUI_DATABASE_FILE, inbound_port, timeout=budget.remaining())})
    checks.append({"name": "api_csrf", "ok": budget.call(api_available, port, base_path, timeout=budget.remaining(cap=3))})
    return {"checks": checks}
