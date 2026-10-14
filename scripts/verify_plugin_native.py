"""Verify a known synthetic plugin using actual Docker and an installed CLI.

Requires an operator-reviewed base digest and complete hashed wheel lock. Only
the explicitly created output directory supplies plugin/config/state data.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
RAW_ARGS = ["MiXeD", "--help", "--repeat", "one", "--repeat", "two"]

PROBE = '''import importlib.util
import json
import os
from pathlib import Path
import subprocess
from core import sdk
import sys

def cli(argv):
    if len(argv) == 1 and argv[0].startswith('exercise:'):
        return exercise(argv[0].split(':', 1)[1])
    checks = {}
    checks['argv'] = argv == ['MiXeD', '--help', '--repeat', 'one', '--repeat', 'two']
    checks['uid'] = os.getuid() == 65534
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    checks['no_capabilities'] = int(status['CapEff'].strip(), 16) == 0
    checks['no_new_privileges'] = status['NoNewPrivs'].strip() == '1'
    checks['platform_modules_absent'] = all(importlib.util.find_spec('core.' + name) is None for name in
        ('updater', 'privilege', 'http', 'plugin', 'plugin_services', 'plugin_runtime', 'plugin_sandbox'))
    checks['shared_sdk_runner'] = sdk.run([sys.executable, '-c', "print('runner-ok')"], capture=True, timeout=5).stdout == 'runner-ok'
    for name, path in [('root_readonly', '/runtime/test-write'), ('plugin_readonly', '/plugin/test-write'), ('seed_readonly', '/seed/test-write')]:
        try:
            Path(path).write_text('unexpected')
        except OSError:
            checks[name] = True
        else:
            checks[name] = False
    checks['docker_socket_absent'] = not Path('/var/run/docker.sock').exists()
    checks['host_canary_absent'] = not Path(HOST_CANARY).exists()
    checks['other_plugin_absent'] = not Path('/data/other-plugin-canary').exists()
    checks['no_network_interface'] = sorted(path.name for path in Path('/sys/class/net').iterdir()) == ['lo']
    for name, paths, limit in [
        ('memory_limit', ['/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'], 256*1024**2),
        ('pids_limit', ['/sys/fs/cgroup/pids.max', '/sys/fs/cgroup/pids/pids.max'], 64),
    ]:
        available = next((Path(path) for path in paths if Path(path).is_file()), None)
        checks[name] = available is not None and available.read_text().strip() == str(limit)
    for name, path, limit in [('tmpfs_tmp_limit', '/tmp', 16*1024**2), ('tmpfs_data_limit', '/data', 32*1024**2)]:
        stat = os.statvfs(path)
        checks[name] = stat.f_blocks * stat.f_frsize == limit
    executable = Path('/tmp/probe-executable')
    executable.write_text('#!/bin/sh\\nexit 0\\n')
    executable.chmod(0o755)
    try:
        subprocess.run([str(executable)], check=True)
    except PermissionError:
        checks['tmp_noexec'] = True
    else:
        checks['tmp_noexec'] = False
    try:
        sdk.plugin_data_dir('other_plugin')
    except PermissionError:
        checks['namespace_denied'] = True
    else:
        checks['namespace_denied'] = False
    for name, call in [('software_install_denied', lambda: sdk.ensure_software('docker')), ('host_binary_denied', lambda: sdk.software_bin('python', 'python'))]:
        try:
            call()
        except PermissionError:
            checks[name] = True
        else:
            checks[name] = False
    state = sdk.plugin_data_dir('native_probe')
    state.mkdir(parents=True, exist_ok=True)
    counter = state / 'counter'
    previous = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(previous + 1))
    (state / 'proof.json').write_text(json.dumps({'checks': checks, 'counter': previous + 1}))
    return 0
'''

EXERCISES = '''
def exercise(mode):
    import errno
    import time
    state = sdk.plugin_data_dir('native_probe')
    (state / 'uncommitted').write_text(mode)
    print('NATIVE_EXERCISE_READY:' + mode, flush=True)
    if mode in ('deadline', 'revoke', 'cancel'):
        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        time.sleep(120)
    elif mode == 'memory':
        data = bytearray(320 * 1024**2)
        return len(data)
    elif mode == 'state':
        with (state / 'oversized').open('wb') as out:
            for _ in range(17):
                out.write(b'x' * 1024**2)
        return 0
    elif mode in ('output', 'output_unicode'):
        chunk = (b'x' * 1024**2 if mode == 'output' else '\\U0001f642'.encode('utf-8') * (1024**2 // 4)) + b'\\n'
        for _ in range(70):
            offset = 0
            while offset < len(chunk):
                offset += os.write(1, chunk[offset:])
        return 0
    elif mode == 'pids':
        children = []
        denied = False
        try:
            for _ in range(80):
                try:
                    children.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']))
                except OSError as error:
                    denied = error.errno == errno.EAGAIN
                    break
        finally:
            for child in children:
                child.terminate()
            for child in children:
                child.wait(timeout=5)
        proof = {'denied': denied, 'children_started': len(children)}
        if not denied or not 40 <= len(children) < 64:
            raise RuntimeError('process quota was not enforced')
        (state / 'exercise.json').write_text(json.dumps(proof))
        return 0
    elif mode == 'tmpfs':
        denied = False
        target = Path('/tmp/fill')
        try:
            with target.open('wb') as out:
                for _ in range(20):
                    out.write(b'x' * 1024**2)
        except OSError as error:
            denied = error.errno == errno.ENOSPC
        finally:
            target.unlink(missing_ok=True)
        if not denied:
            raise RuntimeError('temporary disk quota was not enforced')
        (state / 'exercise.json').write_text(json.dumps({'denied': denied}))
        return 0
    else:
        raise ValueError('unknown synthetic exercise')
'''


def verify_lifecycle(output, manifest, command):
    """Run bounded synthetic failures through the unmodified production supervisor."""
    from core import plugin_sandbox as sandbox, plugin_trust, prompt
    from core.config import save_config
    state = output / 'platform-state/plugin-data/native_probe/self'
    original_start, original_print = sandbox.start, prompt.console.print
    report = []
    active = {}

    def observed_start(arguments, **kwargs):
        if '--log-driver=none' not in arguments:
            raise RuntimeError('worker permits daemon log growth')
        active['container'] = arguments[arguments.index('--name') + 1]
        (output / ('started-' + active['mode'] + '.json')).write_text(json.dumps(active), encoding='utf-8')
        return original_start(arguments, **kwargs)

    def observed_print(value, *args, **kwargs):
        if str(value) == 'NATIVE_EXERCISE_READY:' + active['mode']:
            active['ready'] = True
            if active['mode'] == 'deadline':
                driver = sandbox._control(command, 'inspect', '--format', '{{.HostConfig.LogConfig.Type}}', active['container'])
                if driver != 'none':
                    raise RuntimeError('actual worker log driver is not disabled')
                active['log_driver'] = driver
            if active['mode'] == 'revoke':
                plugin_trust.revoke(manifest.name)
            elif active['mode'] == 'cancel':
                raise KeyboardInterrupt('synthetic cancellation after guest readiness')
        # Do not render the deliberately large output payload; supervisor still
        # reads and counts the actual pipe bytes before reaching this function.

    def snapshot():
        return {path.relative_to(state).as_posix(): path.read_bytes()
                for path in state.rglob('*') if path.is_file()}

    sandbox.start, prompt.console.print = observed_start, observed_print
    try:
        for mode in ('deadline', 'revoke', 'cancel', 'memory', 'state', 'output', 'output_unicode', 'pids', 'tmpfs'):
            fingerprint = plugin_trust.compute_fingerprint(manifest.root)
            plugin_trust.grant(manifest.name, fingerprint, manifest.version)
            # The production budget includes image inspection and snapshot
            # preparation. Leave enough time to observe a running guest before
            # testing expiry; do not give preparation a separate free budget.
            budget = 30
            save_config({'plugin': {'sandbox': {'timeout': budget}}})
            active.clear()
            active.update(mode=mode, ready=False)
            before = snapshot()
            since = int(time.time()) - 1
            started = time.monotonic()
            failure = None
            try:
                value = sandbox.invoke(manifest, 'cli', args=['exercise:' + mode])
            except (sandbox.SandboxError, KeyboardInterrupt) as error:
                failure = type(error).__name__ + ':' + str(error)
            elapsed = time.monotonic() - started
            if not active['ready']:
                raise RuntimeError('guest did not reach exercise: ' + mode)
            expected = {'deadline': 'plugin exceeded execution deadline',
                        'revoke': 'plugin authorization revoked during execution',
                        'cancel': 'synthetic cancellation', 'memory': 'plugin execution failed',
                        'state': 'plugin execution failed', 'output': 'plugin exceeded output quota',
                        'output_unicode': 'plugin exceeded output quota'}
            if mode in expected:
                if failure is None or expected[mode] not in failure or snapshot() != before:
                    raise RuntimeError('failure/state preservation mismatch: ' + mode + ' ' + str(failure))
            elif failure is not None or value != 0:
                raise RuntimeError('quota denial did not recover: ' + mode + ' ' + str(failure))
            container = active['container']
            cleanup_started = time.monotonic()
            while True:
                remaining = sandbox._control(command, 'ps', '-a', '--filter', 'name=' + container, '--format', '{{.Names}}')
                if container not in remaining.splitlines():
                    break
                if time.monotonic() - cleanup_started >= 10:
                    raise RuntimeError('owned container remains after cleanup observation budget: ' + mode)
                time.sleep(0.1)
            cleanup_elapsed = time.monotonic() - cleanup_started
            events = sandbox.capture(command + ['events', '--since', str(since), '--until', str(int(time.time()) + 1),
                                      '--filter', 'container=' + container, '--format', '{{json .}}'],
                                     timeout=10, max_bytes=262144, stdin=subprocess.DEVNULL)
            if events.returncode:
                raise RuntimeError('daemon event evidence unavailable')
            records = [json.loads(line) for line in events.stdout.splitlines() if line.strip()]
            (output / ('events-' + mode + '.json')).write_text(json.dumps(records, indent=2), encoding='utf-8')
            if mode == 'memory' and not any(record.get('Action') == 'oom' for record in records):
                raise RuntimeError('kernel OOM evidence missing')
            if not any(record.get('Action') == 'destroy' for record in records):
                raise RuntimeError('daemon destruction evidence missing: ' + mode)
            item = {'mode': mode, 'guest_ready': True, 'elapsed_seconds': elapsed,
                    'execution_budget_seconds': budget,
                    'failure': failure, 'state_preserved': snapshot() == before,
                    'cleanup_observation_seconds': cleanup_elapsed,
                    'container_removed': container, 'daemon_actions': [record.get('Action') for record in records]}
            if mode in ('pids', 'tmpfs'):
                item['quota_proof'] = json.loads((state / 'state/exercise.json').read_text(encoding='utf-8'))
            if mode == 'deadline':
                item['actual_log_driver'] = active['log_driver']
            report.append(item)
            (output / 'lifecycle-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    finally:
        sandbox.start, prompt.console.print = original_start, original_print
    return report


def verify_rpc(output, base_image, lock_file, command):
    """Real service routing, caller state separation and nested revocation."""
    from core import plugin_sandbox as sandbox, plugin_runtime, plugin_trust, prompt
    from core.config import save_config
    from core.plugin import load_manifest
    provider = "native_rpc_provider"
    callers = ("native_rpc_a", "native_rpc_b")
    provider_code = '''import json
from pathlib import Path
import time

class Service:
    service_api_version = 7

    def count(self):
        path = Path('/data/state/count.txt')
        value = int(path.read_text()) + 1 if path.exists() else 1
        path.write_text(str(value))
        caller = json.loads(Path('/request.json').read_text())['caller']
        return {'counter': value, 'caller': caller}

    def wait(self):
        Path('/data/state/uncommitted').write_text('must not publish')
        print('NATIVE_RPC_PROVIDER_READY', flush=True)
        time.sleep(60)
        return {}

    def delete(self):
        Path('/data/state/forbidden-method').write_text('must never execute')
        return {}

def provide_service(name):
    print('NATIVE_RPC_SERVICE_VERSION:' + str(Service.service_api_version), flush=True)
    return Service() if name == 'native_counter' else None
'''
    caller_code = '''import json
from pathlib import Path
from core import sdk

def cli(argv):
    if argv in (['wire_legacy'], ['wire_other'], ['wire_depth'], ['wire_values'],
                ['wire_missing_state'], ['wire_mixed_result'], ['wire_after_result']):
        Path('/data/state/transport-uncommitted').write_text('must not publish')
        if argv == ['wire_legacy']:
            message = {'type': 'result', 'value': 7, 'state': ''}
        elif argv == ['wire_other']:
            message = {'rpc_version': 2, 'type': 'call', 'method': 'pause', 'args': [], 'kwargs': {}}
        elif argv == ['wire_missing_state']:
            message = {'rpc_version': 1, 'type': 'result', 'value': 0}
        elif argv == ['wire_mixed_result']:
            message = {'rpc_version': 1, 'type': 'result', 'value': 0, 'state': '', 'error': 'mixed'}
        elif argv == ['wire_after_result']:
            print('OPSKIT_RPC:' + json.dumps({'rpc_version': 1, 'type': 'result', 'value': 0, 'state': ''}), flush=True)
            message = {'rpc_version': 1, 'type': 'call', 'method': 'pause', 'args': [], 'kwargs': {}}
        else:
            value = [0] * 131072 if argv == ['wire_values'] else 0
            if argv == ['wire_depth']:
                for _ in range(65):
                    value = [value]
            message = {'rpc_version': 1, 'type': 'result', 'value': value, 'state': ''}
        print('OPSKIT_RPC:' + json.dumps(message), flush=True)
        return 0
    service = sdk.get_service('native_counter')
    if service.service_api_version != 7:
        raise RuntimeError('business version mismatch')
    if argv == ['revoke']:
        Path('/data/state/uncommitted').write_text('must not publish')
        service.wait()
    elif argv == ['edge_revoke']:
        Path('/data/state/uncommitted').write_text('must not publish')
        try:
            service.wait()
        except PermissionError:
            pass  # Catching the broker rejection must not permit state commit.
    elif argv == ['forbidden']:
        service.delete()
    else:
        result = service.count()
        Path('/data/state/result.json').write_text(json.dumps(result))
    return 0
'''
    manifests, images = {}, {}
    save_config({'plugin': {'sandbox': {'timeout': 30}}})
    for name in (provider, *callers):
        root = output / 'plugins' / name
        (root / 'rpc_pkg').mkdir(parents=True)
        declarations = ('provides: [native_counter]\nservice_contracts:\n  native_counter:\n'
                        '    api_version: 7\n    methods: [count, wait]\n' if name == provider else
                        'uses:\n  - service: native_counter\n    provider: native_rpc_provider\n'
                        '    api_version: 7\n    methods: [count, wait]\n')
        (root / 'plugin.yaml').write_text(
            'name: ' + name + '\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: rpc_pkg\npermissions: []\n' + declarations,
            encoding='utf-8')
        (root / 'rpc_pkg/__init__.py').write_text(provider_code if name == provider else caller_code, encoding='utf-8')
        manifest = load_manifest(root)
        if manifest is None:
            raise RuntimeError('synthetic RPC manifest rejected')
        plugin_trust.grant(name, plugin_trust.compute_fingerprint(root), manifest.version)
        manifests[name] = manifest
        images[name] = plugin_runtime.build(root, [], base_image, lock_file=lock_file)
    from core.plugin_services import authorize_service
    data = output / 'platform-state/plugin-data'

    def snapshot(root):
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob('*') if path.is_file()} if root.exists() else {}

    original_start, original_print = sandbox.start, prompt.console.print
    containers, counts = [], []
    active = {'revoke': False, 'ready': False}

    def started(arguments, **kwargs):
        containers.append(arguments[arguments.index('--name') + 1])
        return original_start(arguments, **kwargs)

    def printed(value, *args, **kwargs):
        if str(value).startswith('NATIVE_RPC_SERVICE_VERSION:'):
            active['observed_business_version'] = int(str(value).split(':', 1)[1])
        elif str(value) == 'NATIVE_RPC_PROVIDER_READY' and active['revoke']:
            active['ready'] = True
            if active['revoke'] == 'edge':
                plugin_trust.revoke_service(callers[0], 'native_counter')
            else:
                plugin_trust.revoke(callers[0])
        else:
            original_print(value, *args, **kwargs)

    sandbox.start, prompt.console.print = started, printed
    try:
        before_approval = snapshot(data)
        try:
            sandbox.invoke(manifests[callers[0]], 'cli', args=['count'])
        except sandbox.SandboxError:
            pass
        else:
            raise RuntimeError('independent plugin trust unexpectedly granted RPC')
        if snapshot(data) != before_approval or len(containers) != 1:
            raise RuntimeError('unapproved RPC launched provider or published state')
        for name in callers:
            authorize_service(name, 'native_counter', plugin_trust.compute_fingerprint(manifests[name].root),
                              plugin_trust.compute_fingerprint(manifests[provider].root))
        for name, expected in ((callers[0], 1), (callers[1], 1), (callers[0], 2)):
            if sandbox.invoke(manifests[name], 'cli', args=['count']) != 0:
                raise RuntimeError('synthetic caller failed')
            result = json.loads((data / name / 'self/state/result.json').read_text(encoding='utf-8'))
            counter = (data / provider / 'callers' / name / 'state/count.txt').read_text(encoding='utf-8')
            if result != {'counter': expected, 'caller': name} or counter != str(expected):
                raise RuntimeError('RPC caller identity/state partition mismatch')
            counts.append(result)
        if (data / provider / 'self').exists():
            raise RuntimeError('provider call wrote self state')
        before = snapshot(data)
        forbidden_start = len(containers)
        try:
            sandbox.invoke(manifests[callers[0]], 'cli', args=['forbidden'])
        except sandbox.SandboxError:
            pass
        else:
            raise RuntimeError('undeclared public method executed')
        if snapshot(data) != before or len(containers) - forbidden_start != 2:
            raise RuntimeError('undeclared method launched a call worker or changed state')
        active['revoke'] = True
        failure = None
        try:
            sandbox.invoke(manifests[callers[0]], 'cli', args=['revoke'])
        except sandbox.SandboxError as error:
            failure = str(error)
        if not active['ready'] or failure != 'plugin authorization revoked during execution' or snapshot(data) != before:
            raise RuntimeError('nested caller revocation or transactional state preservation failed')
        identity_ready = active['ready']
        plugin_trust.grant(callers[0], plugin_trust.compute_fingerprint(manifests[callers[0]].root), '1.0.0')
        authorize_service(callers[0], 'native_counter', plugin_trust.compute_fingerprint(manifests[callers[0]].root),
                          plugin_trust.compute_fingerprint(manifests[provider].root))
        active.update(revoke='edge', ready=False)
        edge_failure = None
        try:
            sandbox.invoke(manifests[callers[0]], 'cli', args=['edge_revoke'])
        except sandbox.SandboxError as error:
            edge_failure = str(error)
        if not active['ready'] or edge_failure != 'service authorization revoked during execution' or snapshot(data) != before:
            raise RuntimeError('caught service revocation allowed state publication')
        edge_ready = active['ready']
        active.update(revoke=False, ready=False, observed_business_version=None)
        provider_root = manifests[provider].root
        (provider_root / 'rpc_pkg/__init__.py').write_text(provider_code.replace('service_api_version = 7', 'service_api_version = 8'), encoding='utf-8')
        plugin_trust.grant(provider, plugin_trust.compute_fingerprint(provider_root), '1.0.0')
        images[provider] = plugin_runtime.build(provider_root, [], base_image, lock_file=lock_file)
        for name in callers:
            authorize_service(name, 'native_counter', plugin_trust.compute_fingerprint(manifests[name].root),
                              plugin_trust.compute_fingerprint(provider_root))
        try:
            sandbox.invoke(manifests[callers[0]], 'cli', args=['count'])
        except sandbox.SandboxError:
            pass
        else:
            raise RuntimeError('actual business version mismatch was accepted')
        if snapshot(data) != before or active['observed_business_version'] != 8:
            raise RuntimeError('business version mismatch published persistent state')
        for mode in ('wire_legacy', 'wire_other', 'wire_depth', 'wire_values',
                     'wire_missing_state', 'wire_mixed_result', 'wire_after_result'):
            before_transport = snapshot(data)
            try:
                sandbox.invoke(manifests[callers[0]], 'cli', args=[mode])
            except sandbox.SandboxError as error:
                expected_error = ('plugin call after terminal result' if mode == 'wire_after_result'
                                  else 'invalid plugin protocol')
                if str(error) != expected_error:
                    raise RuntimeError('invalid wire failed at the wrong boundary') from None
            else:
                raise RuntimeError('invalid wire accepted')
            if snapshot(data) != before_transport:
                raise RuntimeError('invalid wire published persistent state')
    finally:
        sandbox.start, prompt.console.print = original_start, original_print
    for container in containers:
        remaining = sandbox._control(command, 'ps', '-a', '--filter', 'name=' + container, '--format', '{{.Names}}')
        if container in remaining.splitlines():
            raise RuntimeError('owned RPC container remains')
    report = {'images': images, 'caller_counts': counts, 'provider_ready_before_revoke': identity_ready,
              'provider_ready_before_edge_revoke': edge_ready,
              'unapproved_rpc_rejected_before_provider_start': True,
              'undeclared_public_method_rejected': True,
              'actual_business_version_mismatch_rejected': True,
              'actual_business_version_observed': active['observed_business_version'],
              'legacy_and_other_transport_versions_rejected': True,
              'allocation_limits_rejected_before_state_publish': True,
              'invalid_result_envelopes_rejected_before_state_publish': True,
              'calls_after_terminal_result_rejected': True,
              'caught_edge_revocation_failure': edge_failure,
              'revocation_failure': failure, 'state_preserved_after_revoke': True,
              'removed_workers': containers,
              'scope': 'known service and two callers; state partition and nested caller revoke; method/API/provider policy and daemon recovery remain'}
    (output / 'rpc-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def verify(base_image, lock_file, cli_entry, output, *, lifecycle=False, rpc=False):
    output = Path(output).resolve()
    lock_file = Path(lock_file).absolute()
    cli_entry = Path(cli_entry).resolve()
    if not cli_entry.is_file():
        raise ValueError("an installed CLI executable is required")
    sys.path.insert(0, str(ROOT))
    from core.plugin_lock import read_lock, valid_base_reference
    if not valid_base_reference(base_image):
        raise ValueError("an operator-reviewed base image digest is required")
    read_lock(lock_file, [])
    output.mkdir(parents=True, exist_ok=False)
    os.environ["OPSKIT_DATA_DIR"] = str(output / "platform-state")
    os.environ["OPSKIT_PLUGINS_DIR"] = str(output / "plugins")
    from core import plugin_runtime, plugin_sandbox, plugin_trust
    from core.config import save_config
    from core.plugin import load_manifest

    command = plugin_sandbox.docker_command()
    plugin = output / "plugins/native_probe"
    (plugin / "probe_pkg").mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "name: native_probe\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: probe_pkg\npermissions: []\n",
        encoding="utf-8",
    )
    canary = output / "host-canary"
    canary.write_text("OWNED-HOST-CANARY", encoding="utf-8")
    host_path = plugin_sandbox.mount_path(canary, command)
    (plugin / "probe_pkg/__init__.py").write_text("HOST_CANARY = " + repr(host_path) + "\n" + PROBE + EXERCISES, encoding="utf-8")
    save_config({"plugin": {"sandbox": {"timeout": 30}}})
    manifest = load_manifest(plugin)
    if manifest is None:
        raise RuntimeError("synthetic manifest rejected")
    fingerprint = plugin_trust.compute_fingerprint(plugin)
    plugin_trust.grant(manifest.name, fingerprint, manifest.version)
    original_capture, original_start = plugin_runtime.capture, plugin_sandbox.start
    containers, results = [], []

    def observed_capture(command, **kwargs):
        if "build" in command:
            source = kwargs["stdin"]
            position = source.tell()
            content = source.read(32 * 1024**2 + 1)
            source.seek(position)
            if len(content) > 32 * 1024**2:
                raise ValueError("verification context exceeds limits")
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
                names = archive.getnames()
            (output / "runtime-context-inventory.json").write_text(json.dumps(names, indent=2), encoding="utf-8")
        return original_capture(command, **kwargs)

    def observed_start(command, **kwargs):
        containers.append(command[command.index("--name") + 1])
        return original_start(command, **kwargs)

    proof = output / "platform-state/plugin-data/native_probe/self/state/proof.json"

    def inspect_result(counter, route):
        result = json.loads(proof.read_text(encoding="utf-8"))
        if result["counter"] != counter or not all(result["checks"].values()):
            raise RuntimeError("native isolation proof failed: " + json.dumps(result))
        if canary.read_text(encoding="utf-8") != "OWNED-HOST-CANARY":
            raise RuntimeError("host canary changed")
        results.append({"route": route, **result})
        (output / "invocation-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    plugin_runtime.capture, plugin_sandbox.start = observed_capture, observed_start
    try:
        image = plugin_runtime.build(plugin, [], base_image, lock_file=lock_file)
        for counter in (1, 2):
            if plugin_sandbox.invoke(manifest, "cli", args=RAW_ARGS) != 0:
                raise RuntimeError("synthetic plugin returned failure")
            inspect_result(counter, "production supervisor")
            remaining = plugin_sandbox._control(command, "ps", "-a", "--filter", "name=" + containers[-1], "--format", "{{.Names}}")
            if containers[-1] in remaining.splitlines():
                raise RuntimeError("owned worker remains in daemon")
        completed = original_capture([str(cli_entry), "--non-interactive", "plugin", "run", "native_probe", "--", *RAW_ARGS],
                                     cwd=output, timeout=90, max_bytes=65536, stdin=subprocess.DEVNULL)
        (output / "cli.stdout").write_text(completed.stdout, encoding="utf-8")
        (output / "cli.stderr").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError("installed plugin CLI failed; see owned output logs")
        inspect_result(3, "installed CLI")
    finally:
        plugin_runtime.capture, plugin_sandbox.start = original_capture, original_start
    report = {"image": image, "base_image": base_image, "cli_entry": str(cli_entry),
              "checks_per_invocation": len(results[0]["checks"]), "invocations": len(results),
              "supervisor_workers_removed": containers, "installed_cli_exit_code": completed.returncode,
              "output": str(output), "scope": "known synthetic plugin; quotas observed, not exhaustion; network/RPC/revocation/daemon failure recovery remain"}
    if lifecycle:
        report['lifecycle'] = verify_lifecycle(output, manifest, command)
        report['scope'] = 'known synthetic plugin; quota exhaustion, deadline, revoke and injected cancellation; authorized network/RPC and daemon failure recovery remain'
    if rpc:
        report['rpc'] = verify_rpc(output, base_image, lock_file, command)
        report['scope'] = ('known synthetic plugins; base isolation, real caller state partitions and nested caller revocation verified; '
                           + ('resource lifecycle verified; ' if lifecycle else '')
                           + 'real OS signals, authorized network, complete method/API/provider policy and daemon recovery remain')
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--lock-file", required=True, type=Path)
    parser.add_argument("--cli-entry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--lifecycle", action="store_true", help="Exercise bounded synthetic quota exhaustion and lifecycle failures")
    parser.add_argument("--rpc", action="store_true", help="Verify known caller/provider RPC isolation and nested revocation")
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.base_image, arguments.lock_file, arguments.cli_entry, arguments.output, lifecycle=arguments.lifecycle, rpc=arguments.rpc)))
