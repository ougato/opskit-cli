"""Damaged tail diagnosis must preserve evidence and reject foreign state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from core import plugin_state as state
from core.plugin_state_repair import inspect_state_record, repair_state_record


def setup_record(tmp_path, phase="prepared"):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "payload").write_bytes(b"old")
    candidate = root.parent / ".state-owned" / "new"
    candidate.mkdir(parents=True)
    (candidate / "payload").write_bytes(b"new")
    record = state.prepare_record(root, candidate, plugin="provider", caller="platform")
    if phase != "prepared":
        root.rename(root.with_name("self.previous"))
    if phase == "published":
        candidate.rename(root)
    return root, record


def inspect(root):
    return inspect_state_record(root, plugin="provider", caller="platform")


@pytest.mark.parametrize("phase,expected", [("prepared", "uncommitted"),
                                           ("old_moved", "restore_previous"),
                                           ("published", "committed")])
@pytest.mark.parametrize("tail", [b'{', b'{"transaction":', b'{"transaction":"cut'])
def test_torn_tail_is_observed_without_mutation(tmp_path, phase, expected, tail):
    root, _ = setup_record(tmp_path, phase)
    path = state.record_path(root)
    raw = path.read_bytes() + tail
    path.write_bytes(raw)
    before = set(root.parent.iterdir())
    observation = inspect(root)
    assert observation.status == "repairable-tail"
    assert observation.phase == expected
    assert observation.sha256 == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == raw
    assert set(root.parent.iterdir()) == before
    with pytest.raises(state.StateTransactionError):
        state.read_record(root, plugin="provider", caller="platform")


@pytest.mark.parametrize("tail", [b'{}', b'[]', b'null', b'\xff',
                                  b'{"transaction":1,"transaction":2}'])
def test_invalid_structured_tail_is_not_repairable(tmp_path, tail):
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + tail
    path.write_bytes(raw)
    with pytest.raises(state.StateTransactionError):
        inspect(root)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("resolution", ["aborted", "committed"])
def test_complete_outcome_requires_matching_topology(tmp_path, resolution):
    root, record = setup_record(tmp_path)
    path = state.record_path(root)
    path.write_bytes(path.read_bytes() + json.dumps({
        "transaction": record.transaction, "resolution": resolution}).encode())
    if resolution == "committed":
        with pytest.raises(state.StateTransactionError):
            inspect(root)
    else:
        assert inspect(root).status == "repairable-tail"


def test_corrupt_header_cannot_claim_candidate(tmp_path):
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    path.write_bytes(b'{"plugin":"provider"}\n{')
    with pytest.raises(state.StateTransactionError):
        inspect(root)


def test_valid_record_and_missing_record_do_not_create_files(tmp_path):
    root, _ = setup_record(tmp_path)
    assert inspect(root).status == "valid"
    state.record_path(root).unlink()
    before = set(root.parent.iterdir())
    with pytest.raises(FileNotFoundError):
        inspect(root)
    assert set(root.parent.iterdir()) == before


def test_foreign_binding_is_refused_even_with_torn_tail(tmp_path):
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    path.write_bytes(path.read_bytes() + b'{')
    with pytest.raises(state.StateTransactionError):
        inspect_state_record(root, plugin="another", caller="platform")


def test_replaced_candidate_is_not_repairable(tmp_path):
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    path.write_bytes(path.read_bytes() + b'{')
    candidate = root.parent / ".state-owned" / "new"
    candidate.rename(candidate.with_name("retained"))
    candidate.mkdir()
    with pytest.raises(state.StateTransactionError):
        inspect(root)


def test_identical_record_replacement_during_diagnosis_is_refused(tmp_path, monkeypatch):
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    classify = state._inspect_topology

    def replace_record(target, record):
        plan = classify(target, record)
        replacement = path.with_name("replacement")
        replacement.write_bytes(raw)
        replacement.replace(path)
        return plan

    monkeypatch.setattr(state, "_inspect_topology", replace_record)
    with pytest.raises(state.StateTransactionError, match="record changed"):
        inspect(root)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("phase,outcome", [("prepared", "aborted"),
                                          ("old_moved", "aborted"),
                                          ("published", "committed")])
def test_explicit_repair_preserves_raw_evidence_and_recovers(tmp_path, monkeypatch, phase, outcome):
    from core import paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "runtime")
    root, _ = setup_record(tmp_path, phase)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    digest = inspect(root).sha256
    result = repair_state_record(root, plugin="provider", caller="platform", expected_sha256=digest)
    assert result.outcome == outcome
    assert result.sha256 == digest
    assert root.with_name("self.repair-" + result.evidence + ".evidence").read_bytes() == raw
    assert not path.exists()
    assert (root / "payload").read_bytes() == (b"new" if outcome == "committed" else b"old")
    assert not root.with_name("self.previous").exists()


def test_stale_digest_cannot_create_repair_artifacts(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "runtime")
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    with pytest.raises(state.StateTransactionError):
        repair_state_record(root, plugin="provider", caller="platform", expected_sha256="0" * 64)
    assert path.read_bytes() == raw
    assert not list(root.parent.glob("self.repair-*"))


@pytest.mark.parametrize("cancel_at", [2, 3, 4, 5])
def test_cancel_preserves_evidence_or_standard_recovery(tmp_path, monkeypatch, cancel_at):
    from core import paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "runtime")
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == cancel_at:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        repair_state_record(root, plugin="provider", caller="platform",
                            expected_sha256=hashlib.sha256(raw).hexdigest(), checkpoint=cancel)
    assert (root / "payload").read_bytes() == b"old"
    if cancel_at < 5:
        assert path.read_bytes() == raw
    else:
        assert list(root.parent.glob("self.repair-*.evidence"))[0].read_bytes() == raw
        assert state.finish_transaction(root, plugin="provider", caller="platform") == "aborted"


def test_evidence_sync_failure_never_replaces_original(tmp_path, monkeypatch):
    from core import paths, plugin_state_repair as repair
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "runtime")
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)

    def disk_full(descriptor):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(repair.os, "fsync", disk_full)
    with pytest.raises(OSError, match="injected"):
        repair_state_record(root, plugin="provider", caller="platform",
                            expected_sha256=hashlib.sha256(raw).hexdigest())
    assert path.read_bytes() == raw
    assert (root / "payload").read_bytes() == b"old"
    assert list(root.parent.glob("self.repair-*.evidence"))[0].read_bytes() == raw
    assert not list(root.parent.glob("self.repair-*.pending"))


def test_unknown_repair_artifacts_count_toward_quota(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "runtime")
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    for index in range(15):
        root.with_name("self.repair-unknown-" + str(index)).write_bytes(b"keep")
    with pytest.raises(state.StateTransactionError, match="quota"):
        repair_state_record(root, plugin="provider", caller="platform",
                            expected_sha256=hashlib.sha256(raw).hexdigest())
    assert path.read_bytes() == raw
    assert len(list(root.parent.glob("self.repair-*"))) == 15


@pytest.mark.parametrize("exit_at", [3, 4, 5])
def test_real_process_exit_can_retry_or_use_normal_recovery(tmp_path, exit_at):
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    script = '''
import os
from pathlib import Path
import sys
from core.plugin_state_repair import repair_state_record
count = 0
def checkpoint():
    global count
    count += 1
    if count == int(sys.argv[3]):
        os._exit(73)
repair_state_record(Path(sys.argv[1]), plugin="provider", caller="platform",
                    expected_sha256=sys.argv[2], checkpoint=checkpoint)
'''
    result = subprocess.run([sys.executable, "-c", script, str(root),
                             hashlib.sha256(raw).hexdigest(), str(exit_at)],
                            cwd=Path(__file__).resolve().parents[1], timeout=30,
                            capture_output=True)
    assert result.returncode == 73, result.stderr
    assert list(root.parent.glob("self.repair-*.evidence"))[0].read_bytes() == raw
    if exit_at < 5:
        assert path.read_bytes() == raw
        assert repair_state_record(root, plugin="provider", caller="platform",
                                   expected_sha256=hashlib.sha256(raw).hexdigest()).outcome == "aborted"
    else:
        with state.transaction_guard(root):
            assert state.finish_transaction(root, plugin="provider", caller="platform") == "aborted"
    assert (root / "payload").read_bytes() == b"old"
    assert not path.exists()


@pytest.mark.parametrize("kill_at", [3, 4, 5])
def test_external_kill_releases_transaction_lock_and_allows_recovery(tmp_path, kill_at):
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    marker = tmp_path / "repair-ready"
    script = '''
from pathlib import Path
import sys
import time
from core.plugin_state_repair import repair_state_record
count = 0
def checkpoint():
    global count
    count += 1
    if count == int(sys.argv[3]):
        Path(sys.argv[4]).write_bytes(b"ready")
        time.sleep(60)
repair_state_record(Path(sys.argv[1]), plugin="provider", caller="platform",
                    expected_sha256=sys.argv[2], checkpoint=checkpoint)
'''
    process = subprocess.Popen([sys.executable, "-c", script, str(root),
                                hashlib.sha256(raw).hexdigest(), str(kill_at), str(marker)],
                               cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "repair did not reach requested stage"
        process.kill()
        process.communicate(timeout=10)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert list(root.parent.glob("self.repair-*.evidence"))[0].read_bytes() == raw
    if kill_at < 5:
        assert path.read_bytes() == raw
        assert repair_state_record(root, plugin="provider", caller="platform",
                                   expected_sha256=hashlib.sha256(raw).hexdigest()).outcome == "aborted"
    else:
        with state.transaction_guard(root):
            assert state.finish_transaction(root, plugin="provider", caller="platform") == "aborted"
    assert (root / "payload").read_bytes() == b"old"


@pytest.mark.parametrize("fault", ["replace", "hardlink", "same-content-replacement"])
def test_artifact_or_replace_failure_preserves_original(tmp_path, monkeypatch, fault):
    from core import plugin_state_repair as repair
    root, _ = setup_record(tmp_path)
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 4 and fault != "replace":
            evidence = list(root.parent.glob("self.repair-*.evidence"))[0]
            if fault == "hardlink":
                os.link(evidence, root.parent / "foreign-link")
            else:
                replacement = root.parent / "replacement"
                replacement.write_bytes(raw)
                replacement.replace(evidence)

    if fault == "replace":
        def refuse(*args):
            raise OSError("injected atomic replacement failure")
        monkeypatch.setattr(repair.os, "replace", refuse)
    with pytest.raises((OSError, state.StateTransactionError)):
        repair_state_record(root, plugin="provider", caller="platform",
                            expected_sha256=hashlib.sha256(raw).hexdigest(), checkpoint=checkpoint)
    assert path.read_bytes() == raw
    assert (root / "payload").read_bytes() == b"old"
    assert list(root.parent.glob("self.repair-*.evidence"))[0].read_bytes() == raw
