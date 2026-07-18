"""插件间服务（provides / uses / get_service）与多选控件测试"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

import core.plugin_services as services
from core.plugin import load_manifest
from core.plugin_trust import compute_fingerprint, grant
from core.prompt import UserCancel, multi_select


def _write_manifest(plugin_dir: Path, content: str) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(textwrap.dedent(content), encoding="utf-8")


def _make_provider(root: Path, name: str = "storagey") -> Path:
    plugin_dir = root / name
    _write_manifest(plugin_dir, f"""\
        name: {name}
        version: 1.0.0
        api_version: 1
        kind: python
        entry: {name}_pkg
        provides:
          - storage
    """)
    pkg = plugin_dir / f"{name}_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent("""\
        from core.module import ModuleInfo


        class _Svc:
            service_api_version = 1

            def open_menu(self, breadcrumb, context):
                self.opened = (breadcrumb, context)


        def register() -> ModuleInfo:
            return ModuleInfo(key="storagey", description_key="x", order=1, entry=lambda: None)


        def provide_service(name):
            if name == "storage":
                return _Svc()
            return None
    """), encoding="utf-8")
    return plugin_dir


@pytest.fixture()
def plugins_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "plugins").mkdir()
    services.invalidate_service_cache()
    yield tmp_path / "plugins"
    services.invalidate_service_cache()
    sys.path[:] = [p for p in sys.path if not p.startswith(str(tmp_path))]
    for mod in list(sys.modules):
        if mod.endswith("_pkg"):
            sys.modules.pop(mod, None)


def _trust(plugin_dir: Path) -> None:
    grant(plugin_dir.name, compute_fingerprint(plugin_dir), "test")


def test_manifest_parses_provides_and_uses(tmp_path) -> None:
    plugin_dir = tmp_path / "p"
    _write_manifest(plugin_dir, """\
        name: p
        version: 1.0.0
        api_version: 1
        kind: python
        entry: p_pkg
        provides:
          - storage
        uses:
          - service: notify
            source: git@example.com:x/notify.git
          - metrics
    """)
    manifest = load_manifest(plugin_dir)
    assert manifest is not None
    assert manifest.provides == ["storage"]
    assert manifest.uses == [
        {"service": "notify", "source": "git@example.com:x/notify.git"},
        {"service": "metrics", "source": ""},
    ]


def test_manifest_defaults_empty_provides_uses(tmp_path) -> None:
    plugin_dir = tmp_path / "p"
    _write_manifest(plugin_dir, """\
        name: p
        version: 1.0.0
        api_version: 1
        kind: python
        entry: p_pkg
    """)
    manifest = load_manifest(plugin_dir)
    assert manifest is not None
    assert manifest.provides == []
    assert manifest.uses == []


def test_get_service_from_trusted_provider(plugins_root) -> None:
    _trust(_make_provider(plugins_root))
    svc = services.get_service("storage")
    assert svc is not None
    assert svc.service_api_version == 1
    # 缓存命中同一对象
    assert services.get_service("storage") is svc


def test_get_service_untrusted_provider_none(plugins_root) -> None:
    _make_provider(plugins_root)
    assert services.get_service("storage") is None


def test_get_service_unknown_none(plugins_root) -> None:
    assert services.get_service("nope") is None


def test_open_service_menu_calls_open_menu(plugins_root, monkeypatch) -> None:
    _trust(_make_provider(plugins_root))
    ok = services.open_service_menu("storage", breadcrumb=["OpsKit", "X"], context={"k": "v"})
    assert ok is True
    svc = services.get_service("storage")
    assert svc.opened == (["OpsKit", "X"], {"k": "v"})


def test_open_service_menu_missing_no_source(plugins_root, monkeypatch) -> None:
    monkeypatch.setattr("core.prompt.pause", lambda *a, **k: None)
    ok = services.open_service_menu("storage", breadcrumb=["OpsKit"])
    assert ok is False


# ─── multi_select ────────────────────────────────────────────────────────────

def _feed_keys(monkeypatch, keys: list[str]) -> None:
    seq = iter(keys)
    monkeypatch.setattr("core.prompt._read_key_seq", lambda: next(seq))
    monkeypatch.setattr("os.system", lambda *_: 0)


def test_multi_select_space_toggle_and_enter(monkeypatch) -> None:
    _feed_keys(monkeypatch, [" ", "DOWN", " ", "\r"])
    assert multi_select(["OpsKit"], "pick", ["a", "b", "c"]) == [0, 1]


def test_multi_select_untoggle(monkeypatch) -> None:
    _feed_keys(monkeypatch, [" ", " ", "\r"])
    assert multi_select(["OpsKit"], "pick", ["a", "b"]) == []


def test_multi_select_zero_returns_none(monkeypatch) -> None:
    _feed_keys(monkeypatch, ["0"])
    assert multi_select(["OpsKit"], "pick", ["a"]) is None


def test_multi_select_esc_raises(monkeypatch) -> None:
    _feed_keys(monkeypatch, ["\x1b"])
    with pytest.raises(UserCancel):
        multi_select(["OpsKit"], "pick", ["a"])


def test_multi_select_cursor_wraps(monkeypatch) -> None:
    _feed_keys(monkeypatch, ["UP", " ", "\r"])
    assert multi_select(["OpsKit"], "pick", ["a", "b", "c"]) == [2]


def test_multi_select_empty_options() -> None:
    assert multi_select(["OpsKit"], "pick", []) == []
