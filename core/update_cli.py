"""Explicit platform update commands, independent of boot/background updates."""
import json
import os
import stat

import typer

from core.cli import emit, guarded, fail


def _status_worker(connection, cache_path, pending_path, supported, version):
    from network.worker import send
    from pathlib import Path
    try:
        cache = {}
        try:
            with open(cache_path, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                    raise ValueError()
                raw = source.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            cache = json.loads(raw)
            if not isinstance(cache, dict):
                raise ValueError()
        except FileNotFoundError:
            pass
        pending_version = cache.get("pending_version")
        if pending_version is not None and (type(pending_version) is not int or pending_version <= 0):
            raise ValueError()
        candidate_id = cache.get("candidate_id")
        if candidate_id is not None:
            import re
            if not isinstance(candidate_id, str) or not re.fullmatch(r"[a-f0-9]{32}", candidate_id):
                raise ValueError()
            pending_path = Path(cache_path).parent / "update-candidates" / candidate_id / "opskit.pending"
        from core.updater import _update_record_path
        _update_record_path(Path(pending_path))
        present = Path(pending_path).is_file()
        data = {
            "current_version": version, "binary_update_supported": supported,
            "pending_present": present, "pending_version": pending_version,
            "pending_verification": "not_checked"}
        if candidate_id is not None:
            data["candidate_id"] = candidate_id
        job_id = cache.get("apply_job")
        history_only = job_id is None and cache.get("last_apply_job") is not None
        if history_only:
            job_id = cache["last_apply_job"]
        if job_id is not None:
            import re
            from core.updater import _read_update_record
            if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
                raise ValueError()
            job = Path(cache_path).parent / "update-jobs" / job_id
            metadata = _read_update_record(job / "job.json")
            if (type(metadata.get("version")) is not int
                    or metadata.get("state") not in {"preparing", "launching", "scheduled", "retired"}):
                raise ValueError()
            if "schema_version" in metadata and (type(metadata["schema_version"]) is not int
                    or metadata["schema_version"] not in {1, 2} or metadata.get("job_id") != job_id):
                raise ValueError()
            from core.update_reconcile import read_receipt
            from core.update_retirement import receipt as retirement_receipt
            retired = retirement_receipt(metadata)
            if history_only and retired is None:
                raise ValueError()
            receipt = None if retired is not None else read_receipt(job, metadata)
            if retired is not None:
                result = {"state": retired["outcome"], "version": metadata["version"]}
            elif receipt is not None:
                result = receipt
            else:
                try:
                    result = _read_update_record(job / "result.json")
                except FileNotFoundError:
                    result = metadata
            state = result.get("state")
            job_version = result.get("version")
            if (state not in {"preparing", "launching", "scheduled", "applied", "failed", "launch_failed", "applied_recovery_incomplete", "rolled_back"}
                    or type(job_version) is not int or job_version <= 0
                    or job_version != metadata.get("version")):
                raise ValueError()
            data["apply_job"] = {"id": job_id, "version": job_version, "state": state,
                                 "health_confirmed": False}
            if receipt is not None:
                data["apply_job"]["reconciled"] = True
            if retired is not None:
                data["apply_job"]["active"] = False
                data["apply_job"]["reconciled"] = True
                if retired["outcome"] == "rolled_back":
                    data["apply_job"]["restored_version"] = retired["restored_build"]
            from core.update_jobs import helper_status
            data["apply_job"]["helper_status"] = helper_status(job, metadata)
            from core.constants import FILE_UPDATE_HEALTH
            try:
                health = _read_update_record(Path(cache_path).parent / FILE_UPDATE_HEALTH)
                data["apply_job"]["health_confirmed"] = (type(health.get("build")) is int
                    and health.get("build") == job_version
                    and health.get("confirmed") is True
                    and health.get("job_id") == job_id
                    and state in {"launching", "scheduled", "applied", "applied_recovery_incomplete"})
            except FileNotFoundError:
                pass
            if retired is not None and retired["outcome"] == "applied":
                data["apply_job"]["health_confirmed"] = True
        send(connection, {"ok": True, "data": data})
    except (OSError, ValueError):
        send(connection, {"ok": False, "error": "update status unavailable or invalid"})
    finally:
        connection.close()


def _check_worker(connection):
    from network.worker import send
    from core import updater
    from core.config import load_config
    try:
        cfg = load_config(max_bytes=65536).get("update", {})
        if not isinstance(cfg, dict):
            raise ValueError()
        target = updater.query_update_target(cfg)
        result = {"current_version": updater.APP_VERSION, "available": target is not None,
                  "signature_verified": False}
        if target is None:
            result["reason"] = "no_eligible_update"
        else:
            version, _, _ = target
            from core.update_target_state import validate_upgrade
            try:
                validate_upgrade(updater._self_path(), version)
            except ValueError as exc:
                if str(exc) != "rolled back update version refused":
                    raise
                result.update({"available": False, "reason": "rolled_back_version"})
            else:
                result["candidate_version"] = version
        send(connection, {"ok": True, "data": result})
    except Exception as exc:
        # Config/API exceptions can contain authorization headers or raw bodies.
        safe_errors = {"update rollout identity is not initialized", "invalid update manifest version",
                       "update release query failed", "update release has no matching platform artifact",
                       "invalid update release version", "invalid update artifact URL",
                       "invalid update repository", "configuration exceeds size limit"}
        message = str(exc) if str(exc) in safe_errors else "update check failed"
        send(connection, {"ok": False, "error": message})
    finally:
        connection.close()


def _result(message):
    if not message.get("ok"):
        raise RuntimeError(message.get("error", "update operation failed"))
    return message["data"]


def status(*, timeout=10):
    from core import updater
    from network.worker import run
    return _result(run(_status_worker, (str(updater._get_cache_path()), str(updater._get_cache_path().parent / "opskit.pending"),
                       updater._is_frozen(), updater.APP_VERSION), timeout=timeout, label="update status"))


def check(*, timeout=30):
    from network.worker import run
    return _result(run(_check_worker, (), timeout=timeout, label="update check"))


def _reconcile_worker(connection, on_boot=False):
    from network.worker import send
    from core.update_reconcile import reconcile, reconcile_on_boot
    try:
        send(connection, {"ok": True, "data": reconcile_on_boot() if on_boot else reconcile()})
    except Exception:
        send(connection, {"ok": False, "error": "update reconciliation refused or incomplete"})
    finally:
        connection.close()


def reconcile(*, timeout=30):
    return _run_reconciliation(timeout, on_boot=False)


def reconcile_on_boot(*, timeout=30):
    return _run_reconciliation(timeout, on_boot=True)


def _run_reconciliation(timeout, *, on_boot):
    from core import updater
    from network.worker import run
    if not updater._is_frozen():
        raise ValueError("binary updates require a packaged executable")
    return _result(run(_reconcile_worker, (on_boot,), timeout=timeout, label="update reconciliation"))


def register(app, context_settings):
    group = typer.Typer(context_settings=context_settings)
    app.add_typer(group, name="update")

    @group.command("status")
    @guarded
    def show_status(timeout: float = typer.Option(10, "--timeout", min=.1, max=3600)):
        emit(status(timeout=timeout))

    @group.command("check")
    @guarded
    def check_update(timeout: float = typer.Option(30, "--timeout", min=.1, max=3600)):
        emit(check(timeout=timeout))

    @group.command("apply")
    @guarded
    def apply_update():
        from core import updater
        from core.prompt import confirm
        if not updater._is_frozen():
            fail("binary updates require a packaged executable", 2)
        if not confirm(breadcrumb=["OpsKit", "update"], prompt="Apply the verified pending update?"):
            fail("update application cancelled")
        emit(updater.apply_verified_pending())

    @group.command("download")
    @guarded
    def download_update(timeout: float = typer.Option(300, "--timeout", min=.1, max=3600)):
        from core import updater
        from core.update_download import download
        if not updater._is_frozen():
            fail("binary updates require a packaged executable", 2)
        emit(download(timeout=timeout))

    @group.command("reconcile")
    @guarded
    def reconcile_update(timeout: float = typer.Option(30, "--timeout", min=.1, max=3600)):
        from core import updater
        if not updater._is_frozen():
            fail("binary updates require a packaged executable", 2)
        emit(reconcile(timeout=timeout))
