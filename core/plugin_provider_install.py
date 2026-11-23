"""Host-owned, interactive installation and authorization of declared providers."""

from contextlib import nullcontext
from pathlib import Path

from rich.markup import escape

from core import plugin_services as services
from core.constants import PLUGIN_API_VERSION
from core.i18n import t
from core.plugin_capabilities import CapabilityDenied

# Only host-controlled sources can be invoked by a sandboxed plugin.
_PROVIDERS = {
    "storage_hub": ("git@git.icerror.top:mydea/opskit/plugin/storage-hub.git", "master", (0, 3, 1)),
}


def provider_update_branch(provider, source, branch):
    if provider == "storage_hub" and branch in ("develop", "release"):
        from core.native_acquisition import source_identity

        official_source, master, _ = _PROVIDERS[provider]
        if source == source_identity(official_source)[0]:
            return master
    return branch


def _compatible(manifest, provider, requirement, minimum):
    from plugin.commands import semver_key
    contract = manifest.service_contracts.get(requirement["service"], {})
    version = semver_key(manifest.version)
    return (manifest.name == provider and manifest.api_version == PLUGIN_API_VERSION
            and "root" not in manifest.permissions
            and version is not None and version >= minimum
            and contract.get("api_version") == requirement["api_version"]
            and set(requirement["methods"]).issubset(contract.get("methods", ())))


def _interactive():
    from core.cli import non_interactive
    from core.prompt import console
    if non_interactive.get() or not console.is_terminal:
        raise CapabilityDenied("provider_interaction_required")


def request_install(caller, fingerprint, provider, *, checkpoint, suspend_wait=nullcontext):
    from core.config import configuration_transaction
    from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
    from core.plugin_sandbox import SandboxError
    from core.plugin_trust import authorization_transaction, compute_fingerprint, invalidate, is_trusted
    from core.prompt import confirm
    from plugin import commands, menu

    requirements = [item for item in caller.uses if item["provider"] == provider]
    if len(requirements) != 1 or provider not in _PROVIDERS or caller.name == provider:
        raise CapabilityDenied("provider_request_denied")
    requirement = requirements[0]
    service = requirement["service"]
    source, branch, minimum = _PROVIDERS[provider]
    installed = services._selected_manifests("name", provider)
    providers = services._selected_manifests("service", service)
    if len(installed) > 1 or any(item.name != provider for item in providers):
        raise CapabilityDenied("provider_incompatible")

    def review(candidate, digest, *, installing=False):
        checkpoint()
        if not _compatible(candidate, provider, requirement, minimum) or not digest:
            raise CapabilityDenied("provider_incompatible")
        if verify_checksums(Path(candidate.root)) == CHECK_MISMATCH:
            raise CapabilityDenied("provider_incompatible")
        lines = menu._summary_lines(candidate)
        if installing:
            lines.extend([f"{t('plugin.col_source')}: {escape(source)}",
                          f"{t('plugin.col_branch')}: {escape(branch)}"])
        lines.extend([
            f"{t('plugin.col_fingerprint')}: {digest}",
            t("plugin_native.provider.scope", caller=escape(caller.name), provider=escape(provider),
              service=escape(service), methods=escape(", ".join(requirement["methods"]))),
        ])
        if not is_trusted(provider, digest):
            lines.append(t("plugin_native.provider.trust"))
        with suspend_wait():
            return confirm(breadcrumb=["OpsKit", t("menu.plugin")],
                       prompt=t("plugin_native.provider.install" if installing else "plugin_native.provider.approve"),
                       info_lines=lines, require_explicit=True)

    if installed:
        candidate, = installed
        if not _compatible(candidate, provider, requirement, minimum) or not commands.is_enabled(provider):
            raise CapabilityDenied("provider_incompatible")
        try:
            services.resolve_service_access(candidate, service, "", caller.name)
        except SandboxError:
            _interactive()
        else:
            return True
        digest = compute_fingerprint(candidate.root)
        if not review(candidate, digest):
            return False
    else:
        _interactive()
        if not commands.is_enabled(provider):
            raise CapabilityDenied("provider_incompatible")
        from core.installation_progress import InstallationProgress
        from core.prompt import UserCancel

        with suspend_wait(), InstallationProgress() as progress:
            options = menu._fetch_options(source)
            if "fetch_interaction" in options:
                options["fetch_interaction"] = progress.interaction(options["fetch_interaction"])

            approved_digest = None

            def review_install(candidate, digest):
                nonlocal approved_digest
                with progress.suspend():
                    if not review(candidate, digest, installing=True):
                        return False
                approved_digest = digest
                return True

            try:
                candidate, error = commands.install(source, branch, automatic=True,
                                                    progress=progress, review_candidate=review_install, **options)
            except UserCancel:
                return False
        if candidate is None:
            if error == "declined":
                return False
            raise CapabilityDenied("provider_install_failed")
        digest = approved_digest

    with configuration_transaction(), authorization_transaction():
        checkpoint()
        if compute_fingerprint(candidate.root) != digest or not commands.is_enabled(provider):
            raise CapabilityDenied("provider_incompatible")
        newly_trusted = not is_trusted(provider, digest)
        if newly_trusted:
            commands.grant_trust(candidate, expected_fingerprint=digest)
        try:
            services.authorize_service(caller.name, service, fingerprint, digest)
        except BaseException:
            if newly_trusted:
                invalidate(provider)
            raise
    return True
