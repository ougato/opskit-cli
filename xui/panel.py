"""Authenticated loopback panel API; transport errors and rejections stay distinct."""
from __future__ import annotations

import json
import urllib.error
from urllib.parse import quote
import urllib.request

from software.base import InstallError
from xui.constants import HTTP_TIMEOUT_SECONDS, OPSKIT_USER_AGENT
from xui.utils import login_panel, panel_api_base


class PanelError(InstallError):
    pass


class PanelHTTPError(PanelError):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"XUI panel API HTTP {code}; check authentication and API compatibility")


class Panel:
    def __init__(self, port: int, username: str, password: str, base_path: str):
        self.base = panel_api_base(port, base_path)
        self.cookie, self.csrf = login_panel(port, username, password, base_path)
        if not self.cookie or not self.csrf:
            raise PanelError("XUI panel authentication failed; check panel credentials")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path: str, payload: dict | None = None):
        request = urllib.request.Request(
            self.base + "/panel/api" + (path if path.startswith("/clients/") else "/inbounds" + path),
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", "Cookie": self.cookie,
                     "X-CSRF-Token": self.csrf, "X-Requested-With": "XMLHttpRequest",
                     "User-Agent": OPSKIT_USER_AGENT},
            method="POST" if payload is not None else "GET",
        )
        try:
            with self.opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise PanelHTTPError(exc.code) from None
            raise
        except ValueError:
            raise PanelError("XUI panel returned invalid JSON; check panel API compatibility") from None
        if not isinstance(data, dict) or data.get("success") is not True:
            # Provider messages can contain credentials. Expose only known reasons.
            message = str(data.get("msg", "")).lower() if isinstance(data, dict) else ""
            reason = "request rejected; check x-ui service logs"
            if "duplicate email" in message:
                reason = "duplicate client email; check existing inbounds"
            elif "port" in message:
                reason = "inbound port conflict; check existing listeners"
            raise PanelError("XUI panel API " + reason)
        return data.get("obj")

    def list_inbounds(self) -> list[dict]:
        rows = self.request("/list")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise PanelError("XUI panel returned invalid inbound list")
        return rows

    def update_client(self, inbound_id: int, client: dict) -> None:
        try:
            self.request("/clients/update/" + quote(client["email"], safe=""), client)
        except PanelHTTPError as exc:
            if exc.code != 404:
                raise
            # Older 3x-ui releases keep client edits under the inbound API.
            self.request("/updateClient/" + quote(client["id"], safe=""),
                         {"id": inbound_id, "settings": json.dumps({"clients": [client]})})
