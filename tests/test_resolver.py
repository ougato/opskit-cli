"""resolver 依赖解析器单元测试"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


def _make_recipe(key: str, deps: list, sys_ver: str | None = "1.0.0"):
    """创建一个 mock Recipe 实例"""
    inst = MagicMock()
    inst.__class__.key = key
    inst.__class__.dependencies = deps
    inst.system_version.return_value = sys_ver
    inst.install.return_value = None
    return inst


def _make_cls(key: str, deps: list, sys_ver: str | None = "1.0.0"):
    """创建一个 mock Recipe 类"""
    cls = MagicMock()
    cls.key = key
    cls.dependencies = deps
    instance = _make_recipe(key, deps, sys_ver)
    cls.return_value = instance
    return cls, instance


class TestResolverVersionCompare:
    """版本比较逻辑测试"""

    def test_version_lt_true(self):
        from software.resolver import _version_lt
        assert _version_lt("3.9.0", "3.10") is True

    def test_version_lt_false_equal(self):
        from software.resolver import _version_lt
        assert _version_lt("3.10.0", "3.10") is False

    def test_version_lt_false_greater(self):
        from software.resolver import _version_lt
        assert _version_lt("3.11.2", "3.10") is False

    def test_version_lt_patch(self):
        from software.resolver import _version_lt
        assert _version_lt("3.10.1", "3.10.2") is True


class TestResolverParseDep:
    """依赖声明解析测试"""

    def test_parse_str(self):
        from software.resolver import _parse_dep
        key, min_ver = _parse_dep("python")
        assert key == "python"
        assert min_ver is None

    def test_parse_dict_with_min(self):
        from software.resolver import _parse_dep
        key, min_ver = _parse_dep({"key": "python", "min": "3.10"})
        assert key == "python"
        assert min_ver == "3.10"

    def test_parse_dict_without_min(self):
        from software.resolver import _parse_dep
        key, min_ver = _parse_dep({"key": "python"})
        assert key == "python"
        assert min_ver is None


class TestResolverDeps:
    """resolve_deps 主逻辑测试"""

    def test_no_deps(self):
        """无依赖时应直接通过"""
        from software.resolver import resolve_deps
        inst = _make_recipe("wg_client", [])
        resolve_deps(inst, ["OpsKit"])  # 不应抛出

    def test_dep_satisfied(self):
        """依赖版本已满足，跳过安装"""
        from software.resolver import resolve_deps
        from software.base import InstallError

        py_cls, py_inst = _make_cls("python", [], sys_ver="3.11.2")

        with patch("software.resolver.registry_get", return_value=py_cls):
            inst = _make_recipe("wg_client", [{"key": "python", "min": "3.10"}])
            resolve_deps(inst, ["OpsKit"])
            py_inst.install.assert_not_called()

    def test_dep_missing_installs(self):
        """依赖缺失时静默安装"""
        from software.resolver import resolve_deps

        py_cls, py_inst = _make_cls("python", [], sys_ver=None)

        with patch("software.resolver.registry_get", return_value=py_cls):
            inst = _make_recipe("wg_client", [{"key": "python", "min": "3.10"}])
            resolve_deps(inst, ["OpsKit"])
            py_inst.install.assert_called_once_with("3.10")

    def test_dep_version_too_low_user_confirm(self):
        """版本低于要求，用户确认后升级"""
        from software.resolver import resolve_deps

        py_cls, py_inst = _make_cls("python", [], sys_ver="3.9.0")

        with patch("software.resolver.registry_get", return_value=py_cls), \
             patch("software.resolver.confirm", return_value=True):
            inst = _make_recipe("wg_client", [{"key": "python", "min": "3.10"}])
            resolve_deps(inst, ["OpsKit"])
            py_inst.install.assert_called_once_with("3.10")

    def test_dep_version_too_low_user_cancel(self):
        """版本低于要求，用户拒绝 → InstallError"""
        from software.resolver import resolve_deps
        from software.base import InstallError

        py_cls, py_inst = _make_cls("python", [], sys_ver="3.9.0")

        with patch("software.resolver.registry_get", return_value=py_cls), \
             patch("software.resolver.confirm", return_value=False):
            inst = _make_recipe("wg_client", [{"key": "python", "min": "3.10"}])
            with pytest.raises(InstallError, match="版本不满足"):
                resolve_deps(inst, ["OpsKit"])

    def test_dep_not_registered(self):
        """依赖未注册 → InstallError"""
        from software.resolver import resolve_deps
        from software.base import InstallError

        with patch("software.resolver.registry_get", return_value=None):
            inst = _make_recipe("wg_client", [{"key": "nonexistent"}])
            with pytest.raises(InstallError, match="未在注册表"):
                resolve_deps(inst, ["OpsKit"])

    def test_circular_dep(self):
        """循环依赖 → InstallError"""
        from software.resolver import resolve_deps
        from software.base import InstallError

        inst = _make_recipe("wg_client", [{"key": "wg_client"}])
        inst.__class__.key = "wg_client"

        wg_cls = MagicMock()
        wg_cls.key = "wg_client"
        wg_cls.dependencies = [{"key": "wg_client"}]
        wg_cls.return_value = inst

        with patch("software.resolver.registry_get", return_value=wg_cls):
            with pytest.raises(InstallError, match="循环依赖"):
                resolve_deps(inst, ["OpsKit"])

    def test_dep_chain_too_deep(self):
        """依赖链超过最大深度 → InstallError"""
        from software.resolver import resolve_deps
        from software.base import InstallError

        inst = _make_recipe("a", [{"key": "b"}])
        b_cls, b_inst = _make_cls("b", [], sys_ver=None)
        b_inst.install.side_effect = lambda v: None

        with patch("software.resolver.registry_get", return_value=b_cls):
            with pytest.raises(InstallError, match="依赖链过深"):
                resolve_deps(inst, ["OpsKit"], _depth=10)

    def test_dep_install_failure_propagates(self):
        """依赖安装失败时错误必须向上传播"""
        from software.resolver import resolve_deps
        from software.base import InstallError

        py_cls, py_inst = _make_cls("python", [], sys_ver=None)
        py_inst.install.side_effect = InstallError("apt 失败")

        with patch("software.resolver.registry_get", return_value=py_cls):
            inst = _make_recipe("wg_client", [{"key": "python", "min": "3.10"}])
            with pytest.raises(InstallError, match="依赖 'python' 安装失败"):
                resolve_deps(inst, ["OpsKit"])
