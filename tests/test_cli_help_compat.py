"""Help patches are isolated, reload-safe and preserve both languages."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_help_compat_reloads_without_stacking_and_switches_language(tmp_path):
    source = Path(__file__).resolve().parents[1]
    env = dict(os.environ, OPSKIT_DATA_DIR=str(tmp_path / "data"), PYTHONDONTWRITEBYTECODE="1")
    program = """
import importlib, sys
from typer.testing import CliRunner
sys.argv = ['opskit', '--lang', 'zh']
import main
import core.cli_help as compatibility
from click import HelpFormatter
runner = CliRunner()
first = runner.invoke(main.app, ['--help'])
assert first.exit_code == 0, first.exception
assert '选项' in first.stdout, first.stdout
assert '显示帮助信息' in first.stdout, first.stdout
assert 'Show this message and exit.' not in first.stdout, first.stdout
for command in (['software', '--help'], ['plugin', '--help']):
    result = runner.invoke(main.app, command)
    assert result.exit_code == 0, result.exception
    assert '显示帮助信息' in result.stdout, result.stdout
    assert 'Show this message and exit.' not in result.stdout, result.stdout
initial_write = HelpFormatter.write_usage
importlib.reload(main)
assert HelpFormatter.write_usage is initial_write
again = runner.invoke(main.app, ['--help'])
assert again.exit_code == 0, again.exception
assert '选项' in again.stdout, again.stdout
importlib.reload(compatibility)
sys.argv = ['opskit', '--lang', 'en']
importlib.reload(main)
english = runner.invoke(main.app, ['plugin', '--help'])
assert english.exit_code == 0, english.exception
assert 'List installed plugins' in english.stdout, english.stdout
assert '列出已安装插件' not in english.stdout, english.stdout
again = runner.invoke(main.app, ['--HELP'])
assert again.exit_code == 0, again.exception
assert 'Options' in again.stdout, again.stdout
assert 'Show this help message' in again.stdout, again.stdout
print('HELP_RELOAD_OK')
"""
    result = subprocess.run([sys.executable, "-c", program], cwd=source, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HELP_RELOAD_OK"
    assert not (tmp_path / "data").exists()
