"""Track inherited static candidates without deleting unreviewed public APIs."""
from collections import Counter
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys


def compare(current, baseline):
    """Compare counts by file/rule/message, independent of shifting line numbers."""
    def counts(items):
        result = Counter()
        for item in items:
            count = item.get("count", 1)
            if type(count) is not int or count < 1:
                raise ValueError("invalid static baseline count")
            result[(item["file"], item["code"], item["message"])] += count
        return result
    found, allowed = counts(current), counts(baseline)
    return found - allowed, allowed - found


def check(root):
    baseline = json.loads((root / "docs/static-debt-baseline.json").read_text(encoding="utf-8"))
    if version("ruff") != baseline["ruff_version"]:
        raise ValueError("static baseline requires its pinned Ruff version")
    result = subprocess.run([sys.executable, "-m", "ruff", "check", str(root),
                             "--select", "F401,F841", "--output-format", "json"],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode not in (0, 1):
        raise RuntimeError("Ruff static scan failed")
    findings = [{"file": Path(item["filename"]).relative_to(root).as_posix(),
                 "code": item["code"], "message": item["message"]}
                for item in json.loads(result.stdout)]
    added, resolved = compare(findings, baseline["findings"])
    for (file, code, message), count in sorted(added.items()):
        print(f"{file}: {code} {message} ({count} additional)")
    if resolved:
        print("Remove resolved findings from the baseline in the same change.")
    print(json.dumps({"current_candidates": len(findings), "new_candidates": sum(added.values()),
                      "resolved_baseline_candidates": sum(resolved.values())}))
    return 1 if added or resolved else 0


if __name__ == "__main__":
    raise SystemExit(check(Path(__file__).resolve().parents[1]))
