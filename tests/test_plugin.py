"""外部插件发现与加载测试"""
from __future__ import annotations

import os
import stat
import sys
import textwrap
import time
from pathlib import Path

import pytest

from core.loader import discover_modules
from core.module import ModuleInfo
from core.plugin import discover_plugins, list_manifests, load_manifest, load_plugin, unload_plugin
from core.plugin_trust import compute_fingerprint, grant, is_trusted, revoke


def _write_manifest(plugin_dir: Path, content: str) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(textwrap.dedent(content), encoding="utf-8")


def _make_python_plugin(root: Path, name: str = "demo", api_version: int = 1) -> Path:
    plugin_dir = root / name
    _write_manifest(plugin_dir, f"""\
        name: {name}
        version: 1.0.0
        api_version: {api_version}
        kind: python
        entry: {name}_pkg
        order: 55
        icon: "🚀"
        label:
          zh: 演示插件
          en: Demo Plugin
    """)
    pkg = plugin_dir / f"{name}_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent(f"""\
        from core.module import ModuleInfo


        def register() -> ModuleInfo:
            return ModuleInfo(
                key="{name}",
                description_key="plugin.{name}.desc",
                order=1,
                entry=lambda: None,
            )
    """), encoding="utf-8")
    return plugin_dir


def _make_exec_plugin(root: Path, name: str = "extool") -> Path:
    plugin_dir = root / name
    _write_manifest(plugin_dir, f"""\
        name: {name}
        version: 0.1.0
        api_version: 1
        kind: exec
        entry: bin/run.sh
        label:
          en: Exec Tool
    """)
    bin_dir = plugin_dir / "bin"
    bin_dir.mkdir()
    script = bin_dir / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return plugin_dir


def _trust(plugin_dir: Path) -> None:
    """测试辅助：模拟用户已确认信任插件当前内容"""
    grant(plugin_dir.name, compute_fingerprint(plugin_dir), "test")


@pytest.fixture()
def plugins_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "plugins").mkdir()
    yield tmp_path / "plugins"
    # 清理 sys.path / sys.modules 注入
    sys.path[:] = [p for p in sys.path if not p.startswith(str(tmp_path))]
    for mod in list(sys.modules):
        if mod.endswith("_pkg"):
            sys.modules.pop(mod, None)


def test_python_plugin_discovered(plugins_root) -> None:
    _trust(_make_python_plugin(plugins_root))
    modules = discover_plugins()
    assert len(modules) == 1
    m = modules[0]
    assert isinstance(m, ModuleInfo)
    assert m.key == "demo"
    assert m.order == 55  # 清单覆盖 register() 里的值
    assert m.icon == "🚀"
    assert m.label in ("演示插件", "Demo Plugin")


def test_exec_plugin_discovered(plugins_root) -> None:
    _trust(_make_exec_plugin(plugins_root))
    modules = discover_plugins()
    assert len(modules) == 1
    assert modules[0].key == "extool"
    assert modules[0].label == "Exec Tool"
    assert callable(modules[0].entry)


def test_invalid_manifest_skipped(plugins_root) -> None:
    _write_manifest(plugins_root / "bad", """\
        name: bad
        kind: python
    """)  # 缺 version / api_version / entry
    assert discover_plugins() == []


def test_incompatible_api_version_skipped(plugins_root) -> None:
    _trust(_make_python_plugin(plugins_root, name="future", api_version=999))
    assert discover_plugins() == []


def test_invalid_name_skipped(plugins_root) -> None:
    _write_manifest(plugins_root / "badname", """\
        name: Bad-Name
        version: 1.0.0
        api_version: 1
        kind: python
        entry: pkg
    """)
    assert discover_plugins() == []


def test_builtin_key_conflict_skipped(plugins_root) -> None:
    _trust(_make_python_plugin(plugins_root, name="software"))
    assert discover_plugins(builtin_keys={"software"}) == []


def test_broken_plugin_does_not_break_others(plugins_root) -> None:
    _trust(_make_python_plugin(plugins_root, name="good"))
    broken = plugins_root / "broken"
    _write_manifest(broken, """\
        name: broken
        version: 1.0.0
        api_version: 1
        kind: python
        entry: broken_pkg
    """)
    pkg = broken / "broken_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    _trust(broken)
    modules = discover_plugins()
    assert [m.key for m in modules] == ["good"]


def test_plugin_sys_exit_on_import_isolated(plugins_root) -> None:
    """插件 import 期 sys.exit() 不得杀死主程序"""
    exiting = plugins_root / "exiting"
    _write_manifest(exiting, """\
        name: exiting
        version: 1.0.0
        api_version: 1
        kind: python
        entry: exiting_pkg
    """)
    pkg = exiting / "exiting_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    _trust(exiting)
    assert discover_plugins() == []


def test_plugin_entry_sys_exit_guarded(plugins_root, monkeypatch, capsys) -> None:
    """插件菜单入口运行期 sys.exit() 被守卫拦截，不抛出"""
    plugin_dir = _make_python_plugin(plugins_root, name="exiter")
    pkg = plugin_dir / "exiter_pkg"
    (pkg / "__init__.py").write_text(textwrap.dedent("""\
        import sys
        from core.module import ModuleInfo


        def register() -> ModuleInfo:
            return ModuleInfo(
                key="exiter",
                description_key="plugin.exiter.desc",
                order=1,
                entry=lambda: sys.exit(2),
            )
    """), encoding="utf-8")
    _trust(plugin_dir)
    modules = discover_plugins()
    assert len(modules) == 1
    monkeypatch.setattr("core.prompt.pause", lambda *a, **k: None)
    modules[0].entry()  # 不得抛出 SystemExit


def test_entry_shadowing_core_rejected(plugins_root) -> None:
    """entry 包名与主程序模块重名时拒绝加载"""
    shadow = plugins_root / "shadow"
    _write_manifest(shadow, """\
        name: shadow
        version: 1.0.0
        api_version: 1
        kind: python
        entry: core
    """)
    pkg = shadow / "core"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    _trust(shadow)
    assert discover_plugins() == []


def test_untrusted_plugin_not_loaded(plugins_root) -> None:
    _make_python_plugin(plugins_root)
    assert discover_plugins() == []


def test_changed_plugin_requires_retrust(plugins_root) -> None:
    plugin_dir = _make_python_plugin(plugins_root)
    _trust(plugin_dir)
    assert len(discover_plugins()) == 1
    (plugin_dir / "demo_pkg" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    assert discover_plugins() == []  # 内容变化后信任失效
    _trust(plugin_dir)
    assert len(discover_plugins()) == 1


def test_trust_revoke(plugins_root) -> None:
    plugin_dir = _make_python_plugin(plugins_root)
    fp = compute_fingerprint(plugin_dir)
    grant("demo", fp, "1.0.0", "https://example.com/demo.git")
    assert is_trusted("demo", fp)
    revoke("demo")
    assert not is_trusted("demo", fp)


def test_exec_entry_escape_rejected(plugins_root) -> None:
    _write_manifest(plugins_root / "escape", """\
        name: escape
        version: 1.0.0
        api_version: 1
        kind: exec
        entry: ../../outside.sh
    """)
    _trust(plugins_root / "escape")
    assert discover_plugins() == []


def test_discover_modules_excludes_plugins(plugins_root) -> None:
    """外部插件不进主菜单，只在插件工具内展示"""
    _trust(_make_python_plugin(plugins_root))
    keys = [m.key for m in discover_modules()]
    assert "demo" not in keys
    assert "plugin" in keys


def test_loaded_plugins_hot_scan(plugins_root) -> None:
    """loaded_plugins 实时扫描：新增插件无需重启即可被发现"""
    from plugin import commands
    assert commands.loaded_plugins() == []
    _trust(_make_python_plugin(plugins_root))
    pairs = commands.loaded_plugins()
    assert [info.key for _m, info in pairs] == ["demo"]


def test_load_plugin_single(plugins_root) -> None:
    plugin_dir = _make_python_plugin(plugins_root)
    manifest = load_manifest(plugin_dir)
    assert manifest is not None
    assert load_plugin(manifest) is None  # 未信任不加载
    _trust(plugin_dir)
    info = load_plugin(manifest)
    assert info is not None and info.key == "demo"


def test_unload_plugin_purges_modules(plugins_root) -> None:
    plugin_dir = _make_python_plugin(plugins_root)
    _trust(plugin_dir)
    manifest = load_manifest(plugin_dir)
    assert load_plugin(manifest) is not None
    assert "demo_pkg" in sys.modules
    unload_plugin(manifest)
    assert "demo_pkg" not in sys.modules
    assert str(plugin_dir.resolve()) not in sys.path


def test_hot_reload_after_update(plugins_root) -> None:
    """更新后 unload + 重新加载，新代码当场生效"""
    plugin_dir = _make_python_plugin(plugins_root)
    _trust(plugin_dir)
    manifest = load_manifest(plugin_dir)
    assert load_plugin(manifest) is not None
    pkg_init = plugin_dir / "demo_pkg" / "__init__.py"
    pkg_init.write_text(pkg_init.read_text(encoding="utf-8").replace("order=1", "order=77"), encoding="utf-8")
    now = time.time()
    os.utime(pkg_init, (now + 10, now + 10))  # 确保 mtime 变化，避免命中旧 .pyc
    assert load_plugin(manifest) is None  # 内容变化后信任失效
    _trust(plugin_dir)
    unload_plugin(manifest)
    info = load_plugin(manifest)
    assert info is not None
    assert sys.modules["demo_pkg"].register().order == 77


def test_list_manifests_and_load_manifest(plugins_root) -> None:
    plugin_dir = _make_python_plugin(plugins_root)
    manifests = list_manifests()
    assert len(manifests) == 1
    m = load_manifest(plugin_dir)
    assert m is not None
    assert m.name == "demo"
    assert m.version == "1.0.0"
    assert m.kind == "python"
    assert m.display_label("zh") == "演示插件"
    assert m.display_label("en") == "Demo Plugin"
