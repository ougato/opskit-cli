"""x-ui 自动化工具函数。"""
from __future__ import annotations

import http.cookiejar
import json
import os
import secrets
import shutil
import stat
import subprocess
import time
import tempfile
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from core.constants import PUBLIC_IP_APIS
from xui.constants import (
    HTTP_CONTENT_TYPE_FORM,
    HTTP_CONTENT_TYPE_JSON,
    HTTP_HEADER_CONTENT_TYPE,
    HTTP_HEADER_COOKIE,
    HTTP_HEADER_USER_AGENT,
    HTTP_HEADER_X_CSRF_TOKEN,
    HTTP_HEADER_X_REQUESTED_WITH,
    HTTP_STATUS_OK,
    HTTP_STATUS_REDIRECT_MAX,
    HTTP_STATUS_REDIRECT_MIN,
    HTTP_TIMEOUT_SECONDS,
    LOOPBACK_HOST,
    INSTALL_SCRIPT_TIMEOUT,
    OPSKIT_USER_AGENT,
    PANEL_API_RETRY_COUNT,
    PANEL_API_RETRY_DELAY_SECONDS,
    PASSWORD_BYTES,
    REDACTED_VALUE,
    SENSITIVE_STATE_KEYS,
    SERVICE_RESTART_TIMEOUT,
    SHORT_ID_BYTES,
    SS_COMMAND,
    SS_TCP_LISTEN_ARGS,
    SYSTEMCTL_COMMAND,
    HTTP_URL_TEMPLATE,
    HTTP_VALUE_XMLHTTPREQUEST,
    XHTTP_PATH_SUFFIX_BYTES,
    BASH_COMMAND,
    DEBIAN_FRONTEND_ENV,
    DEBIAN_FRONTEND_NONINTERACTIVE,
    XUI_API_ADD_INBOUND_PATH,
    XUI_API_CSRF_PATH,
    XUI_API_LOGIN_PATH,
    XUI_BINARY_COMMAND,
    XUI_COMMAND,
    XUI_SETTING_PASSWORD_ARG,
    XUI_SETTING_PORT_ARG,
    XUI_SETTING_SUBCOMMAND,
    XUI_SETTING_USERNAME_ARG,
    XUI_SETTING_WEB_BASE_PATH_ARG,
    XUI_INSTALLED_VERSION,
    XUI_INSTALL_SCRIPT_URL,
    XUI_INSTALL_SCRIPT_INPUT,
    XUI_SERVICE,
    XUI_STATE_FILE,
    XUI_XRAY_CANDIDATES,
    XUI_COOKIE_SEPARATOR,
    XUI_CSRF_META_MARKER,
)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None or Path(name).exists()


def gen_uuid() -> str:
    return str(uuid.uuid4())


def gen_short_id() -> str:
    return secrets.token_hex(SHORT_ID_BYTES)


def gen_password() -> str:
    return secrets.token_urlsafe(PASSWORD_BYTES)


def gen_xhttp_path(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(XHTTP_PATH_SUFFIX_BYTES)}"


def detect_public_host() -> str:
    for url in PUBLIC_IP_APIS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": OPSKIT_USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                text = resp.read().decode("utf-8").strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def is_service_active(service: str = XUI_SERVICE) -> bool:
    try:
        result = subprocess.run(
            [SYSTEMCTL_COMMAND, "is-active", service],
            capture_output=True,
            text=True,
            check=False,
            timeout=SERVICE_RESTART_TIMEOUT,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def is_port_listening(port: int) -> bool:
    try:
        result = subprocess.run(
            [SS_COMMAND, SS_TCP_LISTEN_ARGS],
            capture_output=True,
            text=True,
            check=False,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        return f":{port}" in result.stdout
    except Exception:
        return False


def detect_xui_version() -> str | None:
    if is_service_active() or command_exists(XUI_COMMAND):
        try:
            result = subprocess.run(
                [XUI_COMMAND, "version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            output = (result.stdout or result.stderr).strip()
            if result.returncode == 0 and output:
                return output.splitlines()[0]
        except Exception:
            pass
        return XUI_INSTALLED_VERSION
    return None


def install_xui_script() -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as script:
        script_path = Path(script.name)
    try:
        with urllib.request.urlopen(XUI_INSTALL_SCRIPT_URL, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            script_path.write_bytes(resp.read())
        script_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        subprocess.run(
            [BASH_COMMAND, str(script_path)],
            input=XUI_INSTALL_SCRIPT_INPUT,
            text=True,
            check=True,
            capture_output=True,
            env={**os.environ, DEBIAN_FRONTEND_ENV: DEBIAN_FRONTEND_NONINTERACTIVE},
            timeout=INSTALL_SCRIPT_TIMEOUT,
        )
    finally:
        script_path.unlink(missing_ok=True)


def generate_reality_keypair() -> tuple[str, str]:
    for candidate in XUI_XRAY_CANDIDATES:
        if not command_exists(candidate):
            continue
        result = subprocess.run(
            [candidate, "x25519"],
            capture_output=True,
            text=True,
            check=False,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            continue
        private_key = ""
        public_key = ""
        for line in result.stdout.splitlines():
            lower = line.lower()
            if "private" in lower:
                private_key = line.split(":", 1)[-1].strip()
            if "public" in lower:
                public_key = line.split(":", 1)[-1].strip()
        if private_key and public_key:
            return private_key, public_key
    return "", ""


def write_secret_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def load_state(path: Path = XUI_STATE_FILE) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text)
    except Exception:
        return {}
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            result[str(key)] = item
        return result
    return {}


def redact_state(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in SENSITIVE_STATE_KEYS:
                redacted[key_text] = REDACTED_VALUE
            else:
                redacted[key_text] = redact_state(item)
        return redacted
    if isinstance(value, list):
        return [redact_state(item) for item in value]
    return value


def panel_api_base(port: int, base_path: str) -> str:
    normalized = base_path.strip()
    if normalized and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    normalized = normalized.rstrip("/")
    return HTTP_URL_TEMPLATE.format(host=LOOPBACK_HOST, port=port, base_path=normalized)


def configure_panel_settings(
    *,
    port: int,
    username: str,
    password: str,
    base_path: str,
) -> bool:
    if not command_exists(XUI_BINARY_COMMAND):
        return False
    result = subprocess.run(
        [
            XUI_BINARY_COMMAND,
            XUI_SETTING_SUBCOMMAND,
            XUI_SETTING_USERNAME_ARG,
            username,
            XUI_SETTING_PASSWORD_ARG,
            password,
            XUI_SETTING_PORT_ARG,
            str(port),
            XUI_SETTING_WEB_BASE_PATH_ARG,
            base_path.strip(),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, DEBIAN_FRONTEND_ENV: DEBIAN_FRONTEND_NONINTERACTIVE},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    return result.returncode == 0


def _extract_csrf_token(text: str) -> str:
    marker = XUI_CSRF_META_MARKER
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find('"', start)
    if end < 0:
        return ""
    return text[start:end]


def _cookie_header(jar: http.cookiejar.CookieJar) -> str:
    return XUI_COOKIE_SEPARATOR.join(f"{cookie.name}={cookie.value}" for cookie in jar)


def _post_json(url: str, payload: dict[str, object], cookie: str, csrf_token: str) -> bool:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            HTTP_HEADER_CONTENT_TYPE: HTTP_CONTENT_TYPE_JSON,
            HTTP_HEADER_COOKIE: cookie,
            HTTP_HEADER_USER_AGENT: OPSKIT_USER_AGENT,
            HTTP_HEADER_X_REQUESTED_WITH: HTTP_VALUE_XMLHTTPREQUEST,
            HTTP_HEADER_X_CSRF_TOKEN: csrf_token,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        return resp.status == HTTP_STATUS_OK


def login_panel(port: int, username: str, password: str, base_path: str) -> tuple[str, str]:
    base = panel_api_base(port, base_path)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(f"{base}/", timeout=HTTP_TIMEOUT_SECONDS) as resp:
        csrf_token = _extract_csrf_token(resp.read().decode("utf-8", errors="ignore"))
    if not csrf_token:
        with opener.open(f"{base}{XUI_API_CSRF_PATH}", timeout=HTTP_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            obj = data.get("obj") if isinstance(data, dict) else ""
            csrf_token = obj if isinstance(obj, str) else ""
    body = urllib.parse.urlencode({
        "username": username,
        "password": password,
        "twoFactorCode": "",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{XUI_API_LOGIN_PATH}",
        data=body,
        headers={
            HTTP_HEADER_CONTENT_TYPE: HTTP_CONTENT_TYPE_FORM,
            HTTP_HEADER_USER_AGENT: OPSKIT_USER_AGENT,
            HTTP_HEADER_X_REQUESTED_WITH: HTTP_VALUE_XMLHTTPREQUEST,
            HTTP_HEADER_X_CSRF_TOKEN: csrf_token,
        },
        method="POST",
    )
    with opener.open(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        if resp.status in range(HTTP_STATUS_REDIRECT_MIN, HTTP_STATUS_REDIRECT_MAX + 1) or resp.status == HTTP_STATUS_OK:
            return _cookie_header(jar), csrf_token
    return "", ""


def add_inbound(port: int, username: str, password: str, base_path: str, payload: dict[str, object]) -> bool:
    for attempt in range(PANEL_API_RETRY_COUNT):
        try:
            cookie, csrf_token = login_panel(port, username, password, base_path)
            if cookie and csrf_token:
                base = panel_api_base(port, base_path)
                if _post_json(f"{base}{XUI_API_ADD_INBOUND_PATH}", payload, cookie, csrf_token):
                    return True
        except Exception:
            pass
        if attempt < PANEL_API_RETRY_COUNT - 1:
            time.sleep(PANEL_API_RETRY_DELAY_SECONDS)
    return False


def restart_service(service: str = XUI_SERVICE) -> None:
    subprocess.run([SYSTEMCTL_COMMAND, "restart", service], check=True, timeout=SERVICE_RESTART_TIMEOUT)


def stop_and_disable_service(service: str = XUI_SERVICE) -> None:
    subprocess.run([SYSTEMCTL_COMMAND, "stop", service], check=False, timeout=SERVICE_RESTART_TIMEOUT)
    subprocess.run([SYSTEMCTL_COMMAND, "disable", service], check=False, timeout=SERVICE_RESTART_TIMEOUT)
