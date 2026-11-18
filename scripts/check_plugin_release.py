"""Verify CHECKSUMS.yaml against a plugin's staged tree or committed release."""
import argparse
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory

from core.plugin_integrity import CHECK_OK, verify_checksums


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    revision = parser.add_mutually_exclusive_group()
    revision.add_argument("--index", action="store_true")
    revision.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    git = ["git", "-C", str(args.repository.resolve())]
    command = ["write-tree"] if args.index else ["rev-parse", "--verify", "--end-of-options", args.ref + "^{tree}"]
    try:
        tree = subprocess.check_output([*git, *command], text=True).strip()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "release.tar"
            subprocess.run([*git, "archive", "--format=tar", "--output", str(archive), tree], check=True)
            payload = root / "plugin"
            with tarfile.open(archive) as bundle:
                bundle.extractall(payload, filter="data")
            status = verify_checksums(payload)
    except (OSError, subprocess.CalledProcessError, tarfile.TarError):
        parser.exit(1, "Could not read the plugin release tree\n")
    if status != CHECK_OK:
        parser.exit(1, f"Release CHECKSUMS.yaml validation failed ({status})\n")
    print(f"Release CHECKSUMS.yaml verified for tree {tree}")


if __name__ == "__main__":
    main()
