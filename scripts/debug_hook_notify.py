#!/usr/bin/env python3
"""
Reproduce GitHub Actions build-notify / artifact-pull against HOOK_URL.
Logs NDJSON to repo-root debug-ade7f6.log — never writes secret values.
Usage (PowerShell):
  $env:HOOK_URL='https://example.com'; $env:HOOK_SECRET='...'; python scripts/debug_hook_notify.py
Optional:
  python scripts/debug_hook_notify.py --also-artifact-pull
  python scripts/debug_hook_notify.py --probe-bearer   # same URL, Authorization: Bearer only (diagnosis)
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

# #region agent log
_LOG_PATH = Path(__file__).resolve().parents[1] / "debug-ade7f6.log"


def _agent_log(hypothesis_id: str, message: str, data: dict) -> None:
    row = {
        "sessionId": "ade7f6",
        "timestamp": int(time.time() * 1000),
        "hypothesisId": hypothesis_id,
        "location": "scripts/debug_hook_notify.py",
        "message": message,
        "data": data,
    }
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# #endregion


def _post_json_ex(
    url: str, body: bytes, hypothesis_base: str, auth_headers: dict[str, str]
) -> int:
    """Returns 0 on 2xx, else HTTP status or -1 on exception. auth_headers must not be logged."""
    hdrs = {
        "Content-Type": "application/json",
        "User-Agent": "opskit-debug-hook-notify/1.0",
    }
    hdrs.update(auth_headers)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=hdrs,
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            body_snip = resp.read(512)[:200]
            _agent_log(
                hypothesis_base + "_response",
                "http_ok",
                {
                    "auth_mode": sorted(auth_headers.keys()),
                    "http_status": resp.status,
                    "header_keys": sorted(resp.headers.keys()),
                    "server_header": (resp.headers.get("Server") or "")[:80],
                    "cf_ray": (resp.headers.get("CF-Ray") or "")[:80],
                    "body_prefix_b64_set": bool(body_snip),
                },
            )
            print(f"[debug-hook] OK {hypothesis_base} -> {resp.status} {url}", flush=True)
            return 0
    except urllib.error.HTTPError as e:
        www = e.headers.get("WWW-Authenticate") or ""
        err_body = b""
        try:
            err_body = e.read(512)
        except Exception:
            pass
        text_snip = err_body.decode("utf-8", errors="replace")[:240]
        _agent_log(
            hypothesis_base + "_http_error",
            "http_error",
            {
                "auth_mode": sorted(auth_headers.keys()),
                "http_status": e.code,
                "header_keys": sorted(e.headers.keys()),
                "server_header": (e.headers.get("Server") or "")[:80],
                "cf_ray": (e.headers.get("CF-Ray") or "")[:80],
                "www_authenticate_prefix": www[:120] if www else "",
                "reason": getattr(e, "reason", "") or "",
                "body_text_prefix": text_snip,
            },
        )
        print(
            f"[debug-hook] FAIL {hypothesis_base} -> HTTP {e.code} {url} "
            f"server={e.headers.get('Server')!r} cf-ray={e.headers.get('CF-Ray')!r}",
            flush=True,
        )
        return int(e.code)
    except Exception as e:
        _agent_log(
            hypothesis_base + "_exc",
            "request_failed",
            {"exc_type": type(e).__name__, "exc_str": str(e)[:300]},
        )
        print(f"[debug-hook] FAIL {hypothesis_base} -> {type(e).__name__} {url}", flush=True)
        return -1


def _post_json(url: str, secret: str, body: bytes, hypothesis_base: str) -> int:
    return _post_json_ex(
        url, body, hypothesis_base, {"X-Hook-Secret": secret}
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--also-artifact-pull", action="store_true")
    ap.add_argument(
        "--probe-bearer",
        action="store_true",
        help="Extra POST /build-notify using Authorization: Bearer only (hypothesis H7)",
    )
    args = ap.parse_args()

    hook_url = (os.environ.get("HOOK_URL") or "").strip()
    secret = os.environ.get("HOOK_SECRET") or ""

    # #region agent log
    _agent_log(
        "H1",
        "env_hydration",
        {
            "hook_url_set": bool(hook_url),
            "hook_url_len": len(hook_url),
            "secret_len": len(secret),
            "secret_empty": len(secret) == 0,
        },
    )
    # #endregion

    if not hook_url:
        _agent_log("H1", "abort_missing_url", {})
        return 2

    norm = hook_url.rstrip("/")
    # #region agent log
    _agent_log(
        "H2",
        "url_normalization",
        {"had_trailing_slash": hook_url.endswith("/"), "normalized_len": len(norm)},
    )
    # #endregion

    print(
        f"[debug-hook] log file: {_LOG_PATH} (NDJSON). Secrets are never printed.",
        flush=True,
    )

    payload = b'{"event":"build_start"}'
    target = f"{norm}/build-notify"
    _agent_log("H3", "post_build_notify", {"path_suffix": "/build-notify"})
    failed = False
    if _post_json(target, secret, payload, "H3") != 0:
        failed = True

    if args.also_artifact_pull:
        ap_target = f"{norm}/artifact-pull"
        _agent_log("H3b", "post_artifact_pull", {"path_suffix": "/artifact-pull"})
        if _post_json(ap_target, secret, b'{"event":"release_sync"}', "H3b") != 0:
            failed = True

    if args.probe_bearer:
        _agent_log("H7", "probe_bearer_build_notify", {"path_suffix": "/build-notify"})
        brc = _post_json_ex(
            target,
            payload,
            "H7",
            {"Authorization": f"Bearer {secret}"},
        )
        if brc != 0:
            failed = True
        print(
            "[debug-hook] H7 probe: Authorization Bearer only (see log auth_mode)",
            flush=True,
        )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
