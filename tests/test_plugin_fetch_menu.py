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


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("approved", [True, False])
def test_private_confirmation_is_scoped(monkeypatch, language, approved):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    def confirm(**kw):
        assert kw["info_lines"] == ["ssh://git@example.com:22/team/repo"]
        assert kw["prompt"] == i18n.t("plugin_native.fetch_private_confirm")
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
