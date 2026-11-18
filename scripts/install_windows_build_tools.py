"""Provision Nuitka 4.3's Windows x64 scanner from a fixed, verified archive."""
import argparse
import hashlib
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import platform
import sys
import time
import zipfile

import httpx

URL = "https://www.dependencywalker.com/depends22_x64.zip"
ARCHIVE_SIZE = 468618
ARCHIVE_SHA256 = "35db68a613874a2e8c1422eb0ea7861f825fc71717d46dabf1f249ce9634b4f1"
# Independent digest recorded by Scoop Extras at this immutable revision.
PROVENANCE = "https://raw.githubusercontent.com/ScoopInstaller/Extras/1bc779eb2242a4231cae8929b6d8b5f5413811d6/bucket/depends.json"
MEMBERS = {
    "depends.chm": (164468, "e5a4e001fbfe731b5d8b9d2046c57fa1786599364366704a800d59239d0c064d"),
    "depends.dll": (12288, "7a5cae7605ae5d8c8aee3e6d8e77e455537b636b395b8f00aebe17bf8b228770"),
    "depends.exe": (566272, "57c483dc985a9757501993e969c2a7043c26517f97fd49a42b33d2d6a4193d8b"),
}


def download():
    started = time.monotonic()
    data = bytearray()
    with httpx.stream("GET", URL, timeout=5, follow_redirects=False,
                      headers={"Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("unexpected build-tool response")
        if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
            raise ValueError("encoded build-tool response is not allowed")
        # Enforce the wire budget before appending; never invoke an HTTP
        # decompressor on an unverified executable download.
        for chunk in response.iter_raw(chunk_size=65536):
            if len(chunk) > ARCHIVE_SIZE - len(data) or time.monotonic() - started > 30:
                raise ValueError("build-tool download exceeds allowance")
            data.extend(chunk)
    return bytes(data)


def verified_members(data):
    if len(data) != ARCHIVE_SIZE or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("build-tool archive digest or size mismatch")
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(MEMBERS) or set(names) != set(MEMBERS):
            raise ValueError("unexpected build-tool archive inventory")
        for name, (size, digest) in MEMBERS.items():
            info = archive.getinfo(name)
            if info.file_size != size or info.flag_bits & 1:
                raise ValueError("invalid build-tool member metadata")
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("build-tool member digest mismatch")
            result[name] = content
    return result


def install(cache, archive=None):
    cache = Path(cache).absolute()
    if os.path.lexists(cache) or cache.resolve() != cache:
        raise ValueError("build-tool cache must be new and canonical")
    if archive is None:
        data = download()
    else:
        with Path(archive).open("rb") as stream:
            data = stream.read(ARCHIVE_SIZE + 1)
    files = verified_members(data)
    # Never use extractall or adopt executable files from a shared cache.
    cache.mkdir(parents=True, mode=0o700)
    target = cache / "downloads/depends/x86_64"
    target.mkdir(parents=True, mode=0o700)
    for name, content in files.items():
        with (target / name).open("xb") as stream:
            stream.write(content)
    (cache / "opskit-tools.json").write_text(json.dumps({
        "nuitka": "4.3", "tool": "Dependency Walker 2.2 x64", "url": URL,
        "archive_sha256": ARCHIVE_SHA256, "provenance": PROVENANCE,
        "members": {name: digest for name, (_, digest) in MEMBERS.items()},
    }, indent=2) + "\n", encoding="utf-8")
    return cache


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("--archive", type=Path, help="optional previously downloaded ZIP; identical digest checks apply")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("this reviewed build tool is only for Windows x64")
    if version("Nuitka") != "4.3":
        raise RuntimeError("revalidate the build tool for this Nuitka version")
    print(install(args.cache, args.archive))
