"""Remote menus prompt only when the transport needs user input."""
import pytest
from core.prompt import UserCancel
from core import i18n
from plugin import menu


@pytest.mark.parametrize("source", ["https://example.com/team/repo", "git@example.com:team/repo"])
def test_remote_does_not_show_access_menu(monkeypatch, source):
    monkeypatch.setattr(menu, "select", lambda **kw: pytest.fail("unexpected access menu"))
    monkeypatch.setattr(menu, "text_input", lambda **kw: pytest.fail("premature credentials prompt"))
    assert menu._fetch_options(source) == {"fetch_interaction": menu._fetch_interaction}


def test_local_does_not_prompt(tmp_path):
    assert menu._fetch_options(str(tmp_path)) == {}
    assert menu._fetch_options(str(tmp_path), user_entered=True) == {}


@pytest.mark.parametrize("source", ["https://example.com/team/repo", "git@example.com:team/repo"])
def test_user_entered_install_allows_only_this_acquisition(monkeypatch, source):
    calls = []
    result = (object(), None)

    def install(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(menu.commands, "install", install)
    assert menu._install_with_progress(source) is result
    assert calls[0][0][0] == source
    assert calls[0][1]["allow_private"] is True
    assert menu._fetch_options(source) == {"fetch_interaction": menu._fetch_interaction}


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("approved", [True, False])
def test_private_confirmation_is_scoped(monkeypatch, language, approved):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    def confirm(**kw):
        assert kw["info_lines"] == ["ssh://git@example.com:22/team/repo", i18n.t("confirm_ui.private_detail")]
        assert kw["prompt"] == i18n.t("confirm_ui.private_connection")
        assert kw["require_explicit"] is True
        return approved
    monkeypatch.setattr(menu, "confirm", confirm)
    assert menu._fetch_interaction("ssh://git@example.com:22/team/repo", "fetch_private_denied") is approved


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("key", ["y", "n"])
def test_update_restores_progress_page_after_connection_prompt(monkeypatch, language, key):
    from io import StringIO
    from types import SimpleNamespace

    from rich.console import Console

    from core import installation_progress, prompt, theme

    output = StringIO()
    terminal = Console(file=output, force_terminal=True, force_interactive=True,
                       legacy_windows=False, color_system=None, width=110, _environ={"TERM": "xterm"})
    for module in (installation_progress, menu, prompt, theme):
        monkeypatch.setattr(module, "console", terminal)
    monkeypatch.setattr(prompt, "clear_screen", terminal.clear)
    monkeypatch.setattr(menu, "clear_screen", terminal.clear)
    monkeypatch.setattr(prompt, "_read_key", lambda: key)
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    source = "git@example.com:team/repo"
    manifest = SimpleNamespace(name="demo")
    monkeypatch.setattr(menu.commands, "trusted_record", lambda name: {"source": source})
    monkeypatch.setattr(menu.commands, "manifests", lambda: [manifest])
    monkeypatch.setattr(menu, "_display_name", lambda item: item.name)

    def update(item, *, automatic, progress, fetch_interaction):
        progress("update_check")
        progress("update_fetch")
        approved = fetch_interaction(source, "fetch_private_denied")
        assert approved is (key == "y")
        return (True, "unchanged") if approved else (False, "denied")

    monkeypatch.setattr(menu.commands, "update", update)
    menu._update_one(manifest, ["OpsKit", "Demo update page"])

    preparation, dialog, page = output.getvalue().rsplit("\x1b[2J", 2)
    assert i18n.t("confirm_ui.private_connection") in dialog
    assert source in dialog
    assert source not in page
    assert page.count("Demo update page") == 1
    for phase in ("update_wait", "update_check"):
        label = i18n.t(f"plugin_native.bootstrap.{phase}")
        assert label in preparation
        assert label not in dialog and label not in page
    assert "100%" not in preparation
    assert page.index("Demo update page") < page.index(i18n.t("plugin_native.bootstrap.update_fetch")[:5])
    assert (i18n.t("progress.failed") in page) is (key == "n")


@pytest.mark.parametrize("answer", ["", "C:/private/repo.json"])
def test_missing_credentials_can_cancel(monkeypatch, answer):
    monkeypatch.setattr(menu, "text_input", lambda **kw: answer)
    if not answer:
        with pytest.raises(UserCancel):
            menu._fetch_interaction("git@example.com:team/repo", "fetch_ssh_auth_failed")
    else:
        assert menu._fetch_interaction("git@example.com:team/repo", "fetch_ssh_auth_failed") == answer
