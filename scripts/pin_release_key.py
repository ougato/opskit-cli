"""Embed the operator-provided raw Ed25519 public key in a release build."""
import argparse
import base64
import json
import os
import re
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    root = parser.parse_args().root.resolve(strict=True)
    for name in ("core/constants.py", "install.sh", "install.ps1"):
        target = root / name
        if (target.resolve(strict=True) != target or not target.is_file()
                or target.stat().st_nlink != 1):
            raise ValueError(f"release target must be an unlinked regular file: {target}")
    value = os.environ["OPSKIT_RELEASE_PUBLIC_KEY"]
    if len(base64.b64decode(value, validate=True)) != 32:
        raise ValueError("release public key must be 32 bytes encoded as base64")
    path = root / "core/constants.py"
    text, count = re.subn(r'^UPDATE_SIGNING_PUBLIC_KEY = .*$',
                          'UPDATE_SIGNING_PUBLIC_KEY = ' + json.dumps(value),
                          path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    if count != 1:
        raise ValueError("release trust constant missing")
    path.write_text(text, encoding="utf-8")
    root = path.parent.parent
    for name, pattern, assignment in [
        ("install.sh", r'^RELEASE_PUBLIC_KEY=.*$', 'RELEASE_PUBLIC_KEY=' + json.dumps(value)),
        ("install.ps1", r'^\$RELEASE_PUBLIC_KEY = .*$', '$RELEASE_PUBLIC_KEY = ' + json.dumps(value)),
    ]:
        installer = root / name
        content, count = re.subn(pattern, lambda match: assignment,
                                installer.read_text(encoding="utf-8"), flags=re.MULTILINE)
        if count != 1:
            raise ValueError("installer trust constant missing")
        installer.write_text(content, encoding="utf-8")
