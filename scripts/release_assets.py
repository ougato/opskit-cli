"""Validate the release matrix and publish complete, immutable asset sets."""
from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.directory_archive import MAX_ARCHIVE_SIZE
from core.file_digest import file_digest


MATRIX = {
    "opskit-linux-x64": ("linux", "x64", "opskit-linux-x64"),
    "opskit-linux-arm64": ("linux", "arm64", "opskit-linux-arm64"),
    "opskit-windows-x64": ("windows", "x64", "opskit-windows-x64.exe"),
    "opskit-darwin-arm64": ("darwin", "arm64", "opskit-darwin-arm64"),
}
DIRECTORY_MATRIX = {name for name, (platform, _, _) in MATRIX.items() if platform in {"linux", "windows"}}


def children(path: Path, limit: int) -> list[Path]:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("artifact directory must be a real directory")
    entries = list(itertools.islice(path.iterdir(), limit + 1))
    if len(entries) > limit:
        raise ValueError("too many artifact entries")
    return entries


def assets(root: Path, build: int) -> dict[str, Path]:
    """No glob-driven publication: require exactly the declared build matrix."""
    if build <= 0:
        raise ValueError("release build must be positive")
    directories = children(root, len(MATRIX))
    if {p.name for p in directories} != set(MATRIX):
        raise ValueError("release artifact matrix is incomplete or unexpected")
    result = {}
    base_version = None
    for directory in directories:
        platform, arch, binary = MATRIX[directory.name]
        entries = {path.name: path for path in children(directory, 9)}
        manifest = json.loads(regular_file(entries, "manifest.json", limit=1024 * 1024).read_text(encoding="utf-8"))
        expected = {binary}
        path = regular_file(entries, binary)
        digest = check_checksum(entries, binary)
        if (not isinstance(manifest, dict) or manifest.get("version") != str(build)
                or manifest.get("platform") != platform or manifest.get("arch") != arch
                or manifest.get("binary") != binary or manifest.get("sha256") != digest
                or type(manifest.get("size")) is not int or manifest["size"] != path.stat().st_size):
            raise ValueError("artifact manifest mismatch")
        if directory.name in DIRECTORY_MATRIX:
            extension = ".exe" if platform == "windows" else ""
            stem = directory.name
            required = {f"{stem}-launcher{extension}", f"{stem}.tar.xz"}
            extra = manifest.get("directory")
            if not isinstance(extra, dict) or not required <= extra.keys() or len(extra) > 3:
                raise ValueError("directory artifact manifest mismatch")
            for delta in extra.keys() - required:
                match = re.fullmatch(re.escape(stem) + r"-from-v([1-9][0-9]*)\.tar\.xz", delta)
                if match is None or int(match[1]) >= build or base_version not in (None, int(match[1])):
                    raise ValueError("directory delta name or base version mismatch")
                base_version = int(match[1])
            for name, asset in extra.items():
                path = regular_file(entries, name)
                if (not isinstance(asset, dict) or set(asset) != {"sha256", "size"}
                        or type(asset["size"]) is not int or asset["size"] != path.stat().st_size
                        or asset["sha256"] != check_checksum(entries, name)):
                    raise ValueError("directory artifact metadata mismatch")
            expected.update(extra)
        elif "directory" in manifest:
            raise ValueError("unexpected directory artifact manifest")

        expected.update(f"{name}.sha256" for name in sorted(expected))
        if set(entries) != expected | {"manifest.json"}:
            raise ValueError("unexpected or missing artifact file")
        result.update((name, entries[name]) for name in expected)
    return result


def regular_file(entries: dict[str, Path], name: str, *, limit: int | None = None) -> Path:
    path = entries.get(name)
    if path is None:
        raise ValueError("unexpected or missing artifact file")
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("artifact must be an unlinked regular file")
    size = path.stat().st_size
    if size == 0 or (limit is not None and size > limit):
        raise ValueError("artifact size out of bounds")
    return path


def check_checksum(entries: dict[str, Path], name: str) -> str:
    path = regular_file(entries, name)
    digest = file_digest(path)
    checksum = regular_file(entries, name + ".sha256", limit=1024)
    if checksum.read_text(encoding="utf-8") != f"{digest}  {name}\n":
        raise ValueError("artifact checksum mismatch")
    return digest


class GitHub:
    """Use gh's configured authentication without putting tokens in arguments."""

    def __init__(self, repository: str, run=subprocess.run):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid repository")
        self.repository = repository
        self.run = run

    def request(self, method: str, endpoint: str, data=None):
        args = ["gh", "api", "--include", "--method", method, f"repos/{self.repository}/{endpoint}"]
        if data is not None:
            args += ["--input", "-"]
        result = self.run(args, input=None if data is None else json.dumps(data),
                          capture_output=True, text=True, timeout=120)
        header, separator, body = result.stdout.replace("\r\n", "\n").partition("\n\n")
        status = re.match(r"HTTP/\S+ (\d{3})(?:\s|$)", header)
        if not separator or not status:
            raise RuntimeError("GitHub API returned no verifiable HTTP status")
        code = int(status[1])
        if result.returncode or not 200 <= code < 300:
            raise RuntimeError(f"GitHub API {method} failed with HTTP {code}")
        return json.loads(body)

    def check_tag(self, tag: str, commit: str) -> None:
        obj = self.request("GET", f"git/ref/tags/{tag}")["object"]
        for _ in range(8):
            if obj.get("type") == "commit" and obj.get("sha") == commit:
                return
            if obj.get("type") != "tag" or not re.fullmatch(r"[0-9a-f]{40,64}", obj.get("sha", "")):
                break
            obj = self.request("GET", f"git/tags/{obj['sha']}")["object"]
        raise ValueError("release tag no longer identifies the event commit")

    def upload(self, tag: str, path: Path) -> None:
        result = self.run(["gh", "release", "upload", tag, str(path), "--repo", self.repository],
                          capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise RuntimeError("release upload failed; draft retained for inspection or retry")

    def download(self, tag: str, name: str, path: Path) -> None:
        result = self.run(["gh", "release", "download", tag, "--pattern", name,
                           "--output", str(path), "--repo", self.repository],
                          capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise RuntimeError("previous directory package download failed")


def check_remote(remote, expected: dict[str, Path], *, complete: bool) -> set[str]:
    if not isinstance(remote, list) or len(remote) > len(expected):
        raise ValueError("unexpected remote asset inventory")
    seen = set()
    for asset in remote:
        name = asset.get("name")
        if name not in expected or name in seen:
            raise ValueError("unexpected or duplicate remote asset")
        path = expected[name]
        if (asset.get("state") != "uploaded" or type(asset.get("size")) is not int
                or asset["size"] != path.stat().st_size
                or asset.get("digest") != "sha256:" + file_digest(path)):
            raise ValueError("remote asset differs; refusing replacement")
        seen.add(name)
    if complete and seen != set(expected):
        raise ValueError("remote release asset set is incomplete")
    return seen


def releases(api: GitHub):
    for page in range(1, 11):
        listing = api.request("GET", f"releases?per_page=100&page={page}")
        if not isinstance(listing, list) or len(listing) > 100:
            raise ValueError("unexpected release listing")
        yield from listing
        if len(listing) < 100:
            return
    raise ValueError("release lookup exceeds 1000 entries; refusing ambiguous creation")


def public_build(release) -> int | None:
    match = re.fullmatch(r"v([1-9][0-9]*)", release.get("tag_name", ""))
    if match and release.get("draft") is False and release.get("prerelease") is False:
        return int(match[1])
    return None


def release_state(api: GitHub, tag: str):
    # Listing includes authenticated drafts; the tag endpoint can omit them.
    found = None
    newest_public = 0
    for release in releases(api):
        if release.get("tag_name") == tag:
            if found is not None:
                raise ValueError("duplicate release tag")
            found = release
        version = public_build(release)
        if version is not None:
            newest_public = max(newest_public, version)
    return found, newest_public


def previous_build(api: GitHub, build: int) -> int | None:
    if build <= 0:
        raise ValueError("release build must be positive")
    newest = None
    for release in releases(api):
        version = public_build(release)
        if version is not None and version < build and (newest is None or version > newest):
            newest = version
    return newest


def fetch_base(api: GitHub, version: int, artifact: str, output: Path) -> bool:
    if artifact not in DIRECTORY_MATRIX or version < 1 or output.exists() or output.is_symlink():
        raise ValueError("invalid previous directory package request")
    release = api.request("GET", f"releases/tags/v{version}")
    if public_build(release) != version or type(release.get("id")) is not int:
        raise ValueError("unexpected previous release identity")
    inventory = api.request("GET", f"releases/{release['id']}/assets?per_page=100")
    if not isinstance(inventory, list) or len(inventory) >= 100:
        raise ValueError("unexpected previous release asset inventory")
    names = {asset.get("name") for asset in inventory}
    required = {f"{name}.tar.xz" for name in DIRECTORY_MATRIX}
    if not names & required:
        return False  # Releases predating the directory distribution have no base.
    if not required <= names or len(names) != len(inventory):
        raise ValueError("incomplete previous directory asset matrix")
    name = f"{artifact}.tar.xz"
    asset = next(asset for asset in inventory if asset["name"] == name)
    size = asset.get("size")
    expected = asset.get("digest")
    if (asset.get("state") != "uploaded" or type(size) is not int or not 0 < size <= MAX_ARCHIVE_SIZE
            or not isinstance(expected, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected)):
        raise ValueError("unverifiable previous directory archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="base-package-", dir=output.parent) as temporary:
        package = Path(temporary, name)
        api.download(f"v{version}", name, package)
        if (regular_file({name: package}, name, limit=MAX_ARCHIVE_SIZE).stat().st_size != size
                or "sha256:" + file_digest(package) != expected):
            raise ValueError("previous directory archive checksum mismatch")
        package.replace(output)
    return True


def publish(api: GitHub, tag: str, commit: str, expected: dict[str, Path]) -> None:
    if not re.fullmatch(r"v[1-9][0-9]*", tag) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise ValueError("invalid release identity")
    api.check_tag(tag, commit)
    release, _ = release_state(api, tag)
    if release is None:
        release = api.request("POST", "releases", {
            "tag_name": tag, "target_commitish": commit, "name": tag,
            "body": "", "draft": True, "prerelease": False, "make_latest": "false"})
    if (release.get("tag_name") != tag or type(release.get("id")) is not int
            or type(release.get("draft")) is not bool or release.get("prerelease") is not False):
        raise ValueError("unexpected release identity")
    endpoint = f"releases/{release['id']}"
    inventory = api.request("GET", endpoint + "/assets?per_page=100")
    seen = check_remote(inventory, expected, complete=not release["draft"])
    if not release["draft"]:
        return  # An identical public release is read-only, including on retry.
    for name in sorted(set(expected) - seen):
        api.upload(tag, expected[name])
    check_remote(api.request("GET", endpoint + "/assets?per_page=100"), expected, complete=True)
    api.check_tag(tag, commit)
    _, newest_public = release_state(api, tag)
    published = api.request("PATCH", endpoint, {
        "draft": False, "make_latest": "true" if int(tag[1:]) >= newest_public else "false"})
    if published.get("id") != release["id"] or published.get("draft") is not False or published.get("tag_name") != tag:
        raise ValueError("release publication was not confirmed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=int)
    parser.add_argument("--root", type=Path)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--publish", action="store_true")
    operation.add_argument("--previous-build", action="store_true")
    operation.add_argument("--fetch-base", choices=sorted(DIRECTORY_MATRIX))
    args = parser.parse_args()
    if args.previous_build:
        version = previous_build(GitHub(os.environ["GITHUB_REPOSITORY"]), args.build)
        if version is not None:
            print(version)
        return
    if args.root is None:
        parser.error("--root is required for artifact validation, publication, or base download")
    if args.fetch_base:
        fetch_base(GitHub(os.environ["GITHUB_REPOSITORY"]), args.build, args.fetch_base, args.root)
        return
    expected = assets(args.root, args.build)
    if args.publish:
        publish(GitHub(os.environ["GITHUB_REPOSITORY"]), f"v{args.build}",
                os.environ["GITHUB_SHA"], expected)
    print("release asset verification completed")


if __name__ == "__main__":
    main()
