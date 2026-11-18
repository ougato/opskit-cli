"""CLI import-time locale must use the same bounded config authority reader."""
import pytest
import yaml

import main
from core import config, i18n
from core.bounded_yaml import MAX_BYTES


@pytest.fixture
def locale_reads(monkeypatch):
    languages = []
    def read(language):
        languages.append(language)
        return {'cli': {'help_text': language}}
    monkeypatch.setattr(i18n, '_load_lang', read)
    monkeypatch.setattr(i18n, '_detect_system_lang', lambda: 'zh')
    return languages


def test_no_config_preboot_is_readonly(locale_reads):
    path = config.get_config_path()
    assert not path.exists()
    assert main._preboot_locale() == {'help_text': 'zh'}
    assert locale_reads == ['zh']
    assert not path.parent.exists()


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_valid_config_language_is_preserved(language, locale_reads):
    config.save_config({'language': language})
    before = config.get_config_path().read_bytes()
    assert main._preboot_locale() == {'help_text': language}
    assert locale_reads == [language]
    assert config.get_config_path().read_bytes() == before


@pytest.mark.parametrize('arguments', [
    ['--lang', 'en', 'plugin', '--help'],
    ['-l', 'en', 'plugin', '--help'],
    ['-len', 'plugin', '--help'],
    ['--lang=en', 'plugin', '--help'],
    ['--theme', 'default', '--output', 'text', '--lang', 'en', 'plugin', '--help'],
])
def test_root_language_option_controls_import_time_help(arguments, locale_reads):
    config.save_config({'language': 'zh'})
    assert main._preboot_locale(arguments) == {'help_text': 'en'}
    assert locale_reads == ['en']


def test_plugin_arguments_do_not_override_host_help_language(locale_reads):
    config.save_config({'language': 'zh'})
    assert main._preboot_locale(['plugin', 'run', 'example', '--', '--lang', 'en']) == {'help_text': 'zh'}
    assert locale_reads == ['zh']


def test_plugin_help_uses_explicit_root_language():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, str(main.__file__), '--lang', 'en', 'plugin', '--help'],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'List installed plugins' in result.stdout
    assert '列出已安装插件' not in result.stdout


@pytest.mark.parametrize('body', [
    'language: en\n#' + 'x' * MAX_BYTES,
    'language: en\nanchor: &value [1]\ncopy: *value\n',
    'language: en\nnested: ' + '[' * 65 + '0' + ']' * 65,
], ids=['oversized', 'alias', 'deep'])
def test_preboot_rejects_excess_before_yaml_construction(body, locale_reads, monkeypatch):
    path = config.get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    calls = []
    original = yaml.safe_load
    def observed(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(yaml, 'safe_load', observed)
    assert main._preboot_locale() == {'help_text': 'zh'}
    assert locale_reads == ['zh']
    assert calls == []
    assert path.read_text(encoding='utf-8') == body


@pytest.mark.parametrize('body', ['language: [invalid', '[]', 'language: [en]', 'language: true'])
def test_bad_config_preboot_falls_back_without_rewriting(body, locale_reads):
    path = config.get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    assert main._preboot_locale() == {'help_text': 'zh'}
    assert locale_reads == ['zh']
    assert path.read_text(encoding='utf-8') == body
