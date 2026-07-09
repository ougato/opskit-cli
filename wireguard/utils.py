"""WireGuard 部署工具函数"""
from __future__ import annotations

import secrets
import subprocess
import re
from pathlib import Path

from core.privilege import run_as_root, write_root_file
from wireguard.constants import XRAY_DOC_URL as _XRAY_DOC_URL


def normalize_tunnel_label(value: str | None, default: str = "default") -> str:
    """规范化 systemd 实例和文件名中使用的隧道 label。"""
    raw = (value or "").strip() or default
    return re.sub(r"[^a-zA-Z0-9_-]", "-", raw)[:24].strip("-") or default


def gen_wg_keypair() -> tuple[str, str]:
    """生成 WireGuard 密钥对，返回 (private_key, public_key)"""
    result = subprocess.run(["wg", "genkey"], capture_output=True, text=True, check=True)
    private_key = result.stdout.strip()
    result = subprocess.run(
        ["wg", "pubkey"], input=private_key, capture_output=True, text=True, check=True
    )
    public_key = result.stdout.strip()
    return private_key, public_key


def gen_wg_psk() -> str:
    """生成 WireGuard PresharedKey"""
    result = subprocess.run(["wg", "genpsk"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def gen_xray_keypair() -> tuple[str, str]:
    """生成 xray REALITY x25519 密钥对，返回 (private_key, public_key)"""
    result = subprocess.run(
        ["xray", "x25519"], capture_output=True, text=True, check=True
    )
    private_key = ""
    public_key = ""
    for line in result.stdout.splitlines():
        if "Private" in line:
            private_key = line.split(":")[-1].strip()
        elif "Public" in line:
            public_key = line.split(":")[-1].strip()
    return private_key, public_key


def gen_uuid() -> str:
    """生成 UUID（用于 xray 用户标识）"""
    import uuid
    return str(uuid.uuid4())


def gen_short_id() -> str:
    """生成 xray REALITY shortId（16 位十六进制）"""
    return secrets.token_hex(8)


def detect_default_iface() -> str:
    """检测默认出网网卡名称"""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, check=True,
        )
        for part in result.stdout.split():
            if part.startswith("dev"):
                continue
            idx = result.stdout.split().index("dev")
            return result.stdout.split()[idx + 1]
    except Exception:
        pass
    return "eth0"


def is_port_listening(port: int, proto: str = "tcp") -> bool:
    """检测本机端口是否在监听（proto: 'tcp' 或 'udp'）"""
    try:
        flag = "-tlnp" if proto == "tcp" else "-ulnp"
        result = subprocess.run(
            ["ss", flag],
            capture_output=True, text=True, check=False,
        )
        return f":{port}" in result.stdout
    except Exception:
        return False


def is_service_active(service: str) -> bool:
    """检查 systemd 服务是否活跃"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, check=False,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def enable_and_start(service: str) -> None:
    """启用并启动 systemd 服务（写系统 unit，需 root，非 root 自动 sudo）"""
    run_as_root(["systemctl", "enable", service], check=True,
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    run_as_root(["systemctl", "start", service], check=True,
                capture_output=True, text=True, encoding="utf-8", errors="replace")


def stop_and_disable(service: str) -> None:
    """停止并禁用 systemd 服务（需 root，非 root 自动 sudo）"""
    run_as_root(["systemctl", "stop", service], check=False,
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    run_as_root(["systemctl", "disable", service], check=False,
                capture_output=True, text=True, encoding="utf-8", errors="replace")


def ensure_xray_runtime_permissions() -> None:
    """Prepare Xray runtime directories for services running as the nobody user."""
    from core.paths import xray_config_dir, xray_data_dir, xray_log_dir

    log_dir = xray_log_dir()
    run_as_root(
        ["mkdir", "-p", str(log_dir), str(xray_config_dir()), str(xray_data_dir())],
        check=False, capture_output=True,
    )
    run_as_root(
        ["chmod", "0755", str(log_dir), str(xray_config_dir()), str(xray_data_dir())],
        check=False, capture_output=True,
    )
    run_as_root(
        [
            "sh",
            "-c",
            'group="$(id -gn nobody 2>/dev/null || printf nobody)"; chown -R "nobody:${group}" "$1"',
            "sh",
            str(log_dir),
        ],
        check=False, capture_output=True,
    )


def write_file(path: str, content: str) -> None:
    """写入文件（系统路径自动提权落位）"""
    write_root_file(path, content, "0644")


def write_secret_file(path: str, content: str) -> None:
    """写入敏感文件，并限制为 owner 可读写（系统路径自动提权落位）。"""
    write_root_file(path, content, "0600")


def get_os_id() -> str:
    """读取 /etc/os-release 获取发行版 ID"""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID="):
                    return line.strip().split("=", 1)[1].strip('"').lower()
    except FileNotFoundError:
        pass
    return "unknown"


def get_os_version() -> int:
    """读取 /etc/os-release 获取主版本号整数，失败返回 0"""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("VERSION_ID="):
                    val = line.strip().split("=", 1)[1].strip('"')
                    return int(val.split(".")[0])
    except Exception:
        pass
    return 0


def install_wireguard_pkg(os_id: str) -> None:
    """根据发行版安装 WireGuard 包（幂等）"""
    import shutil
    if shutil.which("wg"):
        return

    from software.base import InstallError
    from core.pkg_runner import get_runner

    runner = get_runner()
    runner.update_index()

    if os_id == "centos" and get_os_version() == 7:
        # CentOS 7 需要 elrepo 提供内核模块
        runner.install_extras(["epel-release", "elrepo-release"])
        runner.install(["kmod-wireguard", "wireguard-tools"])
    elif os_id in ("centos", "rocky", "almalinux", "rhel"):
        runner.install_extras(["epel-release"])
        runner.install(["wireguard-tools"])
    elif os_id in ("debian", "ubuntu", "fedora", "alpine"):
        runner.install(["wireguard", "wireguard-tools"])
    else:
        from core.i18n import t
        raise InstallError(t("wireguard.error.unsupported_wg", os_id=os_id))


def ensure_xray_service(xray_path: Path, service_name: str) -> None:
    """确保 xray systemd service 文件存在（带 CAP_NET_ADMIN）并 daemon-reload"""
    from core.paths import xray_config_file
    service_path = Path(f"/etc/systemd/system/{service_name}.service")
    if not service_path.exists():
        write_root_file(
            service_path,
            "[Unit]\n"
            "Description=Xray Service\n"
            f"Documentation={_XRAY_DOC_URL}\n"
            "After=network.target nss-lookup.target\n\n"
            "[Service]\n"
            "User=nobody\n"
            "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE\n"
            "AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE\n"
            "NoNewPrivileges=true\n"
            f"ExecStart={xray_path} run -config {xray_config_file()}\n"
            "Restart=on-failure\n"
            "RestartPreventExitStatus=23\n"
            "LimitNPROC=10000\n"
            "LimitNOFILE=1000000\n"
            "RuntimeDirectory=xray\n"
            "RuntimeDirectoryMode=0755\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n",
            "0644",
        )
        run_as_root(["systemctl", "daemon-reload"], check=False, capture_output=True)


def install_xray() -> None:
    """安装 xray-core（多镜像回退，幂等）"""
    import tempfile
    import zipfile
    import shutil
    import json
    import urllib.request
    from software.base import InstallError
    from wireguard.constants import XRAY_BINARY, XRAY_SERVICE

    from core.paths import xray_data_dir
    xray_path = Path(XRAY_BINARY)

    if xray_path.exists():
        ensure_xray_runtime_permissions()
        ensure_xray_service(xray_path, XRAY_SERVICE)
        return

    from wireguard.constants import XRAY_API_LATEST, XRAY_API_LATEST_GHPROXY
    ver = None
    api_urls = [XRAY_API_LATEST, XRAY_API_LATEST_GHPROXY]
    for api_url in api_urls:
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "opskit/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                ver = data.get("tag_name", "").lstrip("v")
                if ver:
                    break
        except Exception:
            continue
    if not ver:
        ver = "25.3.6"

    from wireguard.constants import XRAY_DOWNLOAD_ZIP, XRAY_REPO
    from core.constants import GITHUB_BASE
    zip_name = XRAY_DOWNLOAD_ZIP
    github_rel_path = f"{XRAY_REPO}/releases/download/v{ver}/{zip_name}"
    github_direct = f"{GITHUB_BASE}/{github_rel_path}"
    url_template = f"{{mirror}}/{github_rel_path}"

    from core.mirror import download as mirror_download, get_download_cache_path, _is_cached_valid
    cache_zip = get_download_cache_path("xray", ver, zip_name)

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / zip_name
        downloaded = False

        try:
            mirror_download(
                url_template,
                zip_path,
                category="github_releases",
                fallback_url=github_direct,
                cache_path=cache_zip,
            )
            if zip_path.exists() and zip_path.stat().st_size > 1_000_000:
                downloaded = True
            else:
                zip_path.unlink(missing_ok=True)
        except Exception:
            zip_path.unlink(missing_ok=True)

        if not downloaded:
            try:
                result = subprocess.run(
                    ["curl", "-fL",
                     "--connect-timeout", "10",
                     "--speed-time", "15", "--speed-limit", "10000",
                     "--max-time", "60",
                     "-s", "-o", str(zip_path), github_direct],
                    check=False, capture_output=True, text=True,
                )
                if result.returncode == 0 and zip_path.exists() and zip_path.stat().st_size > 1_000_000:
                    downloaded = True
                    try:
                        cache_zip.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(zip_path), str(cache_zip))
                    except Exception:
                        pass
                else:
                    zip_path.unlink(missing_ok=True)
            except Exception:
                zip_path.unlink(missing_ok=True)

        if not downloaded:
            from core.i18n import t
            raise InstallError(t("wireguard.error.xray_download_fail", ver=ver, url=github_direct, path=cache_zip))

        extract_dir = Path(tmpdir) / "xray"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        src = extract_dir / "xray"
        if not src.exists():
            from core.i18n import t
            raise InstallError(t("wireguard.error.xray_zip_missing"))

        run_as_root(["mkdir", "-p", str(xray_path.parent), str(xray_data_dir())],
                    check=False, capture_output=True)
        run_as_root(["install", "-m", "0755", str(src), str(xray_path)])

        for geo in ("geoip.dat", "geosite.dat"):
            geo_src = extract_dir / geo
            if geo_src.exists():
                run_as_root(["install", "-m", "0644", str(geo_src), str(xray_data_dir() / geo)])

    ensure_xray_service(xray_path, XRAY_SERVICE)
    ensure_xray_runtime_permissions()
