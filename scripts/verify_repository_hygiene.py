"""Check Git publication boundaries; never read or print file contents."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import subprocess


ROOT_DIRECTORIES = frozenset({
    'cache', 'backups', 'plugins', 'plugin-data', 'plugin-runtimes',
    'plugin-resources', 'logs', 'data', '.opskit-update-locks',
    '.cursor', '.windsurf', '.vscode', '.idea', 'dist', 'build', '_build',
})
ANY_DIRECTORIES = frozenset({
    '__pycache__', '.venv', 'venv', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.tox', '.nox', '.hypothesis', '.env', 'env', 'ENV',
})
ROOT_FILES = frozenset({
    'config/common.yaml', 'plugin_trust.yaml', '.config.lock',
    '.plugin-trust.lock', 'opskit.exe', 'opskit.bin', '_registry.py',
})
PROBES = sorted([name + '/hygiene-probe' for name in ROOT_DIRECTORIES]
                + [name + '/hygiene-probe' for name in ANY_DIRECTORIES]
                + list(ROOT_FILES) + ['.env', '.env.local', '.env.production'])


def prohibited(name):
    parts = PurePosixPath(name).parts
    if not parts:
        return False
    leaf = parts[-1]
    environment = (leaf == '.env' or leaf.startswith('.env.')) and not leaf.endswith('.example')
    return (parts[0] in ROOT_DIRECTORIES or any(p in ANY_DIRECTORIES for p in parts[:-1])
            or name in ROOT_FILES or environment or leaf.endswith(('.pyc', '.pyo')))


def verify(root):
    def git(*args, data=None, allowed=(0,)):
        result = subprocess.run(['git', '-c', 'core.excludesFile=' + os.devnull,
                                 '-C', str(root), *args], input=data, capture_output=True, timeout=30)
        if result.returncode not in allowed:
            raise RuntimeError('Git repository boundary inspection failed')
        return result.stdout

    tracked = [os.fsdecode(n) for n in git('ls-files', '--cached', '-z').split(b'\0') if n]
    candidates = [os.fsdecode(n) for n in git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if n]
    rejected = sorted({n for n in tracked + candidates if prohibited(n)})
    raw = git('check-ignore', '--no-index', '--verbose', '-z', '--stdin',
              data=b'\0'.join(os.fsencode(n) for n in PROBES) + b'\0', allowed=(0, 1))
    fields = raw.split(b'\0')
    if fields[-1:] == [b'']:
        fields.pop()
    if len(fields) % 4:
        raise RuntimeError('Invalid Git ignore inspection response')
    project_ignored = set()
    for index in range(0, len(fields), 4):
        source, _, pattern, name = map(os.fsdecode, fields[index:index + 4])
        # Local/global excludes do not establish a shared project policy.
        if source.replace('\\', '/') == '.gitignore' and not pattern.startswith('!'):
            project_ignored.add(name)
    missing = sorted(set(PROBES) - project_ignored)
    return {'ok': not rejected and not missing, 'tracked_files': len(tracked),
            'untracked_candidates': len(candidates), 'ignore_probes': len(PROBES),
            'prohibited_candidates': rejected, 'missing_project_ignore_rules': missing,
            'scope': 'Git index/current candidates and shared ignore policy only; not secret contents, history or package inventory.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    print(json.dumps(report := verify(args.repo), ensure_ascii=True))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
