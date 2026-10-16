"""Use real Git to verify index, shared rules and private-exclude boundaries."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repository(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / '.gitignore').write_bytes((ROOT / '.gitignore').read_bytes())
    return tmp_path


def inspect(root):
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/verify_repository_hygiene.py'),
                             '--repo', str(root)], capture_output=True, text=True, timeout=15)
    return result, json.loads(result.stdout)


def test_shared_rules_and_examples_are_allowed(repository):
    (repository / '.env.example').write_text('PLACEHOLDER=example')
    result, report = inspect(repository)
    assert result.returncode == 0 and report['ok']
    assert report['ignore_probes'] == 38 and not report['prohibited_candidates']


@pytest.mark.parametrize('name', ['cache/private.json', 'plugins/vendor/plugin.py', '.env.local', '.env/private.json', 'dist/opskit.whl'])
def test_force_tracked_private_data_is_rejected_without_reading_contents(repository, name):
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('OWNED-SENTINEL-DO-NOT-PRINT')
    subprocess.run(['git', '-C', str(repository), 'add', '-f', '--', name], check=True, capture_output=True)
    result, report = inspect(repository)
    assert result.returncode == 1 and report['prohibited_candidates'] == [name]
    assert 'OWNED-SENTINEL-DO-NOT-PRINT' not in result.stdout + result.stderr


def test_private_excludes_cannot_replace_shared_policy(repository):
    rules = (repository / '.gitignore').read_bytes()
    (repository / '.gitignore').write_bytes(b'')
    (repository / '.git/info/exclude').write_bytes(rules)
    result, report = inspect(repository)
    assert result.returncode == 1
    assert len(report['missing_project_ignore_rules']) == 38


def test_negated_ignore_rule_is_not_accepted(repository):
    with (repository / '.gitignore').open('a') as stream:
        stream.write('\n!.env.local\n')
    result, report = inspect(repository)
    assert result.returncode == 1 and '.env.local' in report['missing_project_ignore_rules']
