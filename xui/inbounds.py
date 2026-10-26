"""Reconcile the OpsKit-owned inbound without replacing client identities."""
from __future__ import annotations

from copy import deepcopy
import json
import time
import urllib.error

from xui.constants import PANEL_API_RETRY_COUNT, PANEL_API_RETRY_DELAY_SECONDS
from xui.panel import Panel, PanelError
from xui.templates import to_xui_api_payload


def decode_inbound(row: dict) -> dict:
    value = deepcopy(row)
    try:
        for key in ("settings", "streamSettings", "sniffing"):
            item = value.get(key, {})
            value[key] = json.loads(item) if isinstance(item, str) else item
            if not isinstance(value[key], dict):
                raise ValueError()
    except (ValueError, TypeError):
        raise PanelError("XUI stored inbound configuration is invalid") from None
    return value


def reconcile(rows: list[dict], desired: dict) -> tuple[dict, int | None]:
    matches = [row for row in rows if row.get("remark") == desired["remark"]]
    if len(matches) > 1:
        raise PanelError("XUI has multiple OpsKit inbounds; resolve ambiguous ownership in panel")
    existing = decode_inbound(matches[0]) if matches else None
    if any(row.get("port") == desired["port"] and row not in matches for row in rows):
        raise PanelError("XUI inbound port conflict; another inbound owns the requested port")
    if existing is None:
        for row in rows:
            clients = decode_inbound(row)["settings"].get("clients", [])
            if any(client.get("email") == desired["remark"] for client in clients):
                raise PanelError("XUI client email belongs to another inbound; resolve conflict in panel")
        return deepcopy(desired), None
    stream = existing["streamSettings"]
    if existing.get("protocol") != "vless" or stream.get("security") != "reality" or stream.get("network") != "tcp":
        raise PanelError("XUI existing OpsKit inbound is not VLESS Reality TCP; refusing to replace it")
    clients = existing["settings"].get("clients", [])
    owned = [client for client in clients if client.get("email") == desired["remark"]]
    if len(owned) != 1 or not owned[0].get("id"):
        raise PanelError("XUI existing OpsKit client identity is missing or ambiguous")
    owned[0].update(enable=True, totalGB=0, expiryTime=0, limitIp=0, flow="xtls-rprx-vision")
    connection_values(existing)
    existing.update(enable=True, total=0, expiryTime=0, port=desired["port"])
    inbound_id = existing.get("id")
    if type(inbound_id) is not int or inbound_id <= 0:
        raise PanelError("XUI existing inbound ID is invalid")
    return existing, inbound_id


def api_payload(inbound: dict) -> dict:
    payload = to_xui_api_payload(inbound)
    # Never reset usage or change the listening interface while repairing limits.
    for key in ("up", "down", "listen", "tag", "allocate"):
        if key in inbound:
            payload[key] = inbound[key]
    if "id" in inbound:
        payload["id"] = inbound["id"]
    return payload


def ensure_inbound(port: int, username: str, password: str, base_path: str, desired: dict) -> dict:
    for attempt in range(PANEL_API_RETRY_COUNT):
        try:
            panel = Panel(port, username, password, base_path)
            actual, inbound_id = reconcile(panel.list_inbounds(), desired)
            payload = api_payload(actual)
            panel.request("/add" if inbound_id is None else f"/update/{inbound_id}", payload)
            # 3.11 keeps client edits separate from inbound edits. In older
            # releases this endpoint is called updateClient under inbounds.
            saved_rows = [row for row in panel.list_inbounds() if row.get("remark") == desired["remark"]]
            if len(saved_rows) != 1:
                raise PanelError("XUI inbound could not be verified after save")
            saved_id = saved_rows[0].get("id")
            if type(saved_id) is not int or saved_id <= 0:
                raise PanelError("XUI saved inbound ID is invalid")
            client = next(c for c in actual["settings"]["clients"] if c["email"] == desired["remark"])
            panel.update_client(saved_id, client)
            if not saved_rows[0].get("enable"):
                panel.request(f"/setEnable/{saved_id}", {"enable": True})
            # Re-read authoritative configuration. A successful HTTP response is insufficient.
            saved_rows = [row for row in panel.list_inbounds() if row.get("remark") == desired["remark"]]
            if len(saved_rows) != 1:
                raise PanelError("XUI inbound could not be verified after save")
            saved = decode_inbound(saved_rows[0])
            if any(saved.get(key) != payload.get(key) for key in
                   ("port", "protocol", "enable", "total", "expiryTime")):
                raise PanelError("XUI inbound settings did not take effect")
            if saved["streamSettings"] != actual["streamSettings"]:
                raise PanelError("XUI Reality settings did not take effect")
            wanted_clients = actual["settings"]["clients"]
            saved_clients = saved["settings"].get("clients", [])
            if len(wanted_clients) != len(saved_clients):
                raise PanelError("XUI clients did not take effect")
            by_email = {c.get("email"): c for c in saved_clients}
            for wanted in wanted_clients:
                persisted = by_email.get(wanted.get("email"), {})
                # The panel owns timestamps and may add new defaults to clients.
                if any(persisted.get(key) != value for key, value in wanted.items()
                       if key not in {"created_at", "updated_at", "createdAt", "updatedAt"}
                       and not (key == "subId" and not value)):
                    raise PanelError("XUI client settings did not take effect")
            return saved
        except PanelError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            # A lost response may follow a successful mutation. Re-list before retrying.
            if attempt + 1 == PANEL_API_RETRY_COUNT:
                raise PanelError("XUI panel API unavailable after retries; check service logs and panel address") from None
            time.sleep(PANEL_API_RETRY_DELAY_SECONDS)
    raise AssertionError("unreachable")


def connection_values(inbound: dict) -> dict:
    try:
        client = next(client for client in inbound["settings"]["clients"]
                      if client["email"] == inbound["remark"])
        reality = inbound["streamSettings"]["realitySettings"]
        values = {"uuid": client["id"], "private_key": reality["privateKey"],
                  "short_id": reality["shortIds"][0], "sni": reality["serverNames"][0],
                  "dest": reality.get("target") or reality["dest"]}
        if any(not isinstance(value, str) or not value for key, value in values.items() if key != "short_id"):
            raise ValueError()
        return values
    except (KeyError, IndexError, TypeError, StopIteration, ValueError):
        raise PanelError("XUI existing Reality connection parameters are incomplete") from None
