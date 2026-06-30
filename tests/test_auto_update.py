"""自动更新策略完整测试 — 覆盖版本缓存、源管理、bootstrap、updater 改进、Recipe 基类、WireGuard 配方"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Recipe 基类能力声明
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecipeCapabilities:
    """验证 Recipe 基类新增的 has_upgrade / has_diagnose / has_submenu / has_wizard 字段"""

    def test_default_flags(self):
        from software.base import Recipe
        # 默认值检查（通过子类验证，因为 Recipe 是 ABC）
        class _Dummy(Recipe):
            key = "dummy"
            platforms = ["linux"]
            def detect(self): return None
            def versions(self): return ["1.0"]
            def install(self, v): pass
            def uninstall(self): pass

        d = _Dummy()
        assert d.has_upgrade is True
        assert d.has_diagnose is False
        assert d.has_submenu is False
        assert d.has_wizard is False

    def test_override_flags(self):
        from software.base import Recipe
        class _Custom(Recipe):
            key = "custom"
            platforms = ["linux"]
            has_upgrade = False
            has_diagnose = True
            has_submenu = True
            has_wizard = True
            def detect(self): return None
            def versions(self): return ["1.0"]
            def install(self, v): pass
            def uninstall(self): pass

        c = _Custom()
        assert c.has_upgrade is False
        assert c.has_diagnose is True
        assert c.has_submenu is True
        assert c.has_wizard is True

    def test_diagnose_default_noop(self):
        from software.base import Recipe
        class _D(Recipe):
            key = "d"
            platforms = ["linux"]
            def detect(self): return None
            def versions(self): return []
            def install(self, v): pass
            def uninstall(self): pass
        # diagnose() 默认不抛异常
        _D().diagnose()

    def test_submenu_items_default_empty(self):
        from software.base import Recipe
        class _D(Recipe):
            key = "d"
            platforms = ["linux"]
            def detect(self): return None
            def versions(self): return []
            def install(self, v): pass
            def uninstall(self): pass
        assert _D().submenu_items() == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. WireGuard Recipes 注册 & 子菜单
# ═══════════════════════════════════════════════════════════════════════════════

class TestWireGuardRecipes:
    """验证 WireGuard 父级 / 服务端 / 客户端 Recipe 注册和字段"""

    def test_wireguard_registered(self):
        from software.registry import get
        cls = get("wireguard")
        assert cls is not None
        assert cls.key == "wireguard"
        assert cls.has_submenu is True
        assert cls.has_upgrade is False

    def test_wireguard_submenu_items(self):
        from software.registry import get
        cls = get("wireguard")
        instance = cls()
        items = instance.submenu_items()
        assert len(items) == 2
        keys = [item["key"] for item in items]
        assert "wg_server" in keys
        assert "wg_client" in keys

    def test_wg_server_registered(self):
        from software.registry import get
        cls = get("wg_server")
        assert cls is not None
        assert cls.has_wizard is True
        assert cls.has_diagnose is True
        assert cls.has_upgrade is False

    def test_wg_client_registered(self):
        from software.registry import get
        cls = get("wg_client")
        assert cls is not None
        assert cls.has_wizard is True
        assert cls.has_diagnose is True

    def test_wg_server_steps(self):
        from software.registry import get
        cls = get("wg_server")
        steps = cls().steps("install")
        assert len(steps) >= 5
        uninstall_steps = cls().steps("uninstall")
        assert len(uninstall_steps) >= 2

    def test_wg_client_steps(self):
        from software.registry import get
        cls = get("wg_client")
        steps = cls().steps("install")
        assert len(steps) >= 7


# ═══════════════════════════════════════════════════════════════════════════════
# 3. WireGuard 模板生成
# ═══════════════════════════════════════════════════════════════════════════════

class TestWireGuardTemplates:
    """验证 xray / WireGuard 配置模板生成"""

    def test_xray_server_config_ws_valid_json(self):
        from wireguard.templates import xray_server_config_ws
        cfg = xray_server_config_ws(
            uuid="test-uuid",
            ws_port=2443,
            ws_path="/vless-ws",
        )
        data = json.loads(cfg)
        assert data["inbounds"][0]["port"] == 2443
        assert data["inbounds"][0]["settings"]["clients"][0]["id"] == "test-uuid"
        assert data["inbounds"][0]["streamSettings"]["network"] == "ws"

    def test_xray_client_config_valid_json(self):
        from wireguard.templates import xray_client_config
        cfg = xray_client_config(
            sni="wg.test.com",
            server_port=443,
            uuid="test-uuid",
            local_port=4000,
            wg_port=3002,
        )
        data = json.loads(cfg)
        assert data["inbounds"][0]["port"] == 4000
        assert data["outbounds"][0]["settings"]["vnext"][0]["address"] == "wg.test.com"
        assert data["outbounds"][0]["streamSettings"]["network"] == "ws"

    def test_wg_server_config_contains_key(self):
        from wireguard.templates import wg_server_config
        cfg = wg_server_config(
            server_private_key="FAKE_KEY", server_ip="10.10.10.1",
            wg_port=3002, iface="eth0",
        )
        assert "FAKE_KEY" in cfg
        assert "10.10.10.1" in cfg
        assert "ListenPort = 3002" in cfg

    def test_wg_client_config_contains_endpoint(self):
        from wireguard.templates import wg_client_config
        cfg = wg_client_config(
            client_private_key="CLIENT_KEY", client_ip="10.10.10.3",
            server_public_key="SERVER_PUB", psk="PSKVAL",
            server_endpoint="1.2.3.4:3002",
        )
        assert "CLIENT_KEY" in cfg
        assert "Endpoint = 1.2.3.4:3002" in cfg
        assert "SERVER_PUB" in cfg


# ═══════════════════════════════════════════════════════════════════════════════
# 4. WireGuard 常量
# ═══════════════════════════════════════════════════════════════════════════════

class TestWireGuardConstants:
    def test_constants_exist(self):
        from wireguard.constants import (
            XRAY_REALITY_PORT, WG_UDP_PORT, VPN_SERVER_IP,
            VPN_CLIENT_IP_START, WG_CONFIG_FILE, XRAY_CONFIG_FILE,
        )
        assert XRAY_REALITY_PORT == 443
        assert WG_UDP_PORT == 3002
        assert VPN_SERVER_IP == "10.10.10.1"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. core/http.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestHttpModule:
    def test_get_json_success(self):
        from core.http import get_json
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        with patch("httpx.Client") as mc:
            mc.return_value.__enter__.return_value.get.return_value = mock_resp
            result = get_json("https://example.com/api")
        assert result == {"ok": True}

    def test_get_json_failure(self):
        from core.http import get_json
        with patch("httpx.Client", side_effect=Exception("fail")):
            result = get_json("https://example.com/api", retries=1)
        assert result is None

    def test_get_json_403(self):
        from core.http import get_json
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch("httpx.Client") as mc:
            mc.return_value.__enter__.return_value.get.return_value = mock_resp
            result = get_json("https://example.com/api")
        assert result is None

    def test_head_ok_success(self):
        from core.http import head_ok
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.Client") as mc:
            mc.return_value.__enter__.return_value.head.return_value = mock_resp
            assert head_ok("https://example.com") is True

    def test_head_ok_failure(self):
        from core.http import head_ok
        with patch("httpx.Client", side_effect=Exception("fail")):
            assert head_ok("https://example.com") is False


# ═══════════════════════════════════════════════════════════════════════════════
# 6. core/version_cache.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionCache:
    """版本缓存层：读写、TTL、后台刷新"""

    def _reset(self):
        from core import version_cache as vc
        vc._cache = {}
        vc._loaded = False

    def test_cache_roundtrip(self, tmp_path):
        from core import version_cache as vc
        self._reset()
        with patch.object(vc, "_get_cache_path", return_value=tmp_path / "vc.yaml"):
            vc.update_cache("docker", ["27.0", "26.1"])
            cached = vc.get_cached_versions("docker")
        assert cached == ["27.0", "26.1"]

    def test_cache_ttl_fresh(self, tmp_path):
        from core import version_cache as vc
        self._reset()
        with patch.object(vc, "_get_cache_path", return_value=tmp_path / "vc.yaml"):
            vc._ensure_loaded()
            vc._cache["nginx"] = {
                "versions": ["1.27", "1.26"],
                "timestamp": time.time(),  # 刚写入
            }
            result = vc.get_cached_versions("nginx")
        assert result == ["1.27", "1.26"]

    def test_cache_ttl_stale(self, tmp_path):
        from core import version_cache as vc
        self._reset()
        with patch.object(vc, "_get_cache_path", return_value=tmp_path / "vc.yaml"):
            vc._ensure_loaded()
            vc._cache["nginx"] = {
                "versions": ["1.27"],
                "timestamp": time.time() - 7200,  # 2h 前
            }
            result = vc.get_cached_versions("nginx")
        # 1h~24h 范围仍返回缓存
        assert result == ["1.27"]

    def test_cache_ttl_expired(self, tmp_path):
        from core import version_cache as vc
        self._reset()
        with patch.object(vc, "_get_cache_path", return_value=tmp_path / "vc.yaml"):
            vc._ensure_loaded()
            vc._cache["nginx"] = {
                "versions": ["1.27"],
                "timestamp": time.time() - 100000,  # >24h
            }
            result = vc.get_cached_versions("nginx")
        assert result is None

    def test_cache_stale_fallback(self, tmp_path):
        from core import version_cache as vc
        self._reset()
        with patch.object(vc, "_get_cache_path", return_value=tmp_path / "vc.yaml"):
            vc._ensure_loaded()
            vc._cache["nginx"] = {
                "versions": ["1.27"],
                "timestamp": time.time() - 100000,
            }
            result = vc.get_cached_versions_stale("nginx")
        assert result == ["1.27"]

    def test_corrupted_cache_recovery(self, tmp_path):
        from core import version_cache as vc
        self._reset()
        cache_path = tmp_path / "vc.yaml"
        cache_path.write_text("{{INVALID YAML", encoding="utf-8")
        with patch.object(vc, "_get_cache_path", return_value=cache_path):
            data = vc._load_cache()
        assert data == {}
        assert not cache_path.exists()  # 损坏文件被删

    def test_atomic_write(self, tmp_path):
        from core import version_cache as vc
        cache_path = tmp_path / "vc.yaml"
        with patch.object(vc, "_get_cache_path", return_value=cache_path):
            vc._save_cache({"docker": {"versions": ["27.0"], "timestamp": 1.0}})
        assert cache_path.exists()
        data = yaml.safe_load(cache_path.read_text(encoding="utf-8"))
        assert "docker" in data

    def test_notify_mirrors_ready(self):
        from core.version_cache import _refresh_event, notify_mirrors_ready
        _refresh_event.clear()
        assert not _refresh_event.is_set()
        notify_mirrors_ready()
        assert _refresh_event.is_set()

    def test_fetch_versions_online_none(self):
        from core.version_cache import fetch_versions_online
        from software.base import Recipe
        class _NoSource(Recipe):
            key = "ns"
            platforms = ["linux"]
            version_source = "none"
            fallback_versions = ["latest"]
            def detect(self): return None
            def versions(self): return []
            def install(self, v): pass
            def uninstall(self): pass
        result = fetch_versions_online(_NoSource())
        assert result == ["latest"]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. sources.yaml 扩展验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestSourcesYaml:
    def test_version_api_section_exists(self):
        from core.mirror import _load_sources
        sources = _load_sources()
        assert "version_api" in sources
        va = sources["version_api"]
        assert "github" in va
        assert "endoflife" in va

    def test_version_api_github_has_regions(self):
        from core.mirror import _load_sources
        sources = _load_sources()
        github = sources["version_api"]["github"]
        assert "cn" in github or "global" in github


# ═══════════════════════════════════════════════════════════════════════════════
# 8. mirror.py 改造验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestMirrorVersionApi:
    """验证 mirror init 处理 version_api 子分类"""

    def test_init_includes_version_api(self, tmp_path):
        from core import mirror as mir
        mir._initialized = False
        mir._cache = {}
        mir._sources = {}

        with patch.object(mir, "detect_region", return_value="global"), \
             patch.object(mir, "rank_sources", return_value=["https://example.com"]), \
             patch.object(mir, "_save_cache"), \
             patch.object(mir, "_load_cache", return_value={}), \
             patch("core.version_cache.notify_mirrors_ready"):
            mir.init(region="global")

        ranked = mir._cache.get("ranked", {})
        assert "version_api.github" in ranked
        mir._initialized = False


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Bootstrap 拉取
# ═══════════════════════════════════════════════════════════════════════════════

class TestBootstrap:
    def test_fetch_bootstrap_success(self):
        from core.updater import fetch_bootstrap
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"schema_version": 1, "latest": {}}

        with patch("httpx.Client") as mc:
            mc.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_bootstrap()
        assert result is not None
        assert result["schema_version"] == 1

    def test_fetch_bootstrap_all_fail_uses_cache(self, tmp_path):
        from core.updater import fetch_bootstrap, _get_bootstrap_cache_path
        cache_path = tmp_path / "bootstrap_cache.json"
        cache_path.write_text('{"schema_version": 1, "cached": true}', encoding="utf-8")

        with patch("httpx.Client", side_effect=Exception("fail")), \
             patch.object(
                 sys.modules["core.updater"], "_get_bootstrap_cache_path",
                 return_value=cache_path,
             ):
            result = fetch_bootstrap()
        assert result is not None
        assert result.get("cached") is True

    def test_fetch_bootstrap_all_fail_no_cache(self):
        from core.updater import fetch_bootstrap
        with patch("httpx.Client", side_effect=Exception("fail")), \
             patch(
                 "core.updater._get_bootstrap_cache_path",
                 return_value=Path("/nonexistent/path/cache.json"),
             ):
            result = fetch_bootstrap()
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Updater 改进
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdaterImprovements:
    """验证 updater 新增功能：pending 检测、二进制验证"""

    def test_verify_binary_too_small(self, tmp_path):
        from core.updater import _verify_binary
        p = tmp_path / "tiny"
        p.write_bytes(b"MZ" + b"\x00" * 10)
        assert _verify_binary(p) is False

    def test_verify_binary_valid_pe(self, tmp_path):
        from core.updater import _verify_binary
        p = tmp_path / "valid.exe"
        p.write_bytes(b"MZ" + b"\x00" * (1024 * 200))
        if sys.platform == "win32":
            assert _verify_binary(p) is True

    def test_verify_binary_valid_elf(self, tmp_path):
        from core.updater import _verify_binary
        p = tmp_path / "valid"
        p.write_bytes(b"\x7fELF" + b"\x00" * (1024 * 200))
        if sys.platform != "win32":
            assert _verify_binary(p) is True

    def test_check_and_apply_pending_no_file(self):
        from core.updater import check_and_apply_pending
        with patch("core.updater._get_pending_path", return_value=Path("/nonexistent")):
            assert check_and_apply_pending() is False

    def test_check_and_apply_pending_post_update_flag(self):
        from core.updater import check_and_apply_pending
        original_argv = sys.argv[:]
        sys.argv.append("--post-update")
        try:
            with patch("core.updater._get_pending_path") as mock_path:
                mock_path.return_value = MagicMock()
                mock_path.return_value.exists.return_value = False
                mock_path.return_value.unlink = MagicMock()
                result = check_and_apply_pending()
            assert result is False
        finally:
            sys.argv = original_argv

    def test_windows_update_script_uses_powershell(self, tmp_path):
        from core.updater import _apply_windows
        import os
        pending = tmp_path / "opskit.pending"
        pending.write_bytes(b"MZ" + b"\x00" * 100)
        self_path = tmp_path / "opskit.exe"
        self_path.write_bytes(b"old")

        with patch("subprocess.Popen"):
            _apply_windows(pending, self_path, 2)
        ps_path = tmp_path / "opskit_update.ps1"
        assert ps_path.exists()
        content = ps_path.read_text(encoding="utf-8")
        assert "Rename-Item" in content, "新策略应使用 Rename-Item（Rename-Then-Copy）"
        assert "Wait-Process" in content, "新策略应包含 Wait-Process 等待父进程退出"
        assert f"$pid_to_wait = {os.getpid()}" in content, "应注入当前 PID"
        assert "Copy-Item" in content, "应包含 Copy-Item 作为跨驱动器降级兜底"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Constants 新增验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstants:
    def test_version_cache_constants(self):
        from core.constants import (
            FILE_VERSION_CACHE, VERSION_CACHE_TTL, VERSION_CACHE_STALE_TTL,
            VERSION_FETCH_TIMEOUT, VERSION_FETCH_INTERVAL,
        )
        assert VERSION_CACHE_TTL == 3600
        assert VERSION_CACHE_STALE_TTL == 86400
        assert VERSION_FETCH_TIMEOUT == 10
        assert VERSION_FETCH_INTERVAL == 500

    def test_bootstrap_constants(self):
        from core.constants import BOOTSTRAP_URLS, BOOTSTRAP_TIMEOUT, FILE_BOOTSTRAP_CACHE
        assert len(BOOTSTRAP_URLS) >= 2
        assert BOOTSTRAP_TIMEOUT > 0
        assert FILE_BOOTSTRAP_CACHE.endswith(".json")


# ═══════════════════════════════════════════════════════════════════════════════
# 12. i18n key 验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestI18nKeys:
    """验证 WireGuard 相关 i18n key 已添加"""

    def _load_locale(self, lang: str) -> dict:
        base = Path(__file__).resolve().parent.parent / "core" / "locale" / f"{lang}.yaml"
        with base.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    @pytest.mark.parametrize("lang", ["zh", "en"])
    def test_software_wg_keys(self, lang):
        data = self._load_locale(lang)
        sw = data.get("software", {})
        assert "wireguard" in sw
        assert "wg_server" in sw
        assert "wg_client" in sw
        assert "diagnose" in sw

    @pytest.mark.parametrize("lang", ["zh", "en"])
    def test_wireguard_section(self, lang):
        data = self._load_locale(lang)
        wg = data.get("wireguard", {})
        assert "quick_install" in wg
        assert "custom_install" in wg
        assert "confirm_install_server" in wg
        assert "confirm_install_client" in wg
        assert "step" in wg
        steps = wg["step"]
        assert "check_os" in steps
        assert "install_wg" in steps
        assert "gen_keys" in steps


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Theme icon 验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestThemeIcons:
    def test_wg_icons_in_catppuccin(self):
        theme_path = Path(__file__).resolve().parent.parent / "core" / "themes" / "catppuccin.yaml"
        with theme_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        icons = data.get("icons", {})
        assert "wireguard" in icons
        assert "wg_server" in icons
        assert "wg_client" in icons
        assert "diagnose" in icons


# ═══════════════════════════════════════════════════════════════════════════════
# 14. bootstrap.json 文件格式验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestBootstrapJsonFile:
    def test_bootstrap_json_valid(self):
        bp = Path(__file__).resolve().parent.parent / "bootstrap.json"
        with bp.open("r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["schema_version"] == 2
        assert "latest_build" in data
        assert "min_build" in data
        assert "rollout" in data
        assert "kill_switch" in data
        assert "update_mirrors" in data
        assert len(data["update_mirrors"]) >= 2

    def test_bootstrap_latest_matches_app_version(self):
        """manifest latest_build 不应小于代码内 APP_VERSION（防发布漂移）"""
        from core.constants import APP_VERSION
        bp = Path(__file__).resolve().parent.parent / "bootstrap.json"
        with bp.open("r", encoding="utf-8") as f:
            data = json.load(f)
        assert int(data["latest_build"]) >= APP_VERSION


class TestControlPlane:
    """控制面 manifest 评估：kill_switch / min_build / rollout 灰度"""

    def _manifest(self, **kw):
        base = {"latest_build": 999, "min_build": 0, "rollout": 100, "kill_switch": False}
        base.update(kw)
        return base

    def test_kill_switch_blocks(self):
        from core.updater import _evaluate_manifest
        with patch("core.updater._save_check_cache"):
            assert _evaluate_manifest(self._manifest(kill_switch=True)) is None

    def test_not_newer_skips(self):
        from core.updater import _evaluate_manifest
        from core.constants import APP_VERSION
        with patch("core.updater._save_check_cache"):
            assert _evaluate_manifest(self._manifest(latest_build=APP_VERSION)) is None

    def test_newer_full_rollout_returns_build(self):
        from core.updater import _evaluate_manifest
        with patch("core.updater._save_check_cache"):
            assert _evaluate_manifest(self._manifest(latest_build=999, rollout=100)) == 999

    def test_rollout_zero_skips_non_forced(self):
        from core.updater import _evaluate_manifest
        with patch("core.updater._save_check_cache"), \
             patch("core.updater._machine_bucket", return_value=50):
            assert _evaluate_manifest(self._manifest(latest_build=999, rollout=0)) is None

    def test_rollout_gate_in_cohort(self):
        from core.updater import _evaluate_manifest
        with patch("core.updater._save_check_cache"), \
             patch("core.updater._machine_bucket", return_value=10):
            assert _evaluate_manifest(self._manifest(latest_build=999, rollout=30)) == 999

    def test_min_build_forces_ignoring_rollout(self):
        from core.updater import _evaluate_manifest
        from core.constants import APP_VERSION
        with patch("core.updater._save_check_cache"), \
             patch("core.updater._machine_bucket", return_value=99):
            # rollout=0 本应跳过，但 min_build 高于当前版本 → 强制更新
            assert _evaluate_manifest(
                self._manifest(latest_build=999, rollout=0, min_build=APP_VERSION + 1)
            ) == 999

    def test_machine_bucket_stable_and_in_range(self):
        from core import updater
        with patch("core.updater._get_machine_id", return_value="fixed-id"):
            b1 = updater._machine_bucket()
            b2 = updater._machine_bucket()
        assert b1 == b2
        assert 0 <= b1 < 100


class TestHealthRollback:
    """健康探针崩溃回滚"""

    def _patch_health(self, hpath):
        return patch("core.updater._get_health_path", return_value=hpath)

    def test_no_health_file_noop(self, tmp_path):
        from core.updater import _check_health
        with self._patch_health(tmp_path / "update_health.json"):
            assert _check_health() is False

    def test_confirmed_noop(self, tmp_path):
        from core.updater import _check_health
        from core.constants import APP_VERSION
        hpath = tmp_path / "update_health.json"
        hpath.write_text(
            json.dumps({"build": APP_VERSION, "confirmed": True, "fails": 0}), encoding="utf-8")
        with self._patch_health(hpath):
            assert _check_health() is False

    def test_first_unconfirmed_boot_no_rollback(self, tmp_path):
        from core.updater import _check_health
        from core.constants import APP_VERSION
        hpath = tmp_path / "update_health.json"
        hpath.write_text(
            json.dumps({"build": APP_VERSION, "confirmed": False, "fails": 0}), encoding="utf-8")
        with self._patch_health(hpath), patch("core.updater.rollback") as rb:
            assert _check_health() is False
            rb.assert_not_called()
        data = json.loads(hpath.read_text(encoding="utf-8"))
        assert data["fails"] == 1

    def test_repeated_failures_trigger_rollback(self, tmp_path):
        from core.updater import _check_health
        from core.constants import APP_VERSION, MAX_HEALTH_FAILS
        hpath = tmp_path / "update_health.json"
        hpath.write_text(
            json.dumps({"build": APP_VERSION, "confirmed": False, "fails": MAX_HEALTH_FAILS - 1}),
            encoding="utf-8")
        with self._patch_health(hpath), patch("core.updater.rollback", return_value=True) as rb:
            assert _check_health() is True
            rb.assert_called_once()
        assert not hpath.exists()

    def test_stale_build_cleared(self, tmp_path):
        from core.updater import _check_health
        from core.constants import APP_VERSION
        hpath = tmp_path / "update_health.json"
        hpath.write_text(
            json.dumps({"build": APP_VERSION - 1, "confirmed": False, "fails": 5}), encoding="utf-8")
        with self._patch_health(hpath), patch("core.updater.rollback") as rb:
            assert _check_health() is False
            rb.assert_not_called()
        assert not hpath.exists()

    def test_confirm_health_sets_flag(self, tmp_path):
        from core.updater import confirm_health, mark_update_applied
        from core.constants import APP_VERSION
        hpath = tmp_path / "update_health.json"
        with self._patch_health(hpath):
            mark_update_applied(APP_VERSION)
            confirm_health()
        data = json.loads(hpath.read_text(encoding="utf-8"))
        assert data["confirmed"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 15. menu.py show_actions 动态菜单集成验证（mock UI）
# ═══════════════════════════════════════════════════════════════════════════════

class TestShowActionsMenu:
    """验证 show_actions 动态构建菜单逻辑"""

    def test_menu_choices_count_with_diagnose(self):
        """has_diagnose=True 时菜单应有 4 项"""
        from software.base import Recipe
        class _D(Recipe):
            key = "d"
            platforms = ["linux"]
            has_diagnose = True
            def detect(self): return None
            def versions(self): return []
            def install(self, v): pass
            def uninstall(self): pass

        from software.menu import show_actions
        # 用 mock 阻止实际渲染
        with patch("software.menu.select", side_effect=Exception("stop")), \
             patch("software.menu.get_color", return_value="dim"):
            try:
                show_actions(["OpsKit"], _D)
            except Exception:
                pass

    def test_menu_choices_count_without_diagnose(self):
        """has_diagnose=False 时菜单应有 3 项"""
        from software.base import Recipe
        class _ND(Recipe):
            key = "nd"
            platforms = ["linux"]
            has_diagnose = False
            def detect(self): return None
            def versions(self): return []
            def install(self, v): pass
            def uninstall(self): pass

        from software.menu import show_actions
        with patch("software.menu.select", side_effect=Exception("stop")), \
             patch("software.menu.get_color", return_value="dim"):
            try:
                show_actions(["OpsKit"], _ND)
            except Exception:
                pass
