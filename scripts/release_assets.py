"""Validate the release matrix and publish complete, immutable asset sets."""
from __future__ import annotations

import argparse
import base64
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.release_signature import file_digest, payload, verify


MATRIX = {
    "opskit-linux-x64": ("linux", "x64", "opskit-linux-x64"),
    "opskit-linux-arm64": ("linux", "arm64", "opskit-linux-arm64"),
    "opskit-windows-x64": ("windows", "x64", "opskit-windows-x64.exe"),
    "opskit-darwin-arm64": ("darwin", "arm64", "opskit-darwin-arm64"),
}


def children(path: Path, limit: int) -> list[Path]:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("artifact directory must be a real directory")
    entries = list(itertools.islice(path.iterdir(), limit + 1))
    if len(entries) > limit:
        raise ValueError("too many artifact entries")
    return entries


def assets(root: Path, build: int, *, signed: bool, public_key: str = "") -> dict[str, Path]:
    """No glob-driven publication: require exactly the declared build matrix."""
    if build <= 0:
        raise ValueError("release build must be positive")
    directories = children(root, len(MATRIX))
    if {p.name for p in directories} != set(MATRIX):
        raise ValueError("release artifact matrix is incomplete or unexpected")
    result = {}
    for directory in directories:
        platform, arch, binary = MATRIX[directory.name]
        expected = {binary, binary + ".sha256", "manifest.json"}
        if signed:
            expected.update(binary + suffix for suffix in (".sig.json", ".signed", ".sig"))
        entries = children(directory, len(expected))
        if {p.name for p in entries} != expected:
            raise ValueError("unexpected or missing artifact file")
        for path in entries:
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError("artifact must be an unlinked regular file")
            if path.name != binary and path.stat().st_size > 1024 * 1024:
                raise ValueError("artifact metadata exceeds 1 MiB")
        path = directory / binary
        digest = file_digest(path)
        if (directory / (binary + ".sha256")).read_text(encoding="utf-8") != f"{digest}  {binary}\n":
            raise ValueError("artifact checksum mismatch")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("version") != str(build)
                or manifest.get("platform") != platform or manifest.get("arch") != arch
                or manifest.get("binary") != binary or manifest.get("sha256") != digest
                or type(manifest.get("size")) is not int or manifest["size"] != path.stat().st_size):
            raise ValueError("artifact manifest mismatch")
        if signed:
            envelope = json.loads((directory / (binary + ".sig.json")).read_text(encoding="utf-8"))
            if not verify(envelope, public_key, build=build, filename=binary, digest=digest):
                raise ValueError("artifact signature mismatch")
            if (directory / (binary + ".signed")).read_bytes() != payload(build, binary, digest):
                raise ValueError("signed payload mismatch")
            if (directory / (binary + ".sig")).read_bytes() != base64.b64decode(envelope["signature"], validate=True):
                raise ValueError("detached signature mismatch")
        for entry in entries:
            if entry.name != "manifest.json":
                result[entry.name] = entry
    return result


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


def release_state(api: GitHub, tag: str):
    # Listing includes authenticated drafts; the tag endpoint can omit them.
    found = None
    newest_public = 0
    for page in range(1, 11):
        releases = api.request("GET", f"releases?per_page=100&page={page}")
        if not isinstance(releases, list) or len(releases) > 100:
            raise ValueError("unexpected release listing")
        for release in releases:
            if release.get("tag_name") == tag:
                if found is not None:
                    raise ValueError("duplicate release tag")
                found = release
            match = re.fullmatch(r"v([1-9][0-9]*)", release.get("tag_name", ""))
            if match and release.get("draft") is False and release.get("prerelease") is False:
                newest_public = max(newest_public, int(match[1]))
        if len(releases) < 100:
            return found, newest_public
    raise ValueError("release lookup exceeds 1000 entries; refusing ambiguous creation")


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
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    expected = assets(args.root, args.build, signed=args.publish,
                      public_key=os.environ.get("OPSKIT_RELEASE_PUBLIC_KEY", ""))
    if args.publish:
        publish(GitHub(os.environ["GITHUB_REPOSITORY"]), f"v{args.build}",
                os.environ["GITHUB_SHA"], expected)
    print("release asset verification completed")


if __name__ == "__main__":
    main()
