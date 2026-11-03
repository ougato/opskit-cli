"""Provision Nuitka 4.3's exact Windows x64 GCC in a private build cache."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import time
import zipfile

import httpx

TAG = "15.2.0posix-13.0.0-msvcrt-r6"
FILENAME = "winlibs-x86_64-posix-seh-gcc-15.2.0-mingw-w64msvcrt-13.0.0-r6.zip"
URL = "https://github.com/brechtsanders/winlibs_mingw/releases/download/" + TAG + "/" + FILENAME
SIZE = 267135426
SHA256 = "d73d02c9caed172db0f3331fb4030e0a3b85947aefff9cc1da4dd81b7423c3a1"
PROVENANCE = "https://api.github.com/repos/brechtsanders/winlibs_mingw/releases/tags/" + TAG
ASSET_API = "https://api.github.com/repos/brechtsanders/winlibs_mingw/releases/assets/360334926"


def verify_archive(path):
    digest = hashlib.sha256()
    consumed = 0
    with Path(path).open("rb") as source:
        while chunk := source.read(1024**2):
            consumed += len(chunk)
            if consumed > SIZE:
                raise ValueError("compiler archive exceeds fixed size")
            digest.update(chunk)
    if consumed != SIZE or digest.hexdigest() != SHA256:
        raise ValueError("compiler archive size or SHA256 mismatch")


def members(archive):
    entries = archive.infolist()
    if not entries or len(entries) > 50000 or sum(entry.file_size for entry in entries) > 2 * 1024**3:
        raise ValueError("compiler archive inventory exceeds limit")
    names = set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        mode = entry.external_attr >> 16
        if (path.is_absolute() or not path.parts or path.parts[0] != "mingw64"
                or ".." in path.parts or "\\" in entry.filename or entry.flag_bits & 1
                or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})
                or any(part.endswith((".", " ")) or any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                       for part in path.parts)):
            raise ValueError("unsafe compiler archive member")
        folded = path.as_posix().casefold()
        if folded in names:
            raise ValueError("duplicate compiler archive member")
        names.add(folded)
    if "mingw64/bin/gcc.exe" not in names:
        raise ValueError("compiler executable missing")
    return entries


def install(cache, archive=None, *, source="release"):
    if source not in {"release", "api"}:
        raise ValueError("unsupported compiler download source")
    url = URL if source == "release" else ASSET_API
    cache = Path(cache).absolute()
    if os.path.lexists(cache) or cache.resolve() != cache:
        raise ValueError("compiler cache must be new and canonical")
    cache.mkdir(parents=True)
    source = Path(archive) if archive is not None else cache / FILENAME
    if archive is None:
        started = time.monotonic()
        count = 0
        with httpx.stream("GET", url, follow_redirects=True, timeout=30,
                          headers={"Accept-Encoding": "identity",
                                   "Accept": "application/octet-stream"}) as response:
            response.raise_for_status()
            if response.status_code != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("invalid compiler download response")
            with source.open("xb") as output:
                for chunk in response.iter_raw(chunk_size=1024**2):
                    count += len(chunk)
                    if count > SIZE or time.monotonic() - started > 600:
                        raise ValueError("compiler download exceeds allowance")
                    output.write(chunk)
    verify_archive(source)
    destination = cache / "downloads/gcc/x86_64" / TAG
    destination.mkdir(parents=True)
    with zipfile.ZipFile(source) as zipped:
        for entry in members(zipped):
            target = destination.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as content, target.open("xb") as output:
                    shutil.copyfileobj(content, output, length=1024**2)
    (cache / "compiler-receipt.json").write_text(json.dumps({
        "nuitka": "4.3", "url": url, "sha256": SHA256, "size": SIZE,
        "provenance": PROVENANCE, "compiler": str(destination / "mingw64/bin/gcc.exe"),
    }, indent=2), encoding="utf-8")
    return destination / "mingw64/bin/gcc.exe"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--source", choices=("release", "api"), default="release",
                        help="Official fixed release URL or GitHub asset API; same required digest")
    args = parser.parse_args()
    if sys.platform != "win32" or version("Nuitka") != "4.3":
        raise RuntimeError("this compiler provisioning is reviewed only for Windows Nuitka 4.3")
    print(install(args.cache, args.archive, source=args.source))
