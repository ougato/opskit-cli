"""Shared counter windows, platform accounting and unavailable sample semantics."""
from collections import namedtuple
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import main
from monitor import commands, cpu_sampling


def counters(**values):
    return namedtuple("Counters", values)(**values)


def zeros(value):
    return type(value)(*[0 for _ in value])


def test_aggregate_is_tick_weighted_not_mean_of_rounded_percentages():
    a = counters(user=10, system=0, idle=0)
    b = counters(user=0, system=0, idle=30)
    percent, per_core, status = cpu_sampling.calculate([zeros(a), zeros(b)], [a, b], linux=False)
    assert (percent, per_core, status) == (25, [100, 0], "available")


def test_linux_guest_is_not_double_counted_and_iowait_is_not_busy():
    new = counters(user=10, nice=4, system=2, idle=5, iowait=5, guest=5, guest_nice=2)
    result = cpu_sampling.calculate([zeros(new)], [new], linux=True)
    assert result == (61.5, [61.5], "available")  # total26, busy16; guest already in user/nice


def test_windows_interrupt_and_dpc_match_psutil_counter_accounting():
    new = counters(user=5, system=5, idle=8, interrupt=1, dpc=1)
    assert cpu_sampling.calculate([zeros(new)], [new], linux=False) == (60, [60], "available")


@pytest.mark.parametrize("value,status", [
    (counters(user=0, idle=0), "no_ticks"),
    (counters(user=float("nan"), idle=1), "invalid_counters"),
    (counters(user=float("inf"), idle=1), "invalid_counters"),
    (counters(user=-1, idle=1), "invalid_counters"),
])
def test_invalid_or_empty_ticks_are_not_reported_as_idle(value, status):
    assert cpu_sampling.calculate([zeros(value)], [value], linux=False) == (None, [None], status)


def test_counter_regression_invalidates_whole_sample_instead_of_clamping():
    old = counters(user=10, idle=5)
    after = counters(user=9, idle=6)
    assert cpu_sampling.calculate([old], [after], linux=False) == (None, [None], "counter_reset")


def test_topology_and_field_changes_are_unavailable():
    new = counters(user=10, idle=10)
    assert cpu_sampling.calculate([zeros(new)], [new, new], linux=False) == (None, [None, None], "topology_changed")
    other = counters(user=10, idle=10, system=0)
    assert cpu_sampling.calculate([zeros(new)], [other], linux=False) == (None, [None], "invalid_counters")


def test_inconsistent_linux_guest_metadata_is_rejected():
    new = counters(user=1, idle=0, guest=10)
    assert cpu_sampling.calculate([zeros(new)], [new], linux=True) == (None, [None], "invalid_counters")


def install_reader(monkeypatch, before, after, *, times=(10, 10.15)):
    import psutil
    calls = []
    rows = iter((before, after))
    clock = iter(times)

    def read(*, percpu):
        assert percpu is True
        calls.append("read")
        return next(rows)

    def sleep(delay):
        assert calls == ["read"] and delay == .1
        calls.append("wait")

    monkeypatch.setattr(psutil, "cpu_times", read)
    monkeypatch.setattr(psutil, "cpu_percent", lambda **kwargs: pytest.fail("global percent caches must not be used"))
    monkeypatch.setattr(psutil, "cpu_freq", lambda: SimpleNamespace(current=1000))
    monkeypatch.setattr(psutil, "cpu_count", lambda **kwargs: 1)
    monkeypatch.setattr(cpu_sampling.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cpu_sampling.time, "sleep", sleep)
    return calls


def test_one_pair_and_one_wait_with_measured_window(monkeypatch):
    new = counters(user=10, idle=10)
    calls = install_reader(monkeypatch, [zeros(new)], [new])
    result = commands.get_cpu()
    assert calls == ["read", "wait", "read"]
    assert result.percent == 50 and result.per_core == [50]
    assert result.window_seconds == pytest.approx(.15)
    assert result.started_at.endswith("+00:00") and result.sampled_at.endswith("+00:00")
    assert result.rate_schema_version == 2 and result.rate_unit == "percent"


def test_invalid_window_has_nullable_rates(monkeypatch):
    new = counters(user=10, idle=10)
    install_reader(monkeypatch, [zeros(new)], [new], times=(10, 10))
    result = commands.get_cpu()
    assert result.percent is None and result.per_core == [None]
    assert result.window_seconds is None and result.rate_status == "invalid_window"


def test_cancel_during_wait_never_reads_second_counter(monkeypatch):
    new = counters(user=10, idle=10)
    calls = install_reader(monkeypatch, [zeros(new)], [new])

    def cancel(delay):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cpu_sampling.time, "sleep", cancel)
    with pytest.raises(KeyboardInterrupt):
        commands.get_cpu()
    assert calls == ["read"]


def test_inventory_quota_is_enforced_before_wait(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "cpu_times", lambda **kwargs: [None] * 4097)
    monkeypatch.setattr(cpu_sampling.time, "sleep", lambda _: pytest.fail("over-quota data must not be sampled"))
    with pytest.raises(RuntimeError, match="quota"):
        commands.get_cpu()


def test_cli_unavailable_preserves_data_and_exits_nonzero(monkeypatch):
    new = counters(user=0, idle=0)
    install_reader(monkeypatch, [new], [new])
    result = CliRunner().invoke(main.app, ["--output", "json", "monitor", "cpu", "--once"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False and payload["data"]["rate_status"] == "no_ticks"
    assert payload["data"]["percent"] is None


def test_cpu_menu_renders_unavailable_without_numeric_formatting(monkeypatch):
    from monitor import menu
    from rich.console import Console
    sample = commands.CpuInfo(None, [None], 0, 1, 1, rate_status="counter_reset")
    monkeypatch.setattr(commands, "get_cpu", lambda: sample)
    rendered = []

    def render_page(render):
        console = Console(force_terminal=False, width=100)
        with console.capture() as output:
            console.print(render())
        rendered.append(output.get())

    monkeypatch.setattr(menu, "_live_loop", render_page)
    menu.show_cpu_detail()
    assert rendered and rendered[0].count("N/A") == 2
