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


@pytest.mark.parametrize("answer", ["", "C:/private/repo.json"])
def test_missing_credentials_can_cancel(monkeypatch, answer):
    monkeypatch.setattr(menu, "text_input", lambda **kw: answer)
    if not answer:
        with pytest.raises(UserCancel):
            menu._fetch_interaction("git@example.com:team/repo", "fetch_ssh_auth_failed")
    else:
        assert menu._fetch_interaction("git@example.com:team/repo", "fetch_ssh_auth_failed") == answer
