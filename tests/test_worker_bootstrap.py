"""Source-mode spawned workers must not reinitialize the CLI application."""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows source-mode spawn")
def test_owned_worker_does_not_reimport_cli(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "spawn_probe.py").write_text(textwrap.dedent("""\
        import sys
        from network.worker import send

        def run(connection):
            send(connection, {"typer_loaded": "typer" in sys.modules,
                              "cli_loaded": "main" in sys.modules})
    """), encoding="utf-8")
    script = textwrap.dedent("""\
        import json, sys
        from pathlib import Path
        root, probe = sys.argv[1:]
        sys.path[:0] = [root, probe]
        sys.modules['__main__'].__file__ = str(Path(root) / 'main.py')
        from core.worker_bootstrap import configure_main_spawn
        configure_main_spawn()
        from network.worker import run
        from spawn_probe import run as probe_worker
        print(json.dumps(run(probe_worker, (), timeout=15, label='spawn probe')))
    """)
    result = subprocess.run([sys.executable, "-c", script, str(root), str(tmp_path)],
                            cwd=root, env=dict(os.environ), capture_output=True,
                            text=True, encoding="utf-8", timeout=25, check=True)
    assert json.loads(result.stdout) == {"typer_loaded": False, "cli_loaded": False}


def test_existing_main_spec_is_not_replaced(monkeypatch):
    from types import SimpleNamespace
    from core import worker_bootstrap
    existing = object()
    fake_main = SimpleNamespace(__spec__=existing)
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    monkeypatch.setattr(worker_bootstrap.sys, "platform", "win32")
    worker_bootstrap.configure_main_spawn()
    assert fake_main.__spec__ is existing
