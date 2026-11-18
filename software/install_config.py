"""Strict configuration for official installation services."""
from pathlib import Path
import yaml


def load_options(name: str, filename: str) -> dict:
    allowed = {"ohmyzsh": {"with_p10k", "switch_shell"}, "tailscale": {"exit_node"},
               "xui": {"host", "panel_port", "panel_user", "panel_password_file", "panel_base_path", "vless_port", "sni", "dest", "credentials_output_file"},
               "wg_server": {"domain", "public_ip", "subnet_octet", "label", "acme_email", "credentials_output_file"}}
    if name not in allowed:
        raise ValueError("this software does not yet accept an installation config")
    path = Path(filename)
    with path.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("installation config exceeds 64 KiB")
    options = yaml.safe_load(raw)
    if not isinstance(options, dict) or type(options.get("schema_version")) is not int or options["schema_version"] != 1:
        raise ValueError("installation config requires schema_version: 1")
    if name == "wg_server":
        if set(options) - allowed[name] - {"schema_version"} or not {"domain", "public_ip", "credentials_output_file"} <= set(options):
            raise ValueError("invalid WireGuard config fields")
        del options["schema_version"]
        return wireguard_options(options, path.parent)
    if name == "xui":
        if set(options) - allowed[name] - {"schema_version"} or "host" not in options:
            raise ValueError("invalid XUI config fields")
        del options["schema_version"]
        return xui_options(options, path.parent)
    if set(options) != allowed[name] | {"schema_version"}:
        raise ValueError("installation config has missing or unknown fields")
    del options["schema_version"]
    if any(type(value) is not bool for value in options.values()):
        raise ValueError("installation options must be booleans")
    return options


def install(name: str, options: dict) -> dict:
    if name == "wg_server":
        from wireguard.server import install_server
        return install_server(options=options)
    if name == "xui":
        from xui.server import install_server
        return install_server(options=options)
    if name == "ohmyzsh":
        from software.recipes.ohmyzsh.impl import install_ohmyzsh
        return install_ohmyzsh(**options)
    if name == "tailscale":
        from tailscale.server import prepare_client
        return prepare_client(**options)
    raise ValueError("unsupported installation config")


def wireguard_options(options: dict, directory: Path) -> dict:
    import re
    import ipaddress
    from wireguard.constants import VPN_DEFAULT_OCTET3
    result = {"subnet_octet": VPN_DEFAULT_OCTET3, "label": "server", **options}
    if not isinstance(result["domain"], str) or not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", result["domain"]):
        raise ValueError("invalid certificate domain")
    if not isinstance(result["public_ip"], str) or ipaddress.ip_address(result["public_ip"]).version != 4:
        raise ValueError("public_ip must be IPv4")
    if type(result["subnet_octet"]) is not int or not 1 <= result["subnet_octet"] <= 254:
        raise ValueError("invalid subnet octet")
    if not isinstance(result["label"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", result["label"]):
        raise ValueError("invalid tunnel label")
    result.setdefault("acme_email", "admin@" + result["domain"])
    if not isinstance(result["acme_email"], str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+", result["acme_email"]):
        raise ValueError("invalid ACME email")
    output = result["credentials_output_file"]
    if not isinstance(output, str) or not output or output == "-":
        raise ValueError("explicit credential output is required")
    target = Path(output)
    target = target if target.is_absolute() else directory / target
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("credential output must be a new file")
    result["credentials_output_file"] = str(target)
    return result


def xui_options(options: dict, directory: Path) -> dict:
    import re
    from core.cli_secrets import read_secret_file
    from xui import constants as c
    result = {"panel_port": c.DEFAULT_PANEL_PORT, "panel_user": c.DEFAULT_PANEL_USER,
              "panel_base_path": c.DEFAULT_PANEL_BASE_PATH, "vless_port": c.DEFAULT_VLESS_PORT,
              "sni": c.DEFAULT_REALITY_SNI, **options}
    for key in ("panel_port", "vless_port"):
        if type(result[key]) is not int or not 1 <= result[key] <= 65535:
            raise ValueError("invalid port")
    if result["panel_port"] == result["vless_port"]:
        raise ValueError("panel and inbound ports conflict")
    for key in ("host", "sni"):
        if not isinstance(result[key], str) or not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+", result[key]):
            raise ValueError("invalid hostname")
    if not isinstance(result["panel_user"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", result["panel_user"]):
        raise ValueError("invalid panel user")
    if not isinstance(result["panel_base_path"], str) or not re.fullmatch(r"/?[A-Za-z0-9_/-]*", result["panel_base_path"]):
        raise ValueError("invalid panel path")
    if result["panel_base_path"] and not result["panel_base_path"].startswith("/"):
        result["panel_base_path"] = "/" + result["panel_base_path"]
    result.setdefault("dest", result["sni"] + ":" + str(c.DEFAULT_VLESS_PORT))
    if not isinstance(result["dest"], str) or not re.fullmatch(r"[A-Za-z0-9_.\[\]:-]+:[0-9]{1,5}", result["dest"]):
        raise ValueError("invalid destination")
    if not 1 <= int(result["dest"].rsplit(":", 1)[1]) <= 65535:
        raise ValueError("invalid destination port")
    for key in ("panel_password_file", "credentials_output_file"):
        if key in result:
            if not isinstance(result[key], str) or not result[key] or result[key] == "-":
                raise ValueError("XUI secret paths must be explicit files")
            file = Path(result[key])
            result[key] = str(file if file.is_absolute() else directory / file)
    output = result.get("credentials_output_file")
    if output and (Path(output).exists() or Path(output).is_symlink() or not Path(output).parent.is_dir()):
        raise ValueError("credential output must be a new file in an existing directory")
    if result.get("panel_password_file"):
        result["panel_password"] = read_secret_file(result.pop("panel_password_file"))
    else:
        if not output:
            raise ValueError("generated password requires credentials_output_file")
        from xui.utils import gen_password
        result["panel_password"] = gen_password()
    return result
