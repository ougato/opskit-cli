"""Entry point output setup keeps redirected streams under caller ownership."""
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.console_bootstrap import configure_standard_streams


def test_borrowed_streams_are_preserved_and_each_configured_independently(monkeypatch):
    class Unconfigurable(io.StringIO):
        def reconfigure(self, **kwargs):
            raise ValueError("stream is closed or already read")

    class Configurable(io.StringIO):
        def reconfigure(self, **kwargs):
            self.options = kwargs

    stdout, stderr = Unconfigurable(), Configurable()
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    configure_standard_streams()
    assert sys.stdout is stdout and sys.stderr is stderr
    assert not stdout.closed and not stderr.closed
    assert stderr.options == {"encoding": "utf-8", "errors": "replace"}


def test_explicit_encoding_is_respected(monkeypatch):
    class Stream(io.StringIO):
        def reconfigure(self, **kwargs):
            raise AssertionError("explicit encoding must be preserved")

    stream = Stream()
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    configure_standard_streams()
    assert os.environ["PYTHONIOENCODING"] == "latin-1"


def test_import_main_preserves_redirected_streams(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, OPSKIT_DATA_DIR=str(tmp_path / "data"))
    code = """
import io, sys
from contextlib import redirect_stdout, redirect_stderr
out, err = io.StringIO(), io.StringIO()
with redirect_stdout(out), redirect_stderr(err):
    import main
    assert sys.stdout is out and sys.stderr is err
    assert not out.closed and not err.closed
    print('loaded')
assert out.getvalue() == 'loaded\\n'
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=project_root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX startup path")
@pytest.mark.parametrize("entry", ["import", "direct", "console_script"])
def test_posix_entry_configures_streams_before_cli_load(tmp_path, entry):
    root = Path(__file__).resolve().parents[1]
    marker = tmp_path / "configured"
    (tmp_path / "sitecustomize.py").write_text(
        "import os\n"
        "from core import console_bootstrap\n"
        "original = console_bootstrap.configure_standard_streams\n"
        "def record():\n"
        "    with open(os.environ['OPSKIT_TEST_MARKER'], 'a') as marker:\n"
        "        marker.write('configured\\n')\n"
        "    original()\n"
        "console_bootstrap.configure_standard_streams = record\n",
        encoding="utf-8",
    )
    env = dict(os.environ, OPSKIT_DATA_DIR=str(tmp_path / "data"),
               OPSKIT_TEST_MARKER=str(marker), VIRTUAL_ENV=str(tmp_path))
    env["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(root), env.get("PYTHONPATH", "")))
    if entry == "import":
        command = [sys.executable, "-c", "import main"]
    elif entry == "direct":
        command = [sys.executable, str(root / "main.py"), "--help"]
    else:
        # setuptools' console script resolves the public main:app entry point.
        command = [sys.executable, "-c", "from main import app; app()", "--help"]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == ["configured"]
