"""PID generation ownership, first samples, failures and bounded inventory."""
from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import main
from monitor import commands, process_sampling as sampling


class FakeProcess:
    def __init__(self, values):
        self.values = values

    def oneshot(self):
        return nullcontext()

    def create_time(self):
        return self.values.get("created", 1.0)

    def cpu_times(self):
        return SimpleNamespace(user=self.values.get("user", 0.0), system=self.values.get("system", 0.0))

    def name(self):
        return "owned"

    def status(self):
        return "running"

    def memory_percent(self):
        return self.values.get("memory", 1.0)


def install_frames(monkeypatch, frames, clocks):
    import psutil
    frames = iter(frames)
    clocks = iter(clocks)
    calls, active = [], []

    def process(pid):
        calls.append(pid)
        if len(calls) % 2:
            active[:] = [next(frames)]
        return FakeProcess(active[0])

    monkeypatch.setattr(psutil, "Process", process)
    monkeypatch.setattr(sampling, "time", SimpleNamespace(monotonic=lambda: next(clocks), sleep=lambda _: None))
    return calls


def test_first_sample_is_nullable_then_same_generation_measures_cpu(monkeypatch):
    install_frames(monkeypatch, [dict(user=10, system=5), dict(user=11, system=5.5)], [100, 101])
    with commands.sampling_session():
        initial = commands.get_process(123)
        measured = commands.get_process(123)
    assert initial.cpu_percent is None and initial.rate_status == "initial"
    assert (measured.cpu_percent, measured.window_seconds, measured.rate_status) == (150, 1, "available")
    assert measured.created_at == 1 and measured.cpu_scope == "one_logical_cpu"
    assert measured.rate_schema_version == 2 and measured.sampled_at.endswith("+00:00")


def test_pid_reuse_never_inherits_previous_cpu(monkeypatch):
    install_frames(monkeypatch, [dict(created=1, user=100), dict(created=2, user=500), dict(created=2, user=501)], [1, 2, 3])
    with commands.sampling_session():
        commands.get_process(123)
        replaced = commands.get_process(123)
        valid = commands.get_process(123)
    assert replaced.rate_status == "identity_changed" and replaced.cpu_percent is None
    assert replaced.created_at == 2 and valid.cpu_percent == 100


def test_identity_change_during_read_rejects_mixed_attributes(monkeypatch):
    import psutil
    records = iter([dict(created=1), dict(created=2)])
    monkeypatch.setattr(psutil, "Process", lambda pid: FakeProcess(next(records)))
    sampler = sampling.ProcessSampler()
    with pytest.raises(sampling.ProcessSamplingError) as failure:
        sampler.read(123)
    assert failure.value.data["rate_status"] == "identity_changed" and sampler.previous == {}


@pytest.mark.parametrize("error,reason", [("NoSuchProcess", "gone"), ("AccessDenied", "access_denied"), ("ZombieProcess", "zombie")])
def test_failure_reasons_and_baseline_removal(monkeypatch, error, reason):
    import psutil
    sampler = sampling.ProcessSampler()
    sampler.previous[123] = (1, 100, 200, 1)

    def unavailable(pid):
        raise getattr(psutil, error)(pid)

    monkeypatch.setattr(psutil, "Process", unavailable)
    with pytest.raises(sampling.ProcessSamplingError) as failure:
        sampler.read(123)
    assert failure.value.data["rate_status"] == reason
    assert failure.value.data["cpu_percent"] is None and sampler.previous == {}


@pytest.mark.parametrize("after,clock,reason", [
    (dict(user=9, system=5), 2, "counter_reset"),
    (dict(user=10, system=4), 2, "counter_reset"),
    (dict(user=11, system=5), 1, "invalid_window"),
    (dict(user=float("nan")), 2, "invalid_counters"),
    (dict(memory=float("inf")), 2, "invalid_counters"),
])
def test_bad_measurements_never_become_idle_or_negative(monkeypatch, after, clock, reason):
    install_frames(monkeypatch, [dict(user=10, system=5), after], [1, clock])
    with commands.sampling_session():
        commands.get_process(123)
        result = commands.get_process(123)
    assert result.cpu_percent is None and result.rate_status == reason
    json.dumps(result.__dict__, allow_nan=False)


def test_unchanged_counters_with_positive_window_are_real_idle(monkeypatch):
    install_frames(monkeypatch, [dict(user=10), dict(user=10)], [1, 2])
    with commands.sampling_session():
        commands.get_process(123)
        result = commands.get_process(123)
    assert result.rate_status == "available" and result.cpu_percent == 0


def test_nested_and_new_sessions_do_not_share_process_baselines(monkeypatch):
    install_frames(monkeypatch, [dict(user=x) for x in [10, 50, 12, 14]], [1, 2, 3, 4])
    with commands.sampling_session():
        assert commands.get_process(123).rate_status == "initial"
        with commands.sampling_session():
            assert commands.get_process(123).rate_status == "initial"
        assert commands.get_process(123).cpu_percent == 100
    assert commands.get_process(123).rate_status == "initial"


def test_inventory_prunes_before_replacement_and_retains_failure_rows(monkeypatch):
    import psutil
    sampler = sampling.ProcessSampler()
    sampler.previous[999] = (1, 0, 0, 1)
    monkeypatch.setattr(sampling, "MAX_PROCESSES", 1)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([SimpleNamespace(pid=123)]))

    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", denied)
    rows = sampler.inventory()
    assert sampler.previous == {} and rows[0]["rate_status"] == "access_denied"


def test_inventory_limit_stops_before_metadata_reads(monkeypatch):
    import psutil
    monkeypatch.setattr(sampling, "MAX_PROCESSES", 1)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([SimpleNamespace(pid=1), SimpleNamespace(pid=2)]))
    monkeypatch.setattr(psutil, "Process", lambda pid: pytest.fail("over-quota inventory must not be sampled"))
    with pytest.raises(RuntimeError, match="quota"):
        sampling.ProcessSampler().inventory()


def test_first_cpu_top_primes_once_and_known_zero_ranks_above_unknown(monkeypatch):
    frames = iter([
        [dict(pid=1, cpu_percent=None, mem_percent=2, rate_status="initial")],
        [dict(pid=1, cpu_percent=0, mem_percent=2, rate_status="available"),
         dict(pid=2, cpu_percent=None, mem_percent=None, rate_status="access_denied")],
    ])
    sleeps = []
    monkeypatch.setattr(sampling.ProcessSampler, "inventory", lambda self: next(frames))
    monkeypatch.setattr(sampling, "time", SimpleNamespace(sleep=lambda value: sleeps.append(value)))
    assert [row["pid"] for row in sampling.top(2, "cpu")] == [1, 2]
    assert sleeps == [.1]


def test_memory_top_has_no_cpu_priming_wait_and_preserves_ties(monkeypatch):
    rows = [dict(pid=3, cpu_percent=None, mem_percent=2), dict(pid=1, cpu_percent=None, mem_percent=2), dict(pid=2, cpu_percent=None, mem_percent=None)]
    monkeypatch.setattr(sampling.ProcessSampler, "inventory", lambda self: list(rows))
    monkeypatch.setattr(sampling, "time", SimpleNamespace(sleep=lambda _: pytest.fail("memory ranking must not prime CPU")))
    assert [row["pid"] for row in sampling.top(2, "memory")] == [3, 1]


def test_pid_bound_is_a_parser_error_before_collection(monkeypatch):
    monkeypatch.setattr(commands, "get_process", lambda pid: pytest.fail("invalid PID reached collector"))
    result = CliRunner().invoke(main.app, ["--output", "json", "monitor", "processes", "--pid", "4294967296"])
    assert result.exit_code == 2 and json.loads(result.stdout)["ok"] is False


def test_cli_initial_then_measured_jsonl(monkeypatch):
    from monitor import cli
    install_frames(monkeypatch, [dict(user=10), dict(user=11)], [1, 2])
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    result = CliRunner().invoke(main.app, ["--output", "json", "monitor", "processes", "--pid", "123", "--count", "2"])
    assert result.exit_code == 0, result.output
    first, second = [json.loads(line)["data"][0] for line in result.stdout.splitlines()]
    assert first["cpu_percent"] is None and second["cpu_percent"] == 100
