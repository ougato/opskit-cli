"""Shared official CLI output and interaction contract."""
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
import json
from functools import wraps
import subprocess
import sys

import typer
from typer.core import TyperGroup
try:
    from typer._click.exceptions import ClickException as _ParserError
except ImportError:
    from click import ClickException as _ParserError


class ContractGroup(TyperGroup):
    """Render parser failures as JSON only when the root requests JSON output."""

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True,
             windows_expand_args=True, **extra):
        arguments = list(sys.argv[1:] if args is None else args)
        mode = "text"
        index = 0
        while index < len(arguments):
            value = arguments[index]
            flag = value.lower()
            if not flag.startswith("-") or flag == "--":
                break  # Plugin business options are never root options.
            if flag.startswith("--output="):
                mode = value.split("=", 1)[1]
            elif flag in {"--output", "--theme", "-t", "--lang", "-l"}:
                index += 1
                if index < len(arguments) and flag == "--output":
                    mode = arguments[index]
            index += 1
        if mode != "json":
            return super().main(args=args, prog_name=prog_name, complete_var=complete_var,
                                standalone_mode=standalone_mode,
                                windows_expand_args=windows_expand_args, **extra)
        configure("json", True)
        try:
            result = super().main(args=args, prog_name=prog_name, complete_var=complete_var,
                                  standalone_mode=False, windows_expand_args=windows_expand_args, **extra)
        except _ParserError as exc:
            # Parser messages can echo sensitive argument values. Keep them out.
            emit(error={"code": exc.exit_code, "message": "invalid CLI parameters; use --help"})
            result = exc.exit_code
        except typer.Abort:
            emit(error={"code": 1, "message": "operation aborted"})
            result = 1
        except Exception as exc:
            emit(error={"code": 1, "message": f"unexpected CLI failure: {type(exc).__name__}"})
            result = 1
        if standalone_mode:
            raise SystemExit(result if isinstance(result, int) else 0)
        return result

output_mode = ContextVar("cli_output", default="text")
non_interactive = ContextVar("cli_non_interactive", default=False)
_result_stream = ContextVar("cli_result_stream", default=None)


def configure(output: str, noninteractive: bool) -> None:
    if output not in {"text", "json"}:
        raise typer.BadParameter("output must be text or json")
    output_mode.set(output)
    non_interactive.set(noninteractive or output == "json")
    _result_stream.set(sys.stdout)


def emit(data=None, *, error=None) -> None:
    payload = {"schema_version": 1, "ok": error is None, "data": data, "error": error}
    def encode(value):
        if is_dataclass(value):
            return asdict(value)
        raise TypeError(f"unsupported CLI value: {type(value).__name__}")
    if output_mode.get() == "json":
        typer.echo(json.dumps(payload, default=encode, ensure_ascii=False), file=_result_stream.get())
    else:
        from pprint import pformat
        typer.echo(pformat(asdict(data) if is_dataclass(data) else data) if error is None else error["message"], err=error is not None)


def fail(message: str, code: int = 1) -> None:
    emit(error={"code": code, "message": message})
    raise typer.Exit(code)


def emit_diagnostics(report: dict) -> None:
    """Keep check results on failure while matching envelope and exit status."""
    checks = report.get("checks", [])
    if not checks or not all(item.get("ok") is True for item in checks):
        emit(report, error={"code": 1, "message": "diagnostic checks failed"})
        raise typer.Exit(1)
    emit(report)


def require_interaction() -> None:
    if non_interactive.get():
        fail("This operation requires interactive input; supply its business parameters", 2)


def guarded(function):
    """Translate operation failures without intercepting intended CLI exits."""
    @wraps(function)
    def execute(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except typer.Exit:
            raise
        except KeyboardInterrupt:
            raise typer.Exit(130)
        except ValueError as exc:
            fail(str(exc), 2)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            fail(str(exc))
    return execute
