"""Compare locale parse costs and repeated cached reads on the same resource."""
import json
import platform
import statistics
import time

import yaml

from core import i18n


def measure(operation):
    values = []
    for _ in range(7):
        start = time.perf_counter()
        operation()
        values.append((time.perf_counter() - start) * 1000)
    return {"median_ms": round(statistics.median(values), 3), "runs": len(values)}


def main():
    path = i18n._get_locale_dir() / "zh.yaml"
    raw = path.read_text(encoding="utf-8")
    expected = yaml.safe_load(raw)
    assert i18n._load_lang("zh") == expected
    def cold():
        i18n._cached_lang.cache_clear()
        return i18n._load_lang("zh")
    results = {"python": platform.python_version(), "platform": platform.system(),
               "resource_bytes": path.stat().st_size, "c_safe_loader": hasattr(yaml, "CSafeLoader"),
               "old_parse": measure(lambda: yaml.safe_load(raw)), "new_cold_read": measure(cold),
               "new_cached_read": measure(lambda: i18n._load_lang("zh"))}
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
