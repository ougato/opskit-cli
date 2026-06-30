"""x-ui 自动化工具函数。"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import concurrent.futures
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
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
    PUBLIC_HOST_DETECT_TIMEOUT,
    LOOPBACK_HOST,
    INSTALL_SCRIPT_TIMEOUT,
    OPSKIT_USER_AGENT,
    PANEL_API_RETRY_COUNT,
    PANEL_API_RETRY_DELAY_SECONDS,
    PASSWORD_BYTES,
    REDACTED_VALUE,
    SECONDS_PER_DAY,
    SENSITIVE_STATE_KEYS,
    SERVICE_RESTART_TIMEOUT,
    SHORT_ID_BYTES,
    SS_COMMAND,
    SS_TCP_LISTEN_ARGS,
    SYSTEMCTL_COMMAND,
    SYSTEMCTL_DAEMON_RELOAD_ARG,
    SYSTEMCTL_ENABLE_ARG,
    SYSTEMCTL_ENABLE_NOW_ARG,
    SYSTEMCTL_START_ARG,
    SYSTEMCTL_STOP_ARG,
    TRAFFIC_SERVICE_UNIT_CONTENT,
    TRAFFIC_SERVICE_UNIT_FILE,
    TRAFFIC_SNAPSHOT_CLI_ARGS,
    TRAFFIC_TIMER_UNIT,
    TRAFFIC_TIMER_UNIT_CONTENT,
    TRAFFIC_TIMER_UNIT_FILE,
    XUI_TRAFFIC_HISTORY_FILE,
    WSL_DISTRO_NAME_ENV,
    WSL_MARKER,
    WSL_OSRELEASE_FILE,
    HTTP_URL_TEMPLATE,
    HTTP_VALUE_XMLHTTPREQUEST,
    BASH_COMMAND,
    CLIENT_EXPIRY_DAYS,
    CLIENT_TOTAL_GB,
    DEBIAN_FRONTEND_ENV,
    DEBIAN_FRONTEND_NONINTERACTIVE,
    XUI_API_ADD_INBOUND_PATH,
    XUI_API_CSRF_PATH,
    XUI_API_LOGIN_PATH,
    XUI_BINARY_COMMAND,
    XUI_COMMAND,
    XUI_ARTIFACT_DIRS,
    XUI_ARTIFACT_FILES,
    XUI_DATABASE_FILE,
    XUI_SETTING_PASSWORD_ARG,
    XUI_SETTING_PORT_ARG,
    XUI_SETTING_SUBCOMMAND,
    XUI_SETTING_USERNAME_ARG,
    XUI_SETTING_WEB_BASE_PATH_ARG,
    XUI_SETTING_SHOW_ARG,
    XUI_SETTING_PORT_KEY,
    XUI_SETTING_WEB_BASE_PATH_KEY,
    XUI_SETTING_KV_SEPARATOR,
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


def detect_public_host(timeout: float = PUBLIC_HOST_DETECT_TIMEOUT) -> str:
    """并发探测公网 IP，取最快返回的非空结果；整体受 timeout 限制，避免逐个超时累加阻塞。"""
    def _probe(url: str) -> str:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": OPSKIT_USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception:
            return ""

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(PUBLIC_IP_APIS))
    futures = [pool.submit(_probe, url) for url in PUBLIC_IP_APIS]
    result = ""
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=timeout + 1):
            text = fut.result()
            if text:
                result = text
                break
    except Exception:
        pass
    pool.shutdown(wait=False)
    return result


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
                [XUI_BINARY_COMMAND, "-v"],
                capture_output=True,
                text=True,
                check=False,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            output = (result.stdout or result.stderr).strip()
            if result.returncode == 0 and output:
                match = re.search(r"v?\d+\.\d+(?:\.\d+)?", output)
                if match:
                    return match.group(0)
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


def get_panel_settings() -> dict[str, object]:
    """读取 x-ui 实际生效的面板设置（port / webBasePath）。

    3x-ui 会为面板生成随机 webBasePath；`x-ui setting -webBasePath ""`
    不会将其重置为空，因此必须回读真实值用于后续 API 自动化与地址展示。
    """
    if not command_exists(XUI_BINARY_COMMAND):
        return {}
    result = subprocess.run(
        [XUI_BINARY_COMMAND, XUI_SETTING_SUBCOMMAND, XUI_SETTING_SHOW_ARG],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, DEBIAN_FRONTEND_ENV: DEBIAN_FRONTEND_NONINTERACTIVE},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    settings: dict[str, object] = {}
    for line in result.stdout.splitlines():
        if XUI_SETTING_KV_SEPARATOR not in line:
            continue
        key, _, value = line.partition(XUI_SETTING_KV_SEPARATOR)
        key = key.strip()
        value = value.strip()
        if key == XUI_SETTING_PORT_KEY:
            try:
                settings["port"] = int(value)
            except ValueError:
                continue
        elif key == XUI_SETTING_WEB_BASE_PATH_KEY:
            settings["base_path"] = value
    return settings


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
        text = resp.read().decode("utf-8", errors="ignore")
        if resp.status != HTTP_STATUS_OK:
            return False
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return True
        return data.get("success") is True


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
        text = resp.read().decode("utf-8", errors="ignore")
        if resp.status in range(HTTP_STATUS_REDIRECT_MIN, HTTP_STATUS_REDIRECT_MAX + 1):
            return _cookie_header(jar), csrf_token
        if resp.status == HTTP_STATUS_OK:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {}
            if data.get("success") is True:
                return _cookie_header(jar), csrf_token
    return "", ""


def enable_inbound_clients(remarks: list[str]) -> None:
    if not XUI_DATABASE_FILE.exists():
        return
    expiry_time = int((time.time() + CLIENT_EXPIRY_DAYS * SECONDS_PER_DAY) * 1000)
    with sqlite3.connect(XUI_DATABASE_FILE) as conn:
        for remark in remarks:
            _enable_inbound_client(conn, remark, expiry_time)


def _enable_inbound_client(conn: sqlite3.Connection, remark: str, expiry_time: int) -> None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("select id, settings from inbounds where remark = ?", (remark,)).fetchone()
    if not row:
        return
    settings = json.loads(row["settings"])
    for client in settings.get("clients") or []:
        client["enable"] = True
        client["totalGB"] = CLIENT_TOTAL_GB
        client["expiryTime"] = expiry_time
    conn.execute("update inbounds set settings = ? where id = ?", (json.dumps(settings, ensure_ascii=False), row["id"]))
    conn.execute(
        "update clients set enable = 1, total_gb = ?, expiry_time = ? where email = ?",
        (CLIENT_TOTAL_GB, expiry_time, remark),
    )
    conn.execute(
        "update client_traffics set enable = 1, total = ?, expiry_time = ? where inbound_id = ? and email = ?",
        (CLIENT_TOTAL_GB, expiry_time, row["id"], remark),
    )


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


def systemd_available() -> bool:
    from core.service import SYSTEMD_RUNTIME_DIR
    return SYSTEMD_RUNTIME_DIR.exists()


def is_wsl() -> bool:
    if os.environ.get(WSL_DISTRO_NAME_ENV):
        return True
    try:
        return WSL_MARKER in WSL_OSRELEASE_FILE.read_text(encoding="utf-8").lower()
    except OSError:
        return False


def restart_service(service: str = XUI_SERVICE) -> None:
    if not systemd_available():
        return
    subprocess.run(
        [SYSTEMCTL_COMMAND, "restart", service],
        check=True, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
    )


def start_service(service: str = XUI_SERVICE) -> None:
    if not systemd_available():
        return
    subprocess.run(
        [SYSTEMCTL_COMMAND, SYSTEMCTL_START_ARG, service],
        check=True, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
    )


def stop_service(service: str = XUI_SERVICE) -> None:
    if not systemd_available():
        return
    subprocess.run(
        [SYSTEMCTL_COMMAND, SYSTEMCTL_STOP_ARG, service],
        check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
    )


def enable_service(service: str = XUI_SERVICE) -> None:
    if not systemd_available():
        return
    subprocess.run(
        [SYSTEMCTL_COMMAND, SYSTEMCTL_ENABLE_ARG, service],
        check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
    )


def _snapshot_exec_start() -> str:
    """生成定时器 ExecStart：打包二进制直接调用自身，开发态用 python 跑 main.py。"""
    prog = os.path.abspath(sys.argv[0])
    if prog.endswith(".py"):
        return f"{sys.executable} {prog} {TRAFFIC_SNAPSHOT_CLI_ARGS}"
    return f"{prog} {TRAFFIC_SNAPSHOT_CLI_ARGS}"


def install_traffic_timer() -> None:
    """安装并启用每小时流量快照定时器；无 systemd 时跳过。"""
    if not systemd_available():
        return
    TRAFFIC_SERVICE_UNIT_FILE.write_text(
        TRAFFIC_SERVICE_UNIT_CONTENT.format(exec_start=_snapshot_exec_start()),
        encoding="utf-8",
    )
    TRAFFIC_TIMER_UNIT_FILE.write_text(TRAFFIC_TIMER_UNIT_CONTENT, encoding="utf-8")
    subprocess.run(
        [SYSTEMCTL_COMMAND, SYSTEMCTL_DAEMON_RELOAD_ARG],
        check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
    )
    subprocess.run(
        [SYSTEMCTL_COMMAND, SYSTEMCTL_ENABLE_ARG, SYSTEMCTL_ENABLE_NOW_ARG, TRAFFIC_TIMER_UNIT],
        check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
    )


def uninstall_traffic_timer() -> None:
    """停用并移除流量快照定时器、历史库；无 systemd 时仅清理文件。"""
    if systemd_available():
        subprocess.run(
            [SYSTEMCTL_COMMAND, "disable", SYSTEMCTL_ENABLE_NOW_ARG, TRAFFIC_TIMER_UNIT],
            check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
        )
    TRAFFIC_TIMER_UNIT_FILE.unlink(missing_ok=True)
    TRAFFIC_SERVICE_UNIT_FILE.unlink(missing_ok=True)
    XUI_TRAFFIC_HISTORY_FILE.unlink(missing_ok=True)
    if systemd_available():
        subprocess.run(
            [SYSTEMCTL_COMMAND, SYSTEMCTL_DAEMON_RELOAD_ARG],
            check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
        )


def stop_and_disable_service(service: str = XUI_SERVICE) -> None:
    if not systemd_available():
        return
    subprocess.run(
        [SYSTEMCTL_COMMAND, "stop", service],
        check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
    )
    subprocess.run(
        [SYSTEMCTL_COMMAND, "disable", service],
        check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
    )


def remove_xui_artifacts() -> None:
    for path in XUI_ARTIFACT_DIRS:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    for path in XUI_ARTIFACT_FILES:
        path.unlink(missing_ok=True)
    if systemd_available():
        subprocess.run(
            [SYSTEMCTL_COMMAND, "daemon-reload"],
            check=False, capture_output=True, text=True, timeout=SERVICE_RESTART_TIMEOUT,
        )
