"""Actual platform network, only a task-owned HTTP peer; no external targets."""
import argparse
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

def _verify_gateway_image(image, control):
    """Inspect the approved image with its production user/guard flags."""
    from core.process import capture
    name = 'opskit-owned-gateway-inventory-' + uuid.uuid4().hex
    probe = '''import importlib.metadata, json, os, runpy, ssl, sqlite3, stat
setid = []
def failed(error):
    raise error
for base in ('/usr/bin', '/usr/sbin', '/usr/local/bin'):
    for parent, dirs, files in os.walk(base, followlinks=False, onerror=failed):
        for name in files:
            path = os.path.join(parent, name)
            mode = os.lstat(path).st_mode
            if stat.S_ISREG(mode) and mode & 0o6000:
                setid.append(path)
scope = runpy.run_path('/runtime/gateway.py', run_name='gateway_probe')
try:
    scope['allowed_addresses']('127.0.0.1', 443, [])
    denied = False
except PermissionError:
    denied = True
ssl.create_default_context()
with sqlite3.connect(':memory:') as db:
    sqlite_ok = db.execute('select 1').fetchone() == (1,)
status = dict(line.split(':', 1) for line in open('/proc/self/status') if ':' in line)
print(json.dumps({'setid_files_in_executable_roots': setid,
    'infocmp_absent': not os.path.lexists('/usr/bin/infocmp'),
    'private_target_denied': denied, 'ssl_sqlite_smoke': sqlite_ok,
    'python_distributions': sorted(d.metadata['Name'] for d in importlib.metadata.distributions(
        path=['/usr/local/lib/python3.12/site-packages'])),
    'uid': os.geteuid(), 'cap_eff': int(status['CapEff'], 16),
    'no_new_privileges': int(status['NoNewPrivs']), 'seccomp': int(status['Seccomp'])}))
'''
    try:
        inventory = json.loads(control('run', '--rm', '--name', name, '--network=none', '--read-only',
            '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--memory=128m', '--pids-limit=32', '--entrypoint=python', image, '-I', '-S', '-c', probe))
        expected = {'setid_files_in_executable_roots': [], 'infocmp_absent': True,
            'private_target_denied': True, 'ssl_sqlite_smoke': True, 'python_distributions': [],
            'uid': 65534, 'cap_eff': 0, 'no_new_privileges': 1, 'seccomp': 2}
        if inventory != expected:
            raise RuntimeError('actual gateway hardening inventory failed')
        return inventory
    finally:
        capture(['docker', 'rm', '-f', name], timeout=30, max_bytes=4096)
        if control('container', 'ls', '-a', '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}'):
            raise RuntimeError('owned gateway inventory container cleanup unconfirmed')


def verify(output, *, base_image=None, lock_file=None):
    """Use a new output/data root and restore the caller's environment."""
    previous = os.environ.get('OPSKIT_DATA_DIR')
    try:
        return _verify(Path(output).absolute(), base_image=base_image, lock_file=lock_file)
    finally:
        if previous is None:
            os.environ.pop('OPSKIT_DATA_DIR', None)
        else:
            os.environ['OPSKIT_DATA_DIR'] = previous


def _verify_plugin(output, address, base_image, lock_file):
    """Prepare and invoke only our generated network plugin, with real grants."""
    from core import config, plugin_runtime, plugin_sandbox as sandbox, plugin_trust, prompt
    from core.plugin import load_manifest
    plugin = output / 'plugins' / 'gateway_probe'
    package = plugin / 'probe_pkg'
    package.mkdir(parents=True)
    (plugin / 'plugin.yaml').write_text(
        'name: gateway_probe\nversion: 1.0.0\napi_version: 2\nkind: python\n'
        'entry: probe_pkg\npermissions: [network]\n', encoding='utf-8')
    (package / '__init__.py').write_text('''import base64, json, os, socket, time, urllib.request
from pathlib import Path
from urllib.parse import urlsplit

def cli(argv):
    proxy = os.environ['HTTP_PROXY']
    endpoint = urlsplit(proxy)
    ready = False
    for _ in range(40):
        try:
            with socket.create_connection((endpoint.hostname, endpoint.port), timeout=0.2):
                ready = True
                break
        except OSError:
            time.sleep(0.05)
    direct = False
    try:
        with socket.create_connection((argv[0], 8765), timeout=1):
            direct = True
    except OSError:
        pass
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy}))
    received = False
    try:
        with opener.open('http://' + argv[0] + ':8765/probe', timeout=5) as reply:
            received = reply.read(64) == b'OPSKIT_OWNED_HTTP_PEER'
    except OSError:
        pass
    Path('/data/state/proof.json').write_text(json.dumps({
        'proxy_reachable': ready, 'direct_peer_reachable': direct,
        'owned_http_marker_received': received}))
    if len(argv) == 2:
        Path('/data/state/uncommitted').write_text('must not publish')
        with socket.create_connection((endpoint.hostname, endpoint.port), timeout=5) as tunnel:
            auth = base64.b64encode((endpoint.username + ':' + endpoint.password).encode()).decode()
            tunnel.sendall(('CONNECT ' + argv[0] + ':8765 HTTP/1.1\\r\\n'
                'Proxy-Authorization: Basic ' + auth + '\\r\\n\\r\\n').encode())
            if not tunnel.recv(4096).startswith(b'HTTP/1.1 200 '):
                raise RuntimeError('owned CONNECT tunnel not established')
            print('GATEWAY_PROBE_READY:' + argv[1], flush=True)
            if argv[1] == 'cleanup_revoke':
                return 0
            time.sleep(60)
    return 0
''', encoding='utf-8')
    manifest = load_manifest(plugin)
    if manifest is None:
        raise RuntimeError('owned network plugin manifest rejected')
    plugin_trust.grant(manifest.name, plugin_trust.compute_fingerprint(plugin), manifest.version)
    image = plugin_runtime.build(plugin, [], base_image, lock_file=lock_file)
    proof = output / 'platform-state/plugin-data/gateway_probe/self/state/proof.json'
    reports = []
    for granted in (False, True, False):
        targets = [str(address) + ':8765'] if granted else []
        config.save_config({'plugin': {'sandbox': {'timeout': 30,
            'private_targets': {manifest.name: targets}}}})
        if sandbox.invoke(manifest, 'cli', args=[str(address)]) != 0:
            raise RuntimeError('owned network plugin invocation failed')
        report = json.loads(proof.read_text(encoding='utf-8'))
        report['private_granted'] = granted
        if (not report['proxy_reachable'] or report['direct_peer_reachable']
                or report['owned_http_marker_received'] is not granted):
            raise RuntimeError('full network plugin boundary failed')
        reports.append(report)
    original_print, original_network = prompt.console.print, sandbox._network
    failures = []
    active = {}

    def observed_print(value, *args, **kwargs):
        if str(value) == 'GATEWAY_PROBE_READY:' + active['mode']:
            active['ready'] = True
            if active['mode'] == 'cancel':
                raise KeyboardInterrupt('owned gateway cancellation')
            if active['mode'] == 'private_revoke':
                config.save_config({'plugin': {'sandbox': {'timeout': 30,
                    'private_targets': {manifest.name: []}}}})
            elif active['mode'] == 'revoke':
                plugin_trust.revoke(manifest.name)
        else:
            original_print(value, *args, **kwargs)

    @contextmanager
    def observed_network(manifest, command):
        with original_network(manifest, command) as value:
            yield value
        if active['mode'] == 'cleanup_revoke':
            config.save_config({'plugin': {'sandbox': {'timeout': 30,
                'private_targets': {manifest.name: []}}}})
            active['cleanup_finished'] = True

    prompt.console.print, sandbox._network = observed_print, observed_network
    try:
        for mode in ('cancel', 'revoke', 'private_revoke', 'cleanup_revoke'):
            active.update(mode=mode, ready=False, cleanup_finished=False)
            plugin_trust.grant(manifest.name, plugin_trust.compute_fingerprint(plugin), manifest.version)
            config.save_config({'plugin': {'sandbox': {'timeout': 30,
                'private_targets': {manifest.name: [str(address) + ':8765']}}}})
            before = proof.read_bytes()
            failure = None
            try:
                sandbox.invoke(manifest, 'cli', args=[str(address), mode])
            except (sandbox.SandboxError, KeyboardInterrupt) as error:
                failure = str(error)
            expected = {'cancel': 'owned gateway cancellation', 'revoke': 'authorization revoked',
                        'private_revoke': 'private network authorization revoked',
                        'cleanup_revoke': 'private network authorization revoked'}[mode]
            if (not active['ready'] or failure is None or expected not in failure
                    or proof.read_bytes() != before or (proof.parent / 'uncommitted').exists()
                    or mode == 'cleanup_revoke' and not active['cleanup_finished']):
                raise RuntimeError('network lifecycle failure/state preservation mismatch: ' + mode)
            failures.append({'mode': mode, 'ready_with_connect_tunnel': True,
                             'failure': failure, 'persistent_state_preserved': True})
    finally:
        prompt.console.print, sandbox._network = original_print, original_network
    return {'sdk_image': image, 'reports': reports, 'lifecycle': failures,
            'scope': 'actual prepare/invoke/state publication; private grant changes between invocations; '
                     'injected cancellation, identity and private grant revocation with live CONNECT; '
                     'polling detection, not instantaneous per-request authorization'}


def _verify(output, *, base_image=None, lock_file=None):
    from types import SimpleNamespace
    if (base_image is None) != (lock_file is None):
        raise ValueError('base image and lock file must be supplied together')
    if base_image is not None:
        from core.plugin_lock import read_lock, valid_base_reference
        if not valid_base_reference(base_image):
            raise ValueError('a reviewed base image digest is required')
        read_lock(lock_file, [])
    output.mkdir(parents=True, exist_ok=False)
    os.environ['OPSKIT_DATA_DIR'] = str(output / 'platform-state')
    from core import config, plugin_gateway as gateway, plugin_resources as resources, plugin_sandbox as sandbox
    from core.process import capture
    image = gateway.build(['docker'])
    peer_name = 'opskit-owned-http-peer-' + uuid.uuid4().hex
    peer = None
    peer_program = '''import http.server
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'OPSKIT_OWNED_HTTP_PEER'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(('0.0.0.0', 8765), Handler).serve_forever()
'''
    client_program = '''import json, socket, sys, time, urllib.request
from urllib.parse import urlsplit
proxy = __import__('os').environ['HTTP_PROXY']
parsed = urlsplit(proxy)
ready = False
for _ in range(40):
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.2):
            ready = True
            break
    except OSError:
        time.sleep(0.05)
direct = False
try:
    with socket.create_connection((sys.argv[1], 8765), timeout=1):
        direct = True
except OSError:
    pass
opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy}))
received = False
try:
    with opener.open('http://' + sys.argv[1] + ':8765/probe', timeout=5) as reply:
        received = reply.read(64) == b'OPSKIT_OWNED_HTTP_PEER'
except Exception:
    pass
print(json.dumps({'proxy_reachable': ready, 'direct_peer_reachable': direct,
                  'owned_http_marker_received': received}))
'''
    reports = []
    plugin_report = None
    def control(*args):
        # Docker can emit host-capability warnings on stderr even on success.
        # Keep them separate from exact JSON/ID output used by assertions.
        result = subprocess.run(['docker', *args], timeout=30, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True)
        if len(result.stdout.encode()) + len(result.stderr.encode()) > 8192:
            raise RuntimeError('owned network verification output exceeds limit')
        if result.returncode:
            raise RuntimeError('owned network verification control failed')
        return result.stdout.strip()
    try:
        inventory = _verify_gateway_image(image, control)
        peer = control('run', '-d', '--name', peer_name, '--read-only', '--user', '65534:65534',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=32', '--memory=64m',
            '--entrypoint=python', image, '-I', '-S', '-u', '-c', peer_program)
        networks = json.loads(control('inspect', '--format', '{{json .NetworkSettings.Networks}}', peer))
        address = ipaddress.ip_address(networks['bridge']['IPAddress'])
        if address.version != 4 or not address.is_private:
            raise RuntimeError('owned HTTP peer did not receive private IPv4')
        for granted in (False, True):
            targets = [str(address) + ':8765'] if granted else []
            config.save_config({'plugin': {'sandbox': {'timeout': 30, 'private_targets': {'demo': targets}}}})
            with sandbox._execution_budget(), sandbox._network(SimpleNamespace(name='demo', permissions=['network']), ['docker']) as (network, env):
                ipam = json.loads(control('network', 'inspect', '--format', '{{json .IPAM.Config}}', network))[0]
                network_id = control('network', 'inspect', '--format', '{{.Id}}', network)
                try:
                    socket.if_nametoindex('br-' + network_id[:12])
                except OSError:
                    raise RuntimeError('verifier requires the local Docker bridge network namespace') from None
                if json.loads(control('network', 'inspect', '--format', '{{json .EnableIPv6}}', network)) is not False:
                    raise RuntimeError('platform network unexpectedly enables IPv6')
                listener = socket.socket()
                try:
                    try:
                        listener.bind((ipam['Gateway'], 0))
                        host_listener_possible = True
                    except OSError:
                        host_listener_possible = False
                finally:
                    listener.close()
                client = 'opskit-owned-proxy-client-' + uuid.uuid4().hex
                try:
                    result = control('run', '--rm', '--name', client, '--network', network, '--read-only',
                        '--user', '65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                        '--pids-limit=32', '--memory=64m', *env, '--entrypoint=python', image,
                        '-I', '-S', '-u', '-c', client_program, str(address))
                    report = json.loads(result)
                    report.update({'private_granted': granted, 'host_gateway_listener_possible': host_listener_possible})
                    reports.append(report)
                    if (host_listener_possible or report['direct_peer_reachable'] or not report['proxy_reachable']
                            or report['owned_http_marker_received'] is not granted):
                        raise RuntimeError('production gateway network boundary failed')
                finally:
                    capture(['docker', 'rm', '-f', client], timeout=30, max_bytes=4096)
                    if control('container', 'ls', '-a', '--filter', 'name=^/' + client + '$', '--format', '{{.ID}}'):
                        raise RuntimeError('owned proxy client cleanup unconfirmed')
        if base_image is not None:
            plugin_report = _verify_plugin(output, address, base_image, lock_file)
        (output / 'verification.json').write_text(json.dumps({'gateway_image': image, 'reports': reports,
            'gateway_inventory': inventory, 'plugin': plugin_report,
            'scope': 'actual platform scopes, controlled private HTTP peer, IPv4 on current Docker daemon; no foreign services or destinations'}, indent=2))
    finally:
        # A failed `docker run` can still have created the named container.
        # Its returned ID is unavailable on that path; retire the exact unique
        # name allocated above, including when startup failed before assignment.
        capture(['docker', 'rm', '-f', peer_name], timeout=30, max_bytes=4096)
        if control('container', 'ls', '-a', '--filter', 'name=^/' + peer_name + '$', '--format', '{{.ID}}'):
            raise RuntimeError('owned HTTP peer cleanup unconfirmed')
        recovery = resources.reconcile(['docker'], lambda *args: sandbox._cleanup_control(['docker'], *args))
        (output / 'recovery.json').write_text(json.dumps(recovery))
    if any(recovery.values()):
        raise RuntimeError('gateway verifier left resources requiring recovery')
    return {'passed': True, 'reports': reports, 'plugin': plugin_report,
            'gateway_inventory': inventory, 'recovery': recovery, 'output': str(output)}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New isolated verification directory')
    parser.add_argument('--base-image', help='Reviewed digest for a full synthetic plugin prepare/invoke')
    parser.add_argument('--lock-file', type=Path, help='Reviewed complete wheel hash lock for that SDK')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('run inside the Linux host namespace of the Docker daemon')
    if (args.base_image is None) != (args.lock_file is None):
        parser.error('--base-image and --lock-file must be provided together')
    print(json.dumps(verify(args.output, base_image=args.base_image, lock_file=args.lock_file)))
