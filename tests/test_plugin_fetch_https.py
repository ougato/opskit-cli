"""Explicit guest HTTPS settings bind secrets and refuse ambient policy."""
import os

import pytest

from core import plugin_fetch_https as https, plugin_runtime_inventory as inventory


SOURCE = "https://example.com/org/repo.git"
PROXY = {"address": "172.20.0.2", "port": 8080, "token": "a" * 64}


def profile(**auth):
    return {"schema_version": 1, "source": SOURCE, "authentication": auth}


@pytest.fixture
def work(tmp_path):
    (tmp_path / "home").mkdir(mode=0o700)
    return tmp_path


@pytest.mark.parametrize("auth", [None, profile(kind="anonymous"), profile(kind="bearer", token="fixture-token"),
                                profile(kind="basic", username="fixture-user", password="fixture-password")])
def test_explicit_settings_and_secret_free_url(work, auth, monkeypatch):
    monkeypatch.setenv("GIT_SSL_NO_VERIFY", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://wrong.example")
    url = https.configure(SOURCE, auth, PROXY, work)
    config = (work / "home/gitconfig").read_text()
    assert url == "https://example.com:443/org/repo.git"
    assert 'sslVerify = true' in config and 'followRedirects = false' in config
    assert 'allow = never' in config and '[protocol "https"]\n allow = always' in config
    assert 'helper =\n' in config and 'http://wrong.example' not in config
    assert PROXY["token"] in config and PROXY["token"] not in url
    assert config.count("Authorization:") == (auth is not None and auth["authentication"]["kind"] != "anonymous")
    if os.name != "nt":
        assert (work / "home/gitconfig").stat().st_mode & 0o777 == 0o600


def test_explicit_ca_is_private_and_new_files_only(work):
    ca = "-----BEGIN CERTIFICATE-----\nYQ==\n-----END CERTIFICATE-----\n"
    https.configure(SOURCE, profile(kind="anonymous", ca_pem=ca), PROXY, work)
    assert (work / "home/ca.pem").read_text() == ca
    if os.name != "nt":
        assert (work / "home/ca.pem").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        https.configure(SOURCE, None, PROXY, work)


@pytest.mark.parametrize("proxy", [None, {}, {**PROXY, "extra": True}, {**PROXY, "address": "127.0.0.1"},
                                  {**PROXY, "address": "::1"}, {**PROXY, "address": "proxy.example"},
                                  {**PROXY, "address": "0.0.0.0"}, {**PROXY, "port": True},
                                  {**PROXY, "port": 0}, {**PROXY, "token": "x\nsecret"}])
def test_invalid_proxy_refused_without_files_or_sensitive_errors(work, proxy):
    with pytest.raises(ValueError, match="invalid fetch proxy binding") as error:
        https.configure(SOURCE, profile(kind="bearer", token="fixture-secret"), proxy, work)
    assert "fixture-secret" not in str(error.value)
    assert list((work / "home").iterdir()) == []


def test_mismatched_profile_refused_before_writes(work):
    bound = profile(kind="bearer", token="fixture-secret")
    bound["source"] += "-other"
    with pytest.raises(ValueError, match="binding"):
        https.configure(SOURCE, bound, PROXY, work)
    assert list((work / "home").iterdir()) == []


def test_ssh_is_not_an_https_fallback(work):
    with pytest.raises(ValueError, match="HTTPS source required"):
        https.configure("git@example.com:repo.git", None, PROXY, work)
    assert list((work / "home").iterdir()) == []


def test_acquisition_dependencies_are_not_plugin_sdk_code():
    sources = inventory.fetch_snapshot()
    assert set(sources) == {"core/" + name for name in inventory.FETCH_FILES}
    assert sources["core/plugin_fetch_https.py"] == inventory.bundle_snapshot()["core/plugin_fetch_https.py"]
    for name in ["core/plugin_fetch_https.py", "core/plugin_fetch_source.py", "core/plugin_egress.py"]:
        assert name not in inventory.snapshot()
