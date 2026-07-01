"""守卫测试：卸载流程不得出现「两次任意键才能返回」。

历史问题：向导型 recipe（has_wizard=True）在自己的 uninstall() 里已经
调用 pause() 展示结果，而菜单框架 _do_uninstall 又无条件 pause 了一次，
导致用户要按两次任意键才返回。

规则（本测试锁定）：
- has_wizard=True 且卸载成功 → 框架不再 pause（结果与 pause 由 recipe 自负责）；
- has_wizard=False 且卸载成功 → 框架负责 pause 一次；
- 卸载失败 → 框架 pause 一次，保证错误可见。
"""
from __future__ import annotations

import software.menu as menu


class _FakeInstance:
    def __init__(self, ok: bool = True) -> None:
        self._ok = ok

    def detect(self):
        return "installed"

    def uninstall(self, version=None):
        if not self._ok:
            raise RuntimeError("boom")


def _make_cls(*, has_wizard: bool):
    return type(
        "FakeRecipe",
        (),
        {
            "key": "faketool",
            "description": "Fake Tool",
            "has_wizard": has_wizard,
            "has_switch": False,
            "confirm_before_uninstall": True,
        },
    )


def _patch_common(monkeypatch, pause_counter: list[int]):
    monkeypatch.setattr(menu, "pause", lambda *a, **k: pause_counter.append(1))
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(menu, "clear_screen", lambda *a, **k: None)
    monkeypatch.setattr(menu, "print_header", lambda *a, **k: None)
    monkeypatch.setattr(menu, "print_success", lambda *a, **k: None)
    monkeypatch.setattr(menu, "print_warning", lambda *a, **k: None)
    monkeypatch.setattr(menu, "report_failure", lambda *a, **k: None)
    monkeypatch.setattr(menu.base_console, "print", lambda *a, **k: None)


def test_wizard_uninstall_success_frame_does_not_pause(monkeypatch):
    calls: list[int] = []
    _patch_common(monkeypatch, calls)
    cls = _make_cls(has_wizard=True)
    menu._do_uninstall(["OpsKit"], cls, _FakeInstance(ok=True))
    assert calls == [], "向导型 recipe 卸载成功时框架不应再 pause（由 recipe 自负责）"


def test_non_wizard_uninstall_success_frame_pauses_once(monkeypatch):
    calls: list[int] = []
    _patch_common(monkeypatch, calls)
    cls = _make_cls(has_wizard=False)
    menu._do_uninstall(["OpsKit"], cls, _FakeInstance(ok=True))
    assert calls == [1], "非向导型 recipe 卸载成功时框架应 pause 恰好一次"


def test_uninstall_failure_frame_pauses_once(monkeypatch):
    for has_wizard in (True, False):
        calls: list[int] = []
        _patch_common(monkeypatch, calls)
        cls = _make_cls(has_wizard=has_wizard)
        menu._do_uninstall(["OpsKit"], cls, _FakeInstance(ok=False))
        assert calls == [1], "卸载失败时框架应 pause 恰好一次以展示错误"
