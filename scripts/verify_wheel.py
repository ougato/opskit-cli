"""Verify published runtime inventory and RECORD against the current source."""
import argparse
import base64
import configparser
import csv
from email import message_from_bytes
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

PACKAGES = ("core", "monitor", "software", "network", "xui", "tailscale", "wireguard", "plugin")
METADATA = {"METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD", "licenses/LICENSE"}


def inventory(source):
    paths = [source / "main.py"]
    for package in PACKAGES:
        paths.extend((source / package).rglob("*.py"))
    for directory in ("locale", "themes", "mirrors"):
        paths.extend((source / "core" / directory).glob("*.yaml"))
    result = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("runtime inventory contains an invalid source file")
        result[path.relative_to(source).as_posix()] = path.read_bytes()
    return result


def verify(wheel, source):
    wheel = Path(wheel)
    if wheel.is_dir():
        candidates = list(wheel.glob("*.whl"))
        if len(candidates) != 1:
            raise ValueError("verification needs exactly one wheel")
        wheel = candidates[0]
    expected = inventory(Path(source))
    with zipfile.ZipFile(wheel) as archive:
        files = archive.infolist()
        names = [item.filename for item in files]
        if len(names) != len(set(names)) or len(names) > len(expected) + len(METADATA):
            raise ValueError("duplicate or excess wheel members")
        if sum(item.file_size for item in files) > 32 * 1024**2:
            raise ValueError("wheel exceeds verification size limit")
        folders = {name.split("/", 1)[0] for name in names if ".dist-info/" in name}
        if len(folders) != 1:
            raise ValueError("wheel needs one distribution metadata directory")
        folder = folders.pop()
        if not re.fullmatch(r"opskit-[A-Za-z0-9_.+]+\.dist-info", folder):
            raise ValueError("unexpected wheel distribution")
        extras = set(names) - set(expected)
        if extras - {folder + "/" + name for name in METADATA}:
            raise ValueError("wheel contains unexpected files")
        if set(expected) - set(names):
            raise ValueError("wheel is missing runtime source or resources")
        metadata = message_from_bytes(archive.read(folder + "/METADATA"))
        if metadata.get("Name") != "opskit" or folder != "opskit-" + metadata.get("Version", "") + ".dist-info":
            raise ValueError("wheel distribution metadata mismatch")
        for name, content in expected.items():
            if archive.read(name) != content:
                raise ValueError("wheel runtime differs from current source: " + name)
        entrypoints = configparser.ConfigParser()
        entrypoints.read_string(archive.read(folder + "/entry_points.txt").decode("utf-8"))
        if dict(entrypoints["console_scripts"]) != {"opskit": "main:app"}:
            raise ValueError("unexpected CLI entry points")
        record_name = folder + "/RECORD"
        rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))))
        if any(len(row) != 3 for row in rows) or len(rows) != len(names) or {row[0] for row in rows} != set(names):
            raise ValueError("wheel RECORD inventory mismatch")
        for name, digest, size in rows:
            if name == record_name:
                if digest or size:
                    raise ValueError("invalid self-referential RECORD")
                continue
            data = archive.read(name)
            actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
            if digest != "sha256=" + actual or size != str(len(data)):
                raise ValueError("wheel RECORD digest mismatch")
    return {"runtime_files": len(expected), "wheel_members": len(names),
            "sha256": hashlib.sha256(Path(wheel).read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.wheel, arguments.source), sort_keys=True))
