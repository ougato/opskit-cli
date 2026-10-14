"""Gateway authority comes from platform code/base, never a plugin image."""
import hashlib
import io
import json
import tarfile
from types import SimpleNamespace

import pytest

from core import plugin_gateway as gateway, plugin_runtime as runtime, plugin_sandbox as sandbox


def test_platform_only_context_and_reuses_verified_image(monkeypatch):
    source = gateway.gateway_source()
    digest = hashlib.sha256(source).hexdigest()
    image = "sha256:" + "b" * 64
    base = "sha256:" + "a" * 64
    contexts, calls = [], []
    def output(command, **kwargs):
        calls.append(command)
        if command[1] == 'pull':
            assert command[2:] == [gateway.BASE_REFERENCE]
            return 'pulled'
        if command[1] == 'build':
            assert '--network=none' in command
            contexts.append(kwargs['stdin'].read())
            return ''
        assert command[1:5] == ['image', 'inspect', '--format', '{{.Id}}']
        return base if command[-1] == gateway.BASE_REFERENCE else image
    def inspect(command, *args):
        assert args[:2] == ('image', 'inspect') and args[-1] == image
        return json.dumps([image, str(gateway.POLICY), gateway.BASE_REFERENCE.rsplit('@', 1)[1], digest])
    monkeypatch.setattr(runtime, '_command_output', output)
    monkeypatch.setattr(sandbox, '_control', inspect)
    assert gateway.build(['docker']) == image
    assert gateway.build(['docker']) == image
    assert len(contexts) == 1
    with tarfile.open(fileobj=io.BytesIO(contexts[0])) as archive:
        assert set(archive.getnames()) == {'Dockerfile', 'gateway.py'}
        assert archive.extractfile('gateway.py').read() == source
        dockerfile = archive.extractfile('Dockerfile').read().decode()
        assert 'FROM ' + base in dockerfile
        assert 'pip install' not in dockerfile and 'requirements' not in dockerfile
        assert 'rm -rf /usr/local/lib/python3.12/site-packages' in dockerfile
        assert 'USER 65534:65534' in dockerfile and 'ENTRYPOINT []' in dockerfile
        assert 'RUN python -I -S -c ' in dockerfile
        assert 'org.opskit.gateway=2' in dockerfile
    assert len([call for call in calls if call[1] == 'pull']) == 1


@pytest.mark.parametrize('case', ['sdk', 'digest', 'base', 'policy_bool', 'old_policy', 'extra', 'type', 'broken'])
def test_unrelated_or_corrupt_catalog_refused_before_docker(case, monkeypatch):
    source = gateway.gateway_source()
    record = {'schema_version': gateway.POLICY, 'image': 'sha256:' + 'b' * 64,
              'base_reference': gateway.BASE_REFERENCE, 'code_sha256': hashlib.sha256(source).hexdigest()}
    if case == 'sdk':
        record['image'] = 'plugin-runtime-tag'
    elif case == 'digest':
        record['code_sha256'] = 'a' * 64
    elif case == 'base':
        record['base_reference'] = 'untrusted-plugin-base'
    elif case == 'policy_bool':
        record['schema_version'] = True
    elif case == 'old_policy':
        record['schema_version'] = 1
    elif case == 'extra':
        record['plugin'] = 'private-marker'
    else:
        record['image'] = []
    path = gateway._catalog()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('private-marker: [invalid' if case == 'broken' else json.dumps(record))
    monkeypatch.setattr(sandbox, '_control', lambda *args: pytest.fail('unverified gateway queried'))
    with pytest.raises(sandbox.SandboxError, match='prepare a platform gateway') as caught:
        gateway.image(['docker'])
    assert 'private-marker' not in str(caught.value)


def test_gateway_label_mismatch_never_falls_back_to_sdk(monkeypatch):
    source = gateway.gateway_source()
    record = {'schema_version': gateway.POLICY, 'image': 'sha256:' + 'b' * 64,
              'base_reference': gateway.BASE_REFERENCE, 'code_sha256': hashlib.sha256(source).hexdigest()}
    path = gateway._catalog()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    monkeypatch.setattr(sandbox, '_control', lambda *args: json.dumps([record['image'], None, None, None]))
    with pytest.raises(sandbox.SandboxError, match='prepare a platform gateway'):
        gateway.image(['docker'])


def test_network_without_prepared_gateway_creates_no_resources(monkeypatch):
    monkeypatch.setattr(sandbox, '_control', lambda *args: pytest.fail('unsafe network resource created'))
    with pytest.raises(sandbox.SandboxError, match='prepare a platform gateway'):
        with sandbox._network(SimpleNamespace(name='demo', permissions=['network']), ['docker']):
            pytest.fail('unprepared gateway granted network')


def test_no_network_never_loads_gateway(monkeypatch):
    monkeypatch.setattr(gateway, 'image', lambda *args: pytest.fail('unneeded gateway lookup'))
    with sandbox._network(SimpleNamespace(permissions=[]), ['docker']) as result:
        assert result == ('none', [])
