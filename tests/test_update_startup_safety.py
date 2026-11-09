"""Startup cleanup must preserve files without transaction ownership."""
import hashlib
import os
import sys
import types

import pytest
from typer.testing import CliRunner

import main
import core
from core import updater


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "_reconcile_indexed_job_on_boot", lambda: (False, False))
    monkeypatch.setattr(updater, "_check_health", lambda: False)
    return tmp_path


@pytest.mark.parametrize("frozen", [False, True])
def test_unknown_fixed_files_and_cache_survive_startup(isolated, monkeypatch, frozen):
    monkeypatch.setattr(updater, "_is_frozen", lambda: frozen)
    target = isolated / "program.exe"
    target.write_bytes(b"program")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    old = isolated / "program.old.exe"
    old.write_bytes(b"unrelated old file")
    pending = updater._get_pending_path()
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"unrelated file")
    cache = updater._get_cache_path()
    updater._write_update_record(cache, {"pending_version": updater.APP_VERSION + 1})
    original_cache = cache.read_bytes()
    assert updater.check_and_apply_pending() is False
    assert old.read_bytes() == b"unrelated old file"
    assert pending.read_bytes() == b"unrelated file"
    assert cache.read_bytes() == original_cache


def test_post_update_without_marker_preserves_pending_and_temporary_file(isolated):
    pending = updater._get_pending_path()
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"unrelated pending")
    temporary = updater._get_pending_tmp_path()
    temporary.write_bytes(b"unrelated temporary")
    assert updater.check_and_apply_pending(post_update=True) is False
    assert pending.read_bytes() == b"unrelated pending"
    assert temporary.read_bytes() == b"unrelated temporary"


@pytest.mark.parametrize("field,value", [("candidate_id", "b" * 32), ("build", 1),
                                       ("state", "preparing"), ("sha256", "0" * 64)])
def test_unowned_or_modified_candidate_cannot_be_applied(isolated, monkeypatch, field, value):
    identity = "a" * 32
    build = updater.APP_VERSION + 1
    updater._write_update_record(updater._get_cache_path(), {"candidate_id": identity, "pending_version": build})
    pending = updater._get_pending_path()
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"candidate bytes")
    record = {"schema_version": 1, "candidate_id": identity, "build": build,
              "state": "verified", "sha256": hashlib.sha256(pending.read_bytes()).hexdigest()}
    record[field] = value
    updater._write_update_record(pending.parent / "candidate.json", record)
    monkeypatch.setattr(updater, "_do_apply", lambda *a: pytest.fail("unowned candidate applied"))
    assert not updater._apply_owned_pending(pending, build)
    assert pending.read_bytes() == b"candidate bytes"


def test_hardlinked_candidate_is_preserved_and_rejected(isolated, monkeypatch):
    identity = "a" * 32
    build = updater.APP_VERSION + 1
    updater._write_update_record(updater._get_cache_path(), {"candidate_id": identity, "pending_version": build})
    pending = updater._get_pending_path()
    pending.parent.mkdir(parents=True)
    unrelated = isolated / "user-file"
    unrelated.write_bytes(b"candidate bytes")
    os.link(unrelated, pending)
    updater._write_update_record(pending.parent / "candidate.json", {
        "schema_version": 1, "candidate_id": identity, "build": build,
        "state": "verified", "sha256": hashlib.sha256(unrelated.read_bytes()).hexdigest()})
    monkeypatch.setattr(updater, "_do_apply", lambda *a: pytest.fail("hardlinked candidate applied"))
    assert not updater._apply_owned_pending(pending, build)
    assert unrelated.read_bytes() == pending.read_bytes() == b"candidate bytes"


def test_hidden_post_update_option_is_parsed_and_not_advertised(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_boot", lambda **kw: calls.append(kw) or {})
    monkeypatch.setattr(main, "_main_menu", lambda cfg: None)
    monkeypatch.setattr("core.cli.require_interaction", lambda: None)
    runner = CliRunner()
    result = runner.invoke(main.app, ["--post-update", "--post-update"])
    assert result.exit_code == 0, result.output
    assert calls == [{"post_update": True}]
    assert "--post-update" not in runner.invoke(main.app, ["--help"]).output


@pytest.mark.parametrize("tail", [[], ["software", "search", "--", "--post-update"]])
def test_restart_does_not_duplicate_program_name_or_internal_flag(monkeypatch, tail):
    monkeypatch.setattr(sys, "argv", ["old-program", "--lang", "en", "--post-update", "--post-update", *tail])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(main, "ensure_config", lambda: {"log": {}, "update": {}})
    monkeypatch.setattr(main, "theme_init", lambda: None)
    monkeypatch.setattr(main, "i18n_init", lambda: None)
    monkeypatch.setattr(main, "print_info", lambda *_: None)
    monkeypatch.setattr("core.logger.init", lambda *_: None)
    monkeypatch.setattr("core.cleanup.init", lambda: None)
    monkeypatch.setattr("core.platform.preflight_check", lambda: [])
    monkeypatch.setattr(updater, "check_and_apply_pending", lambda **kw: True)
    monkeypatch.setattr(updater, "pending_version", lambda: 2)
    calls = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))
    main._boot(post_update=True)
    assert calls == [(sys.executable, [sys.executable, "--post-update", "--lang", "en", *tail])]


@pytest.mark.parametrize("command", [
    ["software", "list"],
    ["software", "manage", "example"],
    ["software", "search", "--", "--post-update"],
])
def test_restart_preserves_subcommand_and_propagates_recovery_flag(monkeypatch, command):
    """Apply before business, then resume it once with the recovery flag."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["old-program", *command])
    monkeypatch.setattr(main, "ensure_config", lambda: {"log": {}, "update": {}})
    monkeypatch.setattr(main, "theme_init", lambda *_: None)
    monkeypatch.setattr(main, "i18n_init", lambda **_: None)
    monkeypatch.setattr(main, "print_info", lambda *_: None)
    monkeypatch.setattr("core.logger.init", lambda *_: None)
    monkeypatch.setattr("core.cleanup.init", lambda: None)
    monkeypatch.setattr("core.platform.preflight_check", lambda: [])
    monkeypatch.setattr("core.cli.require_interaction", lambda: None)
    monkeypatch.setattr(main, "_software_inventory", lambda **_: False)
    business = []
    monkeypatch.setattr(main, "_print_software_table", lambda **kwargs: business.append(kwargs))
    monkeypatch.setattr(main, "_sw_action_by_name", lambda *args: business.append(args))
    checks = []

    def check(*, post_update):
        checks.append(post_update)
        return not post_update

    monkeypatch.setattr(updater, "check_and_apply_pending", check)
    monkeypatch.setattr(updater, "pending_version", lambda: 2)
    restarts = []

    def restart(path, argv):
        restarts.append((path, argv))
        raise SystemExit(71)

    monkeypatch.setattr(os, "execv", restart)
    runner = CliRunner()
    first = runner.invoke(main.app, command)
    assert first.exit_code == 71, (first.output, first.exception)
    assert not business
    assert restarts == [(sys.executable, [sys.executable, "--post-update", *command])]

    monkeypatch.setattr(sys, "argv", restarts[0][1])
    second = runner.invoke(main.app, restarts[0][1][1:])
    assert second.exit_code == 0, (second.output, second.exception)
    assert checks == [False, True]
    assert len(business) == 1
    if command[1] == "search":
        assert business == [{"query": "--post-update"}]


def test_preinitialization_report_logs_only_event_without_context(monkeypatch):
    logs, reports = [], []
    monkeypatch.setattr(updater._log, "log", lambda level, message, *args: logs.append(message % args))
    telemetry = types.SimpleNamespace(capture_error=lambda *a, **kw: reports.append((a, kw)),
                                      capture_message=lambda *a, **kw: reports.append((a, kw)))
    monkeypatch.setitem(sys.modules, "core.telemetry", telemetry)
    monkeypatch.setattr(core, "telemetry", telemetry, raising=False)
    updater._report("update_rollback_failed", exc=RuntimeError("secret exception"),
                    level="error", pending="secret path")
    assert logs == ["update event: update_rollback_failed"]
    assert len(reports) == 1
    token = updater._query_readonly.set(True)
    try:
        updater._report("update_rolled_back", pending="secret")
    finally:
        updater._query_readonly.reset(token)
    assert len(logs) == len(reports) == 1


def test_untrusted_report_name_is_not_written_to_log(monkeypatch):
    logs = []
    monkeypatch.setattr(updater._log, "log", lambda level, message, *args: logs.append(message % args))
    telemetry = types.SimpleNamespace(capture_message=lambda *a, **kw: None)
    monkeypatch.setitem(sys.modules, "core.telemetry", telemetry)
    monkeypatch.setattr(core, "telemetry", telemetry, raising=False)
    updater._report("secret path /users/alice")
    assert logs == ["update event: invalid_event"]


def test_update_report_survives_local_logging_failure(monkeypatch):
    reports = []
    monkeypatch.setattr(updater._log, "log", lambda *args: (
        _ for _ in ()).throw(PermissionError("private log path")))
    telemetry = types.SimpleNamespace(capture_error=lambda *a, **kw: reports.append((a, kw)),
                                      capture_message=lambda *a, **kw: reports.append((a, kw)))
    monkeypatch.setitem(sys.modules, "core.telemetry", telemetry)
    monkeypatch.setattr(core, "telemetry", telemetry, raising=False)

    updater._report("update_rollback_failed", exc=RuntimeError("private"), level="error")
    assert len(reports) == 1


@pytest.mark.parametrize("state", ["uninitialized", "disabled", "init_failure"])
def test_update_report_keeps_private_local_diagnostic_without_provider(monkeypatch, state):
    import core.telemetry as telemetry

    logs = []
    monkeypatch.setattr(updater._log, "log", lambda level, message, *args: logs.append(message % args))
    monkeypatch.setattr(telemetry, "_provider", None)
    if state == "disabled":
        telemetry.init({"telemetry": {"enabled": False, "dsn": ""}})
    elif state == "init_failure":
        def fail_provider(*args, **kwargs):
            raise RuntimeError("private provider error")
        monkeypatch.setattr(telemetry, "SentryProvider", fail_provider)
        telemetry.init({"telemetry": {"enabled": True, "dsn": "https://example.invalid"}})

    updater._report("update_rollback_failed", exc=RuntimeError("private exception"),
                    level="error", pending="private path")
    assert logs == ["update event: update_rollback_failed"]
