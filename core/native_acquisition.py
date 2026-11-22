"""Reviewed native acquisition into an unpublished lifecycle-owned payload."""
from dataclasses import dataclass
from contextlib import ExitStack
from pathlib import Path
import re
import time

from core.native_sandbox import NativeSandboxError, get_backend


@dataclass(frozen=True)
class Acquisition:
    source: str
    commit: str
    fingerprint: str
    receipt: str


def source_identity(value):
    """Canonical source to persist for updates; inline remote secrets are invalid."""
    if (not isinstance(value, str) or not value or len(value.encode()) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid acquisition source")
    windows_path = re.match(r"^[A-Za-z]:[\\/]", value) is not None
    if not windows_path and ("://" in value or re.match(r"^[^/\\@]+@[^/\\:]+:", value)):
        from core.plugin_fetch_source import remote_source
        return remote_source(value).url, False
    return str(Path(value).absolute()), True


def populate(source, destination, *, branch=None, timeout=300, fetch_profile=None, allow_private=False,
             fetch_interaction=None, copy_progress=None):
    """No host Git fallback, imports, grant or discoverable publication.

    Fetch, independent archive validation, private writes and manifest review all
    finish before copying the authenticated payload into lifecycle-owned staging.
    """
    if type(timeout) not in (int, float) or not 0 < timeout < float("inf"):
        raise ValueError("invalid acquisition deadline")
    deadline = time.monotonic() + timeout
    get_backend().probe()
    canonical, local = source_identity(source)
    if type(allow_private) is not bool or local and (fetch_profile is not None or allow_private):
        raise ValueError("invalid remote acquisition options")
    from core.native_fetch_catalog import FetchCatalog
    from core.native_runtime import stage
    from core.paths import data_dir
    from core.plugin_fetch_prepare import prepare_candidate
    from core.plugin_fetch_review import review_candidate, _manifest_digest
    from core.plugin_workspace import owned_workspace
    from core.plugin import load_manifest
    from core.plugin_trust import compute_fingerprint
    from core.plugin_install_receipt import content_receipt
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise NativeSandboxError("execution_timeout")
        return value
    parent = data_dir() / "native-fetch-staging"
    parent.mkdir(parents=True, exist_ok=True)
    catalog = FetchCatalog()
    # Give missing preparation an actionable fixed diagnostic, without exposing
    # paths or lower-level tool/process exceptions.
    try:
        catalog.load()
    except FileNotFoundError:
        raise NativeSandboxError("fetch_tools_missing") from None
    except (OSError, ValueError, RuntimeError):
        raise NativeSandboxError("fetch_tools_invalid") from None
    with ExitStack() as stack:
        candidate_source, selected_branch, pack = Path(canonical) if local else None, branch, None
        if not local:
            from core.native_git_https import remote_pack
            from core.plugin_fetch_profile import load_profile
            try:
                profile = None if fetch_profile is None else load_profile(canonical, fetch_profile, timeout=remaining())
            except (OSError, ValueError, RuntimeError):
                raise NativeSandboxError("fetch_profile_invalid") from None
            prompted = set()
            while True:
                try:
                    candidate_source, pack = stack.enter_context(remote_pack(
                        canonical, profile=profile, allow_private=allow_private, branch=branch,
                        staging_parent=parent, deadline=deadline, checkpoint=remaining))
                    break
                except NativeSandboxError as error:
                    code = error.code
                    category = "private" if code == "fetch_private_denied" else "credentials"
                    if (fetch_interaction is None or category in prompted or code not in {
                            "fetch_private_denied", "fetch_ssh_auth_failed", "fetch_ssh_host_failed", "fetch_http_auth_failed"}):
                        raise
                    prompted.add(category)
                    answer = fetch_interaction(canonical, code)
                    remaining()
                    if category == "private":
                        if answer is not True:
                            raise
                        allow_private = True
                    else:
                        if not answer:
                            raise
                        try:
                            profile = load_profile(canonical, answer, timeout=remaining())
                        except (OSError, ValueError, RuntimeError):
                            raise NativeSandboxError("fetch_profile_invalid") from None
            selected_branch = None
        inventory, response = stack.enter_context(catalog.local_candidate(
            candidate_source, staging_parent=parent, deadline=deadline,
            checkpoint=remaining, branch=selected_branch, pack=pack))
        with owned_workspace(timeout=remaining(), cleanup_timeout=30) as workspace:
            prepared = prepare_candidate(response, workspace, timeout=remaining())
            review = review_candidate(response, workspace, prepared, timeout=remaining())
            expected = {item.path: item.sha256 for item in inventory.files}
            stage(prepared.root, destination, expected, copy_progress=copy_progress)
            remaining()
            manifest = load_manifest(destination)
            if (manifest is None or _manifest_digest(manifest) != review.manifest_sha256
                    or compute_fingerprint(destination) != review.fingerprint
                    or content_receipt(destination) != review.content_receipt):
                raise ValueError("acquisition publication binding changed")
            remaining()
            result = Acquisition(canonical, prepared.reported_commit, review.fingerprint, review.content_receipt)
    remaining()
    return result
