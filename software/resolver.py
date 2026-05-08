"""依赖解析器 — 安装前自动检测并静默解决依赖链"""
from __future__ import annotations

from software.base import InstallError
from software.registry import get as registry_get
from core.prompt import confirm, UserCancel
from core.i18n import t


def _parse_dep(dep: str | dict) -> tuple[str, str | None]:
    """统一解析依赖声明，返回 (key, min_version_or_None)"""
    if isinstance(dep, str):
        return dep, None
    return dep["key"], dep.get("min")


def _ver_tuple(v: str) -> tuple[int, ...]:
    """将版本字符串转为可比较的 tuple，忽略非数字部分"""
    parts = []
    for seg in v.split(".")[:3]:
        try:
            parts.append(int(seg))
        except ValueError:
            break
    return tuple(parts)


def _version_lt(current: str, required: str) -> bool:
    """判断 current < required"""
    return _ver_tuple(current) < _ver_tuple(required)


def resolve_deps(
    recipe,
    breadcrumb: list[str],
    _visiting: set[str] | None = None,
    _depth: int = 0,
) -> None:
    """
    递归解析并安装 recipe 的依赖链。

    - 依赖缺失 → 静默安装（进度条中透明完成）
    - 版本低于要求 → 交互提示，用户确认后安装隔离版本
    - 循环依赖 / 超深 / 未注册 → InstallError
    """
    from core.constants import MAX_DEP_DEPTH

    if _depth > MAX_DEP_DEPTH:
        raise InstallError(t("deps.too_deep", max=MAX_DEP_DEPTH))

    if _visiting is None:
        _visiting = set()

    # 将当前 recipe 的 key 加入 visiting，防止依赖链中再次出现自己
    current_key = getattr(recipe.__class__, "key", None)
    if current_key:
        _visiting = _visiting | {current_key}

    deps = getattr(recipe.__class__, "dependencies", []) or []
    if not deps:
        return

    for dep in deps:
        key, min_ver = _parse_dep(dep)

        if key in _visiting:
            raise InstallError(t("deps.cycle", dep=key, chain=" → ".join(_visiting)))

        dep_cls = registry_get(key)
        if dep_cls is None:
            raise InstallError(t("deps.not_found", dep=key))

        dep_inst = dep_cls()
        current = dep_inst.system_version()

        if current is None:
            _install_dep(dep_inst, key, min_ver, breadcrumb, _visiting, _depth)
        elif min_ver and _version_lt(current, min_ver):
            _upgrade_dep(dep_inst, key, current, min_ver, breadcrumb, _visiting, _depth)
        # else: 满足要求，跳过


def _install_dep(
    dep_inst,
    key: str,
    min_ver: str | None,
    breadcrumb: list[str],
    visiting: set[str],
    depth: int,
) -> None:
    """静默安装缺失的依赖"""
    from rich.console import Console
    from core.theme import get_color, get_icon
    _con = Console()
    target = min_ver or "latest"
    muted = get_color("muted")
    text = get_color("text")
    _con.print(f"  [{muted}]{t('deps.installing', dep=key, target=target)}[/{muted}]")

    # 先递归解依赖的依赖
    resolve_deps(dep_inst, breadcrumb, visiting | {key}, depth + 1)

    try:
        dep_inst.install(target)
    except Exception as e:
        raise InstallError(t("deps.install_failed", dep=key, error=e)) from e


def _upgrade_dep(
    dep_inst,
    key: str,
    current: str,
    required: str,
    breadcrumb: list[str],
    visiting: set[str],
    depth: int,
) -> None:
    """版本不满足时提示用户确认后安装隔离版本"""
    from rich.console import Console
    from core.theme import get_color, get_icon
    _con = Console()
    warn = get_color("warning")
    muted = get_color("muted")
    error_c = get_color("error")
    success_c = get_color("success")

    _con.print(
        f"\n  [bold {warn}]{get_icon('warning')} {t('deps.conflict_title')}[/bold {warn}]  "
        f"[{muted}]{t('deps.conflict_desc', dep=key, current=current, required=required)}[/{muted}]"
    )
    _con.print(
        f"  [{muted}]{t('deps.conflict_hint')}[/{muted}]\n"
    )

    try:
        ok = confirm(breadcrumb=breadcrumb, prompt=t("deps.confirm_upgrade", dep=key, required=required))
    except UserCancel:
        raise InstallError(t("deps.user_cancel", dep=key, required=required, current=current))

    if not ok:
        raise InstallError(t("deps.version_mismatch", dep=key, required=required, current=current))

    resolve_deps(dep_inst, breadcrumb, visiting | {key}, depth + 1)
    try:
        dep_inst.install(required)
    except Exception as e:
        raise InstallError(t("deps.upgrade_failed", dep=key, error=e)) from e
