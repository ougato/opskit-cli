"""Run native prepare/trust/isolation/state/rollback through a real frozen CLI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import socket

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROBE = '''from pathlib import Path
from core.sdk import current_lang, plugin_data_dir
import errno,json,os,socket

def _verify(argv):
    mode, secret, tcp_port, udp_port = argv
    state = plugin_data_dir("frozen_native_probe") / "verified.txt"
    if mode == "fail":
        state.write_text("must-not-commit")
        raise RuntimeError("synthetic failure")
    try:
        Path(secret).read_bytes()
    except PermissionError:
        pass
    else:
        raise RuntimeError("host secret accessible")
    try:
        Path(__file__).write_text("must-not-write")
    except PermissionError:
        pass
    else:
        raise RuntimeError("plugin source writable")
    target = Path(secret).with_name("forbidden-write.txt")
    try:
        target.write_bytes(b"must-not-write")
    except PermissionError:
        pass
    else:
        raise RuntimeError("host directory writable")
    if os.environ.get("OPSKIT_ACCEPTANCE_HOST_SECRET"):
        raise RuntimeError("host environment secret inherited")
    network_results = {}
    for kind, port in ((socket.SOCK_STREAM,tcp_port),(socket.SOCK_DGRAM,udp_port)):
        try:
            with socket.socket(socket.AF_INET,kind) as connection:
                connection.settimeout(2)
                if kind == socket.SOCK_STREAM:
                    connection.connect(('127.0.0.1',int(port)))
                else:
                    connection.sendto(b'must-not-send',('127.0.0.1',int(port)))
        except OSError as error:
            # WFP may silently drop instead of returning WSAEACCES. A timed
            # out TCP handshake to the live host listener is also blocked;
            # the parent independently verifies no connection/datagram arrived.
            if isinstance(error,TimeoutError):
                observation = 'timeout'
            elif error.errno in (errno.EACCES,errno.EPERM) or getattr(error,'winerror',None) == 10013:
                observation = 'access_denied'
            else:
                raise RuntimeError('unexpected network result: '+str(kind)+':'+str(error.errno)+':'+str(getattr(error,'winerror',None)))
            network_results['tcp' if kind == socket.SOCK_STREAM else 'udp'] = observation
        else:
            if kind == socket.SOCK_STREAM:
                raise RuntimeError("raw TCP connected outside host broker")
            # A UDP send may be accepted then discarded by WFP. Only the
            # independent host receiver can establish whether it was delivered.
            network_results['udp'] = 'send_accepted'
    (state.parent/'adversarial.json').write_text(json.dumps({
        'host_read_denied':True,'host_write_denied':True,'source_write_denied':True,
        'host_secret_environment_absent':True,'raw_tcp_unreachable':True,'raw_udp_attempted':True}))
    (state.parent/'network-observations.json').write_text(json.dumps(network_results))
    state.write_text(current_lang())
    return 0

def cli(argv):
    # Preserve test-owned diagnostic evidence without turning a failed probe
    # into a passing report. The parent rejects this file before verification.
    if argv[0] == 'fail':
        return _verify(argv)
    try:
        return _verify(argv)
    except Exception as error:
        (plugin_data_dir('frozen_native_probe')/'failure.json').write_text(json.dumps({
            'type':type(error).__name__,'message':str(error)}))
        return 0
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    args = parser.parse_args()
    binary, offline = args.binary.resolve(strict=True), args.offline.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    data, plugins = output / "data", output / "plugins"
    environment = dict(os.environ, OPSKIT_DATA_DIR=str(data), OPSKIT_PLUGINS_DIR=str(plugins),
                       PYTHONIOENCODING="utf-8", OPSKIT_ACCEPTANCE_HOST_SECRET="synthetic-do-not-inherit")
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    os.environ["OPSKIT_DATA_DIR"] = str(data)
    from core.config import set_config_value
    from core.plugin_trust import compute_fingerprint
    set_config_value({}, "language", args.language)
    plugin = plugins / "frozen_native_probe"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "name: frozen_native_probe\nversion: 1.0.0\napi_version: 2\nkind: python\n"
        "entry: frozen_native_probe\n", encoding="utf-8")
    (plugin / "frozen_native_probe.py").write_text(PROBE, encoding="utf-8")
    fingerprint = compute_fingerprint(plugin)
    secret = output / "host-secret.txt"
    secret.write_text("synthetic host-only content", encoding="utf-8")
    outside = output / "outside-source"
    outside.mkdir()
    records = []
    # Real listening endpoints distinguish kernel access denial from a
    # connection refusal caused by no receiver. No external network is used.
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for receiver in (tcp, udp):
        if os.name == "nt":
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        receiver.bind(("127.0.0.1", 0))
        receiver.setblocking(False)
    tcp.listen(1)
    ports = [str(tcp.getsockname()[1]), str(udp.getsockname()[1])]
    # Sanity-check delivery as the host before testing the isolated process.
    with socket.create_connection(("127.0.0.1", int(ports[0])), timeout=2):
        connection, _ = tcp.accept()
        connection.close()
    udp.settimeout(2)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(b"host-sanity", ("127.0.0.1", int(ports[1])))
    if udp.recvfrom(1024)[0] != b"host-sanity":
        raise RuntimeError("host UDP sanity check failed")
    udp.setblocking(False)

    def run(label, arguments, *, success=True):
        result = subprocess.run([str(binary), *arguments], cwd=outside, env=environment,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=600)
        (output / (label + ".stdout")).write_bytes(result.stdout)
        (output / (label + ".stderr")).write_bytes(result.stderr)
        records.append({"step": label, "exit_code": result.returncode})
        if (result.returncode == 0) != success:
            raise RuntimeError("frozen native acceptance failed at " + label)
        return result

    run("prepare", ["--output", "json", "plugin", "prepare", str(plugin),
                    "--python-runtime", str(offline / "python"), "--wheels", str(offline / "wheels"),
                    "--lock-file", str(offline / "requirements.lock")])
    preparation = run("inspect-preparation", ["--output", "json", "plugin", "inspect-native-cache", "prepare"])
    if json.loads(preparation.stdout).get("data") != []:
        raise RuntimeError("frozen prepare left recorded temporary objects")
    run("trust", ["--output", "json", "plugin", "trust", "frozen_native_probe", "--fingerprint", fingerprint])
    run("invoke", ["plugin", "run", "frozen_native_probe", "--", "verify", str(secret), *ports])
    state = data / "plugin-data/frozen_native_probe/self/state/verified.txt"
    failure = state.with_name("failure.json")
    if failure.exists():
        raise RuntimeError("frozen adversarial probe failed: " + failure.read_text())
    if state.read_text() != args.language:
        raise RuntimeError("frozen language/state acceptance failed")
    adversarial = json.loads(state.with_name("adversarial.json").read_text())
    if len(adversarial) != 6 or not all(value is True for value in adversarial.values()):
        raise RuntimeError("frozen adversarial state mismatch")
    try:
        connection, _ = tcp.accept()
    except BlockingIOError:
        pass
    else:
        connection.close()
        raise RuntimeError("host TCP listener received a connection")
    try:
        udp.recvfrom(1024)
    except BlockingIOError:
        pass
    else:
        raise RuntimeError("host UDP listener received a datagram")
    adversarial['raw_udp_not_delivered'] = True
    tcp.close()
    udp.close()
    if secret.with_name("forbidden-write.txt").exists():
        raise RuntimeError("frozen plugin wrote a host file")
    run("rollback", ["plugin", "run", "frozen_native_probe", "--", "fail", str(secret), *ports], success=False)
    if state.read_text() != args.language:
        raise RuntimeError("failed frozen invocation committed state")
    run("revoke", ["--output", "json", "plugin", "revoke", "frozen_native_probe"])
    run("denied", ["plugin", "run", "frozen_native_probe", "--", "verify", str(secret), *ports], success=False)
    if state.read_text() != args.language or (plugin / "frozen_native_probe.py").read_text() != PROBE:
        raise RuntimeError("revoked invocation changed state/source")
    (output / "verification.json").write_text(json.dumps({
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "language": args.language,
        "frozen_native_verified": True, "isolation": True, "state_committed": True,
        "rollback_verified": True, "revocation_verified": True, "steps": records,
        "prepare_cleanup_verified": True,
        "adversarial": adversarial,
        "network_observations": json.loads(state.with_name("network-observations.json").read_text()),
        "actual_creator_verified": False, "actual_s3_verified": False,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
