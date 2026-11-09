"""Orchestrate initial configuration, preflight and pending-update handling.

The entry module supplies its callbacks so installed, direct-script and frozen
entry points retain the same runtime bindings without importing ``main`` here.
"""
from __future__ import annotations

import sys

import click


def boot(host, *, post_update: bool | None, compiled: bool) -> dict:
    """Initialize startup services and apply eligible pending updates.

    Network background jobs start separately after the first menu is rendered.
    """
    ctx = host._typer_current_context(silent=True) or click.get_current_context(silent=True)
    root_params = ctx.find_root().params if ctx else {}
    if post_update is None:
        post_update = bool(root_params.get("post_update", False))

    def prepare_error_locale(config=None):
        from core.i18n import _detect_system_lang
        requested = root_params.get("lang")
        language = requested or (config.get("language") if config else None)
        if language not in ("zh", "en"):
            language = _detect_system_lang()
        host.i18n_init(language=language)

    from core.config import StartupConfigPublicationError
    from core.resource_lock import ResourceBusyError, ResourceIdentityError
    try:
        cfg = host.ensure_config()
    except ResourceBusyError:
        prepare_error_locale()
        raise host._StartupClickException(host.t("error.config_busy")) from None
    except ResourceIdentityError:
        prepare_error_locale()
        raise host._StartupClickException(host.t("error.config_lock_identity")) from None
    except StartupConfigPublicationError:
        prepare_error_locale()
        raise host._StartupClickException(host.t("error.config_publication")) from None
    except ValueError:
        prepare_error_locale()
        raise host._StartupClickException(host.t("error.config_unreadable")) from None
    except OSError:
        prepare_error_locale()
        raise host._StartupClickException(host.t("error.config_io")) from None

    from core.config import StartupConfigError, validate_startup_config
    try:
        validate_startup_config(cfg)
    except StartupConfigError as exc:
        prepare_error_locale(cfg)
        raise host._StartupClickException(host.t(
            "error.config_field", field=exc.field,
            expected=host.t(f"error.config_expected_{exc.expected}"),
        )) from None

    import core.logger as logger
    logger.init(cfg.get("log", {}).get("level", "WARNING"))

    import core.cleanup as cleanup
    cleanup.init()
    host.theme_init()
    host.i18n_init()

    from core.platform import preflight_check
    from core.theme import print_warning
    from core.prompt import pause
    shown = False
    for issue in preflight_check():
        if issue.code == "non_root":
            logger.warning("%s（%s）", issue.message, issue.suggestion)
            continue
        print_warning(str(issue.message))
        if issue.suggestion:
            host.console.print(f"  [dim]↳ {issue.suggestion}[/dim]")
        shown = True
    if shown:
        pause()

    try:
        from core.updater import check_and_apply_pending, pending_version
        if check_and_apply_pending(post_update=post_update):
            version = pending_version()
            host.print_info(host.t("update.applying", version=f"v{version}" if version else ""))
            if getattr(sys, "frozen", False) or compiled:
                import os
                arguments = host._restart_arguments(sys.argv[1:])
                os.execv(sys.executable, [sys.executable, "--post-update", *arguments])
    except Exception as exc:
        host._log_startup_failure("pending_update", exc)

    return cfg
