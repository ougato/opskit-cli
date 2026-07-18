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


def test_plugin_entry_user_exit_propagates(plugins_root, monkeypatch) -> None:
    """插件内 Ctrl+C（UserExit）穿透守卫，作为正常退出向上传播"""
    plugin_dir = _make_python_plugin(plugins_root, name="ctrlc")
    pkg = plugin_dir / "ctrlc_pkg"
    (pkg / "__init__.py").write_text(textwrap.dedent("""\
        from core.module import ModuleInfo
        from core.prompt import UserExit


        def _boom() -> None:
            raise UserExit


        def register() -> ModuleInfo:
            return ModuleInfo(
                key="ctrlc",
                description_key="plugin.ctrlc.desc",
                order=1,
                entry=_boom,
            )
    """), encoding="utf-8")
    _trust(plugin_dir)
    modules = discover_plugins()
    assert len(modules) == 1
    from core.prompt import UserExit
    with pytest.raises(UserExit):
        modules[0].entry()


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


def test_update_inherits_trust(plugins_root, tmp_path) -> None:
    """已信任插件经平台更新流程拉取新内容后自动继承信任，不再重复确认"""
    import subprocess

    from plugin import commands

    origin = _make_python_plugin(tmp_path / "origin_root")
    git_env = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(["git", *git_env, "-C", str(origin), "commit", "-q", "-m", "v1"], check=True)

    dest = plugins_root / "demo"
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True)
    _trust(dest)
    manifest = load_manifest(dest)
    assert commands.trust_status(manifest) == commands.TRUST_OK

    ok, msg = commands.update(manifest)
    assert (ok, msg) == (True, "unchanged")

    (origin / "demo_pkg" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(["git", *git_env, "-C", str(origin), "commit", "-q", "-m", "v2"], check=True)

    ok, msg = commands.update(manifest)
    assert (ok, msg) == (True, "updated")
    refreshed = load_manifest(dest)
    assert commands.trust_status(refreshed) == commands.TRUST_OK  # 更新后自动继承信任


def test_checksums_write_and_verify(plugins_root) -> None:
    from core.plugin_integrity import CHECK_MISMATCH, CHECK_MISSING, CHECK_OK, verify_checksums, write_checksums

    plugin_dir = _make_python_plugin(plugins_root)
    assert verify_checksums(plugin_dir) == CHECK_MISSING
    write_checksums(plugin_dir)
    assert verify_checksums(plugin_dir) == CHECK_OK
    (plugin_dir / "demo_pkg" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    assert verify_checksums(plugin_dir) == CHECK_MISMATCH


def test_checksums_mismatch_blocks_load(plugins_root) -> None:
    """内容与 CHECKSUMS.yaml 不符（可能被篡改）时即使已信任也拒绝加载"""
    from core.plugin_integrity import write_checksums

    plugin_dir = _make_python_plugin(plugins_root)
    write_checksums(plugin_dir)
    _trust(plugin_dir)
    assert len(discover_plugins()) == 1
    (plugin_dir / "demo_pkg" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    _trust(plugin_dir)  # 即便用户重新信任，清单不符仍拒绝
    assert discover_plugins() == []


def test_invalid_semver_version_rejected(plugins_root) -> None:
    plugin_dir = _make_python_plugin(plugins_root)
    manifest_path = plugin_dir / "plugin.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("version: 1.0.0", "version: abc"),
        encoding="utf-8",
    )
    assert load_manifest(plugin_dir) is None


def test_update_downgrade_requires_confirm(plugins_root, tmp_path) -> None:
    """更新后版本回退不自动继承信任，交由用户显式确认（防降级攻击）"""
    import subprocess

    from plugin import commands

    origin = _make_python_plugin(tmp_path / "origin_root")
    git_env = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(["git", *git_env, "-C", str(origin), "commit", "-q", "-m", "v1"], check=True)

    dest = plugins_root / "demo"
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True)
    _trust(dest)
    manifest = load_manifest(dest)

    manifest_path = origin / "plugin.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("version: 1.0.0", "version: 0.9.0"),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(["git", *git_env, "-C", str(origin), "commit", "-q", "-m", "downgrade"], check=True)

    ok, msg = commands.update(manifest)
    assert (ok, msg) == (True, "downgrade")
    refreshed = load_manifest(dest)
    assert commands.trust_status(refreshed) != commands.TRUST_OK


def test_git_error_reason_maps_common_failures() -> None:
    """git 失败转可读原因：认证 / 仓库不存在 / 网络，未识别取 stderr 末行"""
    import subprocess

    from core.i18n import t
    from plugin import commands

    def _result(stderr: str, code: int = 128) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=["git"], returncode=code, stdout="", stderr=stderr)

    assert commands.git_error_reason(
        _result("git@host: Permission denied (publickey).\nfatal: Could not read from remote repository.")
    ) == t("plugin.git_auth_failed")
    assert commands.git_error_reason(
        _result("ERROR: Repository not found.\nfatal: Could not read from remote repository.")
    ) == t("plugin.git_repo_not_found")
    assert commands.git_error_reason(
        _result("ssh: connect to host git.example.com port 22: Connection reset by peer\nfatal: ...")
    ) == t("plugin.git_network_failed")
    assert commands.git_error_reason(_result("fatal: some unknown failure")) == "fatal: some unknown failure"
    assert commands.git_error_reason(_result("", code=130)) == t("plugin.git_exit", code=130)


def test_install_failure_returns_readable_reason(plugins_root, tmp_path) -> None:
    """clone 失败时返回可读原因而非 Python 异常原文"""
    from plugin import commands

    manifest, err = commands.install(str(tmp_path / "no-such-repo.git"))
    assert manifest is None
    assert "returned non-zero exit status" not in err
    assert err


def test_update_untrusted_change_still_requires_confirm(plugins_root) -> None:
    """平台之外途径改动插件内容仍需重新确认信任"""
    from plugin import commands

    plugin_dir = _make_python_plugin(plugins_root)
    _trust(plugin_dir)
    (plugin_dir / "demo_pkg" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    manifest = load_manifest(plugin_dir)
    assert commands.trust_status(manifest) == commands.TRUST_CHANGED


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

def test_manifest_group_fields(plugins_root) -> None:
    plugin_dir = plugins_root / "grouped"
    _write_manifest(plugin_dir, """\
        name: grouped
        version: 1.0.0
        api_version: 1
        kind: python
        entry: grouped_pkg
        group: insight-flow
        group_icon: "📊"
        group_label:
          zh: Insight Flow
          en: Insight Flow
    """)
    m = load_manifest(plugin_dir)
    assert m is not None
    assert m.group == "insight-flow"
    assert m.group_icon == "📊"
    assert m.display_group_label("zh") == "Insight Flow"


def test_manifest_invalid_group_ignored(plugins_root) -> None:
    plugin_dir = plugins_root / "badgroup"
    _write_manifest(plugin_dir, """\
        name: badgroup
        version: 1.0.0
        api_version: 1
        kind: python
        entry: badgroup_pkg
        group: "Bad Group!"
    """)
    m = load_manifest(plugin_dir)
    assert m is not None
    assert m.group is None


def test_plugin_data_dir(plugins_root) -> None:
    from core.paths import data_dir, plugin_data_dir
    path = plugin_data_dir("insight-flow")
    assert path == data_dir() / "plugin-data" / "insight-flow"
    assert path.is_dir()
    with pytest.raises(ValueError):
        plugin_data_dir("Bad_Namespace")


def test_menu_grouping(plugins_root) -> None:
    """同 group 的插件在插件工具菜单聚合为一个入口"""
    from plugin.menu import _grouped, _group_display
    d1 = _make_python_plugin(plugins_root, "srv")
    d2 = _make_python_plugin(plugins_root, "cli2")
    for d in (d1, d2):
        manifest_path = d / "plugin.yaml"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8")
            + "group: insight-flow\ngroup_icon: \"📊\"\ngroup_label:\n  zh: Insight Flow\n",
            encoding="utf-8",
        )
        _trust(d)
    from plugin import commands
    pairs = commands.loaded_plugins()
    ungrouped, groups = _grouped(pairs)
    assert ungrouped == []
    assert set(groups) == {"insight-flow"}
    assert len(groups["insight-flow"]) == 2
    icon, label = _group_display(groups["insight-flow"])
    assert icon == "📊"
    assert label == "Insight Flow"


def test_manage_pick_grouping(plugins_root) -> None:
    """插件管理（更新 / 卸载）选择列表按 group 归组并显示插件显示名"""
    from plugin.menu import _manifest_grouped, _manifest_group_display, _manifest_item
    d1 = _make_python_plugin(plugins_root, "srv3")
    d2 = _make_python_plugin(plugins_root, "cli3")
    for d, label_zh in ((d1, "服务端"), (d2, "客户端")):
        manifest_path = d / "plugin.yaml"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8")
            + f"label:\n  zh: {label_zh}\ngroup: insight-flow\ngroup_icon: \"📊\"\n"
            + "group_label:\n  zh: Insight Flow\n",
            encoding="utf-8",
        )
    from plugin import commands
    manifests = [m for m in commands.manifests() if m.name in ("srv3", "cli3")]
    ungrouped, groups = _manifest_grouped(manifests)
    assert ungrouped == []
    assert len(groups["insight-flow"]) == 2
    icon, label = _manifest_group_display(groups["insight-flow"])
    assert icon == "📊"
    assert label == "Insight Flow"
    items = {_manifest_item(m) for m in groups["insight-flow"]}
    assert any("服务端" in i for i in items)
    assert any("客户端" in i for i in items)
    from plugin.menu import _display_name
    names = {_display_name(m) for m in groups["insight-flow"]}
    assert "Insight Flow 服务端" in names
    assert "Insight Flow 客户端" in names
