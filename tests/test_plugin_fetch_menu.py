"""Trusted menu collects per-acquisition scope without persisting credentials."""
import pytest

from core import i18n
from core.prompt import UserCancel
from plugin import menu


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("source,key,private,credential", [
    ("https://example.com/team/repo", "1", False, False),
    ("https://example.com/team/repo", "3", True, True),
    ("git@example.com:team/repo", "2", False, True),
    ("git@example.com:team/repo", "3", True, True),
])
def test_remote_options_and_prompts_are_scoped_and_localized(monkeypatch, language, source, key, private, credential):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    seen = []
    def select(**kwargs):
        seen.append(kwargs)
        return key
    def text_input(**kwargs):
        assert credential
        assert kwargs["prompt"] == i18n.t("plugin_native.fetch_profile_prompt")
        return "C:\\private\\repo.json"
    monkeypatch.setattr(menu, "select", select)
    monkeypatch.setattr(menu, "text_input", text_input)
    options = menu._fetch_options(source)
    assert options == {"allow_private": private, "fetch_profile": "C:\\private\\repo.json" if credential else None}
    assert seen[0]["subtitle"] == i18n.t("plugin_native.fetch_access")
    assert "example.com" in seen[0]["description"]
    assert i18n.t("plugin_native.fetch_access_hint") in seen[0]["description"]
    if source.startswith("git@"):
        assert {item["key"] for item in seen[0]["choices"]} == {"2", "3"}
    assert "plugin_native." not in str(seen)


def test_local_source_does_not_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "select", lambda **kwargs: pytest.fail("local source requested network access"))
    assert menu._fetch_options(str(tmp_path)) == {}


@pytest.mark.parametrize("key", [None, "invalid", "1"])
def test_ssh_cannot_select_unauthenticated_or_invalid_access(monkeypatch, key):
    monkeypatch.setattr(menu, "select", lambda **kwargs: key)
    with pytest.raises(UserCancel):
        menu._fetch_options("git@example.com:team/repo")


def test_invalid_source_is_safe_and_never_prompts(monkeypatch):
    messages = []
    monkeypatch.setattr(menu, "print_error", messages.append)
    monkeypatch.setattr(menu, "select", lambda **kwargs: pytest.fail("invalid source prompted"))
    with pytest.raises(UserCancel):
        menu._fetch_options("https://user:SECRET@example.com/team/repo")
    assert messages == [i18n.t("plugin_native.lifecycle.repository_invalid")]
    assert "SECRET" not in str(messages)
