"""Release security boundaries; these checks never dispatch a workflow."""
from pathlib import Path
import re

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


def test_only_release_job_can_write_repository():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    for name, job in workflow["jobs"].items():
        effective = job.get("permissions", workflow["permissions"])
        assert effective == {"contents": "write" if name == "release" else "read"}


def test_signer_comes_from_event_commit_without_persisted_credentials():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release"]["steps"]
    signer = next(i for i, step in enumerate(steps) if
                  "OPSKIT_RELEASE_SIGNING_KEY" in step.get("env", {}))
    checkouts = [step for step in steps[:signer] if
                 step.get("uses", "").startswith("actions/checkout@")]
    assert checkouts[-1]["with"]["ref"] == "${{ github.sha }}"
    assert checkouts[-1]["with"]["persist-credentials"] is False
    assert sum("OPSKIT_RELEASE_SIGNING_KEY" in step.get("env", {})
               for step in steps) == 1
    assert "OPSKIT_RELEASE_SIGNING_KEY" not in workflow.get("env", {})
    assert "OPSKIT_RELEASE_SIGNING_KEY" not in workflow["jobs"]["release"].get("env", {})


def test_bootstrap_commit_uses_default_branch_after_release_upload():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release"]["steps"]
    upload = next(i for i, step in enumerate(steps) if
                  "--publish" in step.get("run", ""))
    publish = next(i for i, step in enumerate(steps) if
                   "push origin HEAD:" in step.get("run", ""))
    checkouts = [(i, step) for i, step in enumerate(steps[:publish]) if
                 step.get("uses", "").startswith("actions/checkout@")]
    index, checkout = checkouts[-1]
    assert upload < index < publish
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["path"] == "bootstrap-target"
    commands = steps[publish]["run"]
    assert "python -I scripts/pin_release_key.py --root bootstrap-target" in commands
    assert 'python -I scripts/release_bump.py "${GITHUB_REF_NAME}" --root bootstrap-target' in commands
    assert "git -C bootstrap-target push" in commands
    preflight = commands.index("--preflight")
    assert preflight < commands.index("scripts/pin_release_key.py")


def test_release_tags_share_non_cancelling_publish_lock():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    concurrency = workflow["jobs"]["release"]["concurrency"]
    assert concurrency == {"group": "opskit-release-publish", "cancel-in-progress": False}


def test_artifact_validation_precedes_signing_and_publication():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release"]["steps"]
    validate = next(i for i, step in enumerate(steps) if
                    "scripts/release_assets.py" in step.get("run", "") and
                    "--publish" not in step["run"])
    signer = next(i for i, step in enumerate(steps) if
                  "OPSKIT_RELEASE_SIGNING_KEY" in step.get("env", {}))
    publish = next(i for i, step in enumerate(steps) if "--publish" in step.get("run", ""))
    assert validate < signer < publish
    assert "pip install" not in steps[signer]["run"]
    assert "OPSKIT_RELEASE_PUBLIC_KEY" in steps[publish]["env"]


def test_all_external_actions_use_immutable_commits():
    for path in WORKFLOW.parent.glob("*.yml"):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                action = step.get("uses")
                if action is not None:
                    assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", action), (path, action)


def test_signing_uses_isolated_hash_locked_environment():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release"]["steps"]
    install = next(step for step in steps if step.get("name") == "install release verification dependency")
    assert "-I -m venv" in install["run"]
    assert "--isolated install" in install["run"]
    for option in ("--require-hashes", "--only-binary=:all:", "--no-deps", "requirements-release-signing.txt"):
        assert option in install["run"]
    assert "OPSKIT_RELEASE_SIGNING_KEY" not in install.get("env", {})
    assert install["run"].index("requirements-installer.txt") < install["run"].index("requirements-release-signing.txt")
    signer = next(step for step in steps if "OPSKIT_RELEASE_SIGNING_KEY" in step.get("env", {}))
    assert '"$RUNNER_TEMP/opskit-signing/bin/python" -I scripts/sign_release.py' in signer["run"]


def test_manylinux_builder_is_pinned_to_amd64_digest():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["build"]["steps"]
    command = next(step["run"] for step in steps if "docker run" in step.get("run", ""))
    assert "--platform linux/amd64" in command
    images = re.findall(r"quay\.io/pypa/manylinux2014_x86_64\S*", command)
    assert len(images) == 1
    assert re.fullmatch(r"quay\.io/pypa/manylinux2014_x86_64@sha256:[0-9a-f]{64}", images[0])
    # SCons' default environment searches /usr/local/bin, not the devtoolset PATH.
    for name in ("gcc", "g++"):
        assert f"ln -s /opt/rh/devtoolset-10/root/usr/bin/{name} /usr/local/bin/{name}" in command


def test_read_only_checkouts_do_not_leave_tokens_for_tests_or_builds():
    for path in WORKFLOW.parent.glob("*.yml"):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("actions/checkout@"):
                    options = step.get("with", {})
                    if options.get("path") != "bootstrap-target":
                        assert options.get("persist-credentials") is False, (path, job.get("name"))


def test_ci_dependency_installations_cannot_resolve_unlocked_packages():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    regression = yaml.safe_load((WORKFLOW.parent / "test.yml").read_text(encoding="utf-8"))
    release_install = next(s["run"] for s in workflow["jobs"]["test"]["steps"] if s.get("name") == "install deps")
    steps = regression["jobs"]["regression"]["steps"]
    install = next(s["run"] for s in steps if s.get("name") == "Install project and test dependencies")
    release_steps = workflow["jobs"]["test"]["steps"]
    for sequence, command in ((release_steps, release_install), (steps, install)):
        bootstrap_step = next(s for s in sequence if "requirements-installer.txt" in s.get("run", ""))
        dependency_step = next(s for s in sequence if "requirements-ci.txt" in s.get("run", ""))
        assert sequence.index(bootstrap_step) < sequence.index(dependency_step)
        # A separate step propagates native command failure on Windows too;
        # later successful pip commands cannot mask the failed bootstrap.
        bootstrap = bootstrap_step["run"]
        assert len(bootstrap.splitlines()) == 1
        for option in ("--require-hashes", "--only-binary=:all:", "--no-deps", "--disable-pip-version-check"):
            assert option in bootstrap
        for option in ("--require-hashes", "--only-binary=:all:", "--no-deps", "requirements-ci.txt"):
            assert option in command
    assert "--no-deps --no-build-isolation -e ." in install
    build = next(s["run"] for s in steps if s.get("name") == "Build distributable wheel")
    assert "--no-isolation" in build
    wheel = next(s["run"] for s in steps if s.get("name") == "Install wheel in independent environment")
    assert "requirements-runtime.txt" in wheel and "--require-hashes" in wheel
    assert wheel.index("requirements-installer.txt") < wheel.index("requirements-runtime.txt")
    assert "'--no-deps', str(next(" in wheel


def test_frozen_artifact_gate_precedes_upload():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["build"]["steps"]
    gate = next(i for i, step in enumerate(steps) if step.get("name") == "verify frozen CLI outside source")
    upload = next(i for i, step in enumerate(steps) if step.get("name") == "upload artifact")
    assert gate < upload
    command = steps[gate]["run"]
    assert "scripts/verify_frozen_cli.py" in command
    assert "subprocess.check_call" in command
    assert '"${{ matrix.artifact }}"' in command
    assert '"--build"' in command and '"--output"' in command
