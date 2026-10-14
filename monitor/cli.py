"""Official monitoring CLI, independent of menu rendering."""
import sys
import time
import inspect
import typer
from core.cli import emit, fail, non_interactive, output_mode
from monitor import commands
from monitor.process_sampling import ProcessSamplingError


def register(app):
    for name in ("dashboard", "cpu", "memory", "disk", "network", "processes"):
        app.command(name)(make_command(name))


def make_command(name):
    def command(
        once: bool = typer.Option(False, "--once"),
        watch: bool = typer.Option(False, "--watch"),
        interval: float = typer.Option(1.0, "--interval", min=0.1, max=3600),
        count: int = typer.Option(0, "--count", min=0),
        limit: int = typer.Option(15, "--limit", min=1, max=10000),
        sort: str = typer.Option("cpu", "--sort"),
        pid: int | None = typer.Option(None, "--pid", min=1, max=4294967295),
        interface: str = typer.Option("", "--interface"),
        mount: str = typer.Option("", "--mount"),
    ):
        if once and watch:
            fail("--once and --watch conflict", 2)
        if sort not in {"cpu", "memory"}:
            fail("--sort must be cpu or memory", 2)
        if (pid is not None or limit != 15 or sort != "cpu") and name != "processes":
            fail("process filters require monitor processes", 2)
        if interface and name != "network" or mount and name != "disk":
            fail("filter does not apply to this monitor", 2)
        if once and count > 1:
            fail("--once permits only one sample", 2)
        live = watch or count > 1
        filtered = pid is not None or limit != 15 or sort != "cpu" or bool(interface or mount)
        if not once and not live and not filtered and sys.stdout.isatty() and not non_interactive.get() and output_mode.get() == "text" and name != "disk":
            from monitor import menu
            getattr(menu, {"dashboard": "show_dashboard", "cpu": "show_cpu_detail", "memory": "show_memory_detail", "network": "show_network_detail", "processes": "show_processes"}[name])()
            return
        samples = count or (0 if live else 1)
        try:
            i = 0
            while samples == 0 or i < samples:
                if name == "processes":
                    if pid is not None:
                        process = commands.get_process(pid)
                        data = [process]
                        if process.rate_status not in {"initial", "available"}:
                            emit(data, error={"code": 1, "message": "process sampling is unavailable: " + process.rate_status})
                            raise typer.Exit(1)
                    else:
                        data = commands.get_top_processes(limit, sort)
                else:
                    data = getattr(commands, {"dashboard": "get_snapshot", "cpu": "get_cpu", "memory": "get_mem", "disk": "get_disks", "network": "get_net"}[name])()
                    if interface:
                        data = [item for item in data if item.name == interface]
                        if not data:
                            fail("network interface not found", 2)
                    if mount:
                        data = [item for item in data if item.mountpoint == mount]
                        if not data:
                            fail("mount is unavailable or inaccessible", 2)
                cpu = data.cpu if name == "dashboard" else data if name == "cpu" else None
                if cpu is not None and cpu.percent is None:
                    emit(data, error={"code": 1, "message": "CPU sampling is unavailable: " + cpu.rate_status})
                    raise typer.Exit(1)
                emit(data)
                i += 1
                if samples == 0 or i < samples:
                    time.sleep(interval)
        except KeyboardInterrupt:
            raise typer.Exit(130)
        except typer.Exit:
            raise
        except ProcessSamplingError as exc:
            emit([commands.ProcessEntry(**exc.data)], error={"code": 1, "message": str(exc)})
            raise typer.Exit(1)
        except (OSError, RuntimeError) as exc:
            fail(str(exc))
    # One sampling implementation, with only applicable options registered by Typer.
    signature = inspect.signature(command)
    allowed = {"once", "watch", "interval", "count"}
    allowed.update({"processes": {"limit", "sort", "pid"},
                    "network": {"interface"}, "disk": {"mount"}}.get(name, set()))
    defaults = {key: parameter.default.default for key, parameter in signature.parameters.items()}

    def entry(ctx: typer.Context, **options):
        if name == "processes" and options.get("pid") is not None:
            if any(ctx.get_parameter_source(key).name != "DEFAULT"
                   for key in ("limit", "sort")):
                fail("--pid cannot be combined with --limit or --sort", 2)
        with commands.sampling_session():
            return command(**{**defaults, **options})

    entry.__signature__ = signature.replace(parameters=[
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=typer.Context),
        *[
        parameter for key, parameter in signature.parameters.items() if key in allowed
        ],
    ])
    entry.__name__ = "monitor_" + name
    return entry
