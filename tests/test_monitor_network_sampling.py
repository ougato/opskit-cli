"""Counter reset and invocation ownership, not just nonnegative formatting."""
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import main
from monitor import cli, commands


def install_counters(monkeypatch, frames, times):
    import psutil
    frames = iter(frames)
    times = iter(times)

    def read(*, pernic, nowrap):
        assert pernic is True and nowrap is False
        return {name: SimpleNamespace(bytes_sent=sent, bytes_recv=recv)
                for name, (sent, recv) in next(frames).items()}

    monkeypatch.setattr(psutil, "net_io_counters", read)
    monkeypatch.setattr(commands.time, "monotonic", lambda: next(times))


def test_reset_is_unavailable_then_rebases(monkeypatch):
    install_counters(monkeypatch, [dict(eth0=x) for x in
                                  [(1000, 2000), (500, 1000), (600, 1200)]], [100, 101, 102])
    with commands.sampling_session():
        first, reset, valid = [commands.get_net()[0] for _ in range(3)]
    assert (first.rate_status, first.speed_send, first.window_seconds) == ("initial", None, None)
    assert (reset.rate_status, reset.speed_send, reset.speed_recv) == ("counter_reset", None, None)
    assert (valid.rate_status, valid.speed_send, valid.speed_recv, valid.window_seconds) == ("available", 100, 200, 1)
    assert valid.rate_unit == "bytes/s" and valid.counter_unit == "bytes"
    assert valid.sampled_at.endswith("+00:00")


@pytest.mark.parametrize("second, time", [((90, 220), 101), ((110, 190), 101), ((110, 220), 100)])
def test_one_counter_reset_or_invalid_clock_never_reports_rate(monkeypatch, second, time):
    install_counters(monkeypatch, [dict(eth0=(100, 200)), dict(eth0=second)], [100, time])
    with commands.sampling_session():
        commands.get_net()
        result = commands.get_net()[0]
    assert result.speed_send is None and result.speed_recv is None
    assert result.rate_status == ("invalid_window" if time == 100 else "counter_reset")


def test_disappeared_interface_restarts_baseline(monkeypatch):
    install_counters(monkeypatch, [dict(eth0=(100, 200)), {}, dict(eth0=(200, 400))], [1, 2, 3])
    with commands.sampling_session():
        commands.get_net()
        assert commands.get_net() == []
        result = commands.get_net()[0]
    assert result.rate_status == "initial" and result.speed_recv is None


def test_nested_sessions_restore_outer_and_cleanup_on_cancel(monkeypatch):
    install_counters(monkeypatch, [dict(eth0=x) for x in [(100, 200), (200, 400), (300, 600), (400, 800)]], [1, 2, 3, 4])
    with commands.sampling_session():
        assert commands.get_net()[0].rate_status == "initial"
        with pytest.raises(KeyboardInterrupt):
            with commands.sampling_session():
                assert commands.get_net()[0].rate_status == "initial"
                raise KeyboardInterrupt()
        assert commands.get_net()[0].speed_send == 100
    assert commands.get_net()[0].rate_status == "initial"


def test_interfaces_share_observation_timestamp_and_window(monkeypatch):
    install_counters(monkeypatch, [dict(a=(10, 20), b=(30, 40)), dict(a=(20, 40), b=(50, 80))], [10, 12])
    with commands.sampling_session():
        commands.get_net()
        a, b = commands.get_net()
    assert a.sampled_at == b.sampled_at and a.window_seconds == b.window_seconds == 2
    assert (a.speed_send, b.speed_recv) == (5, 20)


def test_cli_jsonl_and_new_invocation_isolation(monkeypatch):
    install_counters(monkeypatch, [dict(eth0=x) for x in [(100, 200), (110, 220), (120, 240)]], [1, 2, 3])
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    runner = CliRunner()
    args = ["--output", "json", "monitor", "network"]
    result = runner.invoke(main.app, [*args, "--count", "2"])
    assert result.exit_code == 0, result.output
    first, second = [json.loads(line)["data"][0] for line in result.stdout.splitlines()]
    assert first["speed_send"] is None and first["rate_status"] == "initial"
    assert second["speed_recv"] == 20 and second["rate_status"] == "available"
    again = runner.invoke(main.app, [*args, "--once"])
    assert again.exit_code == 0, again.output
    assert json.loads(again.stdout)["data"][0]["rate_status"] == "initial"


def test_menu_unknown_rate_is_not_zero():
    from monitor.menu import _format_net_rate
    assert _format_net_rate(None) == "N/A"
    assert _format_net_rate(0) == "0.0 B/s"


def test_menu_page_owns_and_releases_baselines(monkeypatch):
    from monitor import menu
    install_counters(monkeypatch, [dict(eth0=x) for x in [(100, 200), (110, 220), (120, 240)]], [1, 2, 3])

    def page(render):
        assert commands.get_net()[0].rate_status == "initial"
        assert commands.get_net()[0].speed_recv == 20
        raise KeyboardInterrupt()

    monkeypatch.setattr(menu, "_live_loop_scoped", page)
    with pytest.raises(KeyboardInterrupt):
        menu._live_loop(lambda: None)
    assert commands.get_net()[0].rate_status == "initial"


def test_interface_quota_rejects_without_creating_baseline(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "net_io_counters", lambda **kwargs: {str(i): None for i in range(4097)})
    with commands.sampling_session():
        with pytest.raises(RuntimeError, match="quota"):
            commands.get_net()
        assert commands._net_session.get() == {}
