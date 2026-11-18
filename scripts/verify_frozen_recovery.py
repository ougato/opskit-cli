"""Verify frozen cleanup against source-created, test-owned crash receipts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    os.environ["OPSKIT_DATA_DIR"] = str(output / "data")
    from core.config import set_config_value
    from core.native_preparation import workspace
    from core import native_catalog_cleanup as cleanup
    set_config_value({}, "language", args.language)
    environment = dict(os.environ)
    records = []

    def run(label, arguments, success=True):
        result = subprocess.run([str(binary), "--non-interactive", "--output", "json",
                                 "plugin", *arguments], cwd=output, env=environment,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=240)
        (output / (label + ".stdout")).write_bytes(result.stdout)
        (output / (label + ".stderr")).write_bytes(result.stderr)
        records.append({"step": label, "exit_code": result.returncode})
        if (result.returncode == 0) != success:
            raise RuntimeError("frozen recovery failed at " + label)
        return json.loads(result.stdout) if success else None

    def collect(label, record, success=True, digest=None):
        return run(label, ["clean-native-cache", "prepare", record["object"],
                          "--record-sha256", digest or record["record_sha256"]], success)

    with workspace() as active:
        record, = run("inspect-active", ["inspect-native-cache", "prepare"])["data"]
        collect("refuse-active", record, False)
        if not active.is_dir():
            raise RuntimeError("active preparation removed")
    result = subprocess.run([sys.executable, "-c",
        "from core.native_preparation import workspace; import os; "
        "scope=workspace(); root=scope.__enter__(); "
        "(root/'partial.txt').write_text('synthetic crash evidence'); os._exit(27)"],
        cwd=ROOT, env=environment, capture_output=True, timeout=30)
    if result.returncode != 27:
        raise RuntimeError("crash fixture failed")
    record, = run("inspect-crash", ["inspect-native-cache", "prepare"])["data"]
    target = cleanup._root("prepare") / "objects" / record["object"]
    collect("refuse-wrong-digest", record, False, "0" * 64)
    if (target / "partial.txt").read_text() != "synthetic crash evidence":
        raise RuntimeError("wrong digest changed evidence")
    original = target.with_name(target.name + "-original")
    target.rename(original)
    target.mkdir()
    (target / "independent.txt").write_text("keep replacement")
    collect("refuse-replacement", record, False)
    if (target / "independent.txt").read_text() != "keep replacement":
        raise RuntimeError("replacement was modified")
    # Restore only our own fixture so the exact original receipt can be tested.
    (target / "independent.txt").unlink()
    target.rmdir()
    original.rename(target)
    unknown = target.parent / ("f" * 32)
    unknown.mkdir()
    (unknown / "unknown.txt").write_text("keep unknown")
    collect("collect-crash", record)
    if target.exists() or (unknown / "unknown.txt").read_text() != "keep unknown":
        raise RuntimeError("cleanup ownership mismatch")
    if run("inspect-empty", ["inspect-native-cache", "prepare"])["data"] != []:
        raise RuntimeError("cleanup receipt retained")
    (output / "verification.json").write_text(json.dumps({
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "language": args.language, "fixture_created_by": "source Python, os._exit(27)",
        "cleanup_executed_by": "frozen official CLI", "physical_crash": False,
        "active_refused": True, "wrong_digest_refused": True,
        "replacement_preserved": True, "unknown_preserved": True,
        "exact_receipt_recovered": True, "steps": records,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
