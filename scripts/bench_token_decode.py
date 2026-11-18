"""Reproduce memory costs of compressed token abuse, without touching user state.

Run from the repository: python -m scripts.bench_token_decode
The baseline reproduces aa9b035's decode/decompress/JSON chain for comparison
only. It is never used by the platform or plugin runtime.
"""
import base64
import gzip
import json
import platform
import statistics
import time
import tracemalloc

from wireguard.token import TOKEN_PREFIX, decode_token, encode_token


def measure(operation, token, repeats=7):
    timings, peaks, rejected = [], [], 0
    for _ in range(repeats):
        tracemalloc.start()
        start = time.perf_counter()
        try:
            operation(token)
        except ValueError:
            rejected += 1
        timings.append((time.perf_counter() - start) * 1000)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
    return {"median_ms": round(statistics.median(timings), 3),
            "peak_python_bytes": max(peaks), "rejected": rejected, "runs": repeats}


def baseline(token):
    return json.loads(gzip.decompress(base64.urlsafe_b64decode(token[len(TOKEN_PREFIX):])))


def main():
    key = base64.b64encode(bytes(32)).decode()
    fields = {"server_ip": "192.0.2.1", "server_port": 443, "wg_server_pubkey": key,
              "wg_client_privkey": key, "wg_psk": key, "client_ip": "10.10.10.2",
              "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "sni": "example.com", "label": "hk"}
    decode_token(encode_token(fields))  # Warm imports outside allocation measurement.
    token = encode_token({**fields, "unused": "x" * (4 * 1024 * 1024)})
    print(json.dumps({"python": platform.python_version(), "platform": platform.system(),
                      "encoded_bytes": len(token), "expanded_extra_bytes": 4 * 1024 * 1024,
                      "baseline": measure(baseline, token), "bounded": measure(decode_token, token)}, indent=2))


if __name__ == "__main__":
    main()
