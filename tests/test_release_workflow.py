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
        expected = {"contents": "write" if name == "release" else "read"}
        if name == "test":
            expected["actions"] = "read"
        assert effective == expected


def test_candidate_branch_builds_and_validates_without_publishing():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow[True]["push"] == {"branches": ["release"], "tags": ["v*"]}
    jobs = workflow["jobs"]
    assert jobs["release"]["if"] == "github.ref_type == 'tag'"
    assert jobs["notify-start"]["steps"][0]["if"] == "github.ref_type == 'tag'"
    candidate = jobs["release-check"]
    assert candidate["if"] == "github.ref_type == 'branch'"
    assert candidate["needs"] == ["test", "build"]
    commands = "\n".join(step.get("run", "") for step in candidate["steps"])
    assert "scripts/release_assets.py" in commands
    assert "--sync-installers --check" in commands
    assert "--publish" not in commands


def test_tag_requires_matching_version_and_successful_release_commit():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["test"]
    version = next(step for step in job["steps"] if step.get("id") == "version")
    assert 'release_bump.py "v$build" --check' in version["run"]
    assert '"$GITHUB_REF_NAME" != "v$build"' in version["run"]
    gate = next(step for step in job["steps"] if step.get("if") == "github.ref_type == 'tag'")
    assert 'git/ref/heads/release' in gate["run"]
    assert '"$release_sha" != "$GITHUB_SHA"' in gate["run"]
    assert '-f status=success -f head_sha="$GITHUB_SHA"' in gate["run"]


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
    assert 'python -I scripts/release_bump.py "${GITHUB_REF_NAME}" --root bootstrap-target' in commands
    assert "git -C bootstrap-target push" in commands
    preflight = commands.index("--preflight")
    assert preflight < commands.index("--check")


def test_release_tags_share_non_cancelling_publish_lock():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    concurrency = workflow["jobs"]["release"]["concurrency"]
    assert concurrency == {"group": "opskit-release-publish", "cancel-in-progress": False}


def test_artifact_validation_precedes_publication():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release"]["steps"]
    validate = next(i for i, step in enumerate(steps) if
                    "scripts/release_assets.py" in step.get("run", "") and
                    "--publish" not in step["run"])
    publish = next(i for i, step in enumerate(steps) if "--publish" in step.get("run", ""))
    assert validate < publish
    assert steps[publish]["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    download = next(step for step in steps if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["pattern"] == "opskit-*"
    assert download["with"].get("merge-multiple", False) is False


def test_all_external_actions_use_immutable_commits():
    for path in WORKFLOW.parent.glob("*.yml"):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                action = step.get("uses")
                if action is not None:
                    assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", action), (path, action)


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


def test_build_setup_does_not_save_disabled_pip_cache():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["build"]["steps"]
    setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
    assert "cache" not in setup.get("with", {})
    installer = WORKFLOW.parents[2] / "scripts/install_release_build.py"
    assert '"--no-cache-dir"' in installer.read_text(encoding="utf-8")


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


def test_test_jobs_use_fresh_environments_before_installing_dependencies():
    release = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    regression = yaml.safe_load((WORKFLOW.parent / "test.yml").read_text(encoding="utf-8"))
    for job in (release["jobs"]["test"], regression["jobs"]["regression"]):
        steps = job["steps"]
        isolation = next(step for step in steps if "venv.EnvBuilder" in step.get("run", ""))
        installer = next(step for step in steps if "requirements-installer.txt" in step.get("run", ""))
        assert steps.index(isolation) < steps.index(installer)
        assert isolation["shell"] == "python"
        assert "with_pip=True" in isolation["run"]
        assert "system_site_packages=True" not in isolation["run"]
        assert "GITHUB_PATH" in isolation["run"]
        assert "RUNNER_TEMP" in isolation["run"]


def test_secret_scan_exception_is_limited_to_reviewed_historical_fixture():
    root = WORKFLOW.parents[2]
    exceptions = [line for line in (root / ".gitleaksignore").read_text().splitlines()
                  if line and not line.startswith("#")]
    assert exceptions == [
        "3e80a4ea522de862e8fb2f2df72c315a68b1f4b2:tests/test_plugin_fetch_source.py:generic-api-key:160",
        "a25fa93adf14bb3365e2ff557cbeec512d859fc9:tests/test_plugin_fetch_source.py:generic-api-key:160",
        "67125d26312450173da0be1f86aa7f6762b2f43f:tests/test_plugin_fetch_source.py:generic-api-key:160",
        "0506b194acf315dc488dc596cb4bb2d1df980263:tests/test_plugin_fetch_source.py:generic-api-key:161",
    ]
    workflow = yaml.safe_load((WORKFLOW.parent / "test.yml").read_text(encoding="utf-8"))
    scan = next(step["run"] for step in workflow["jobs"]["secrets"]["steps"]
                if "--log-opts" in step.get("run", ""))
    assert "--gitleaks-ignore-path .gitleaksignore" in scan
    assert "--ignore-gitleaks-allow" in scan
    assert "--all -m --full-history" in scan


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
    diagnostic = next(step for step in steps if step.get("name") == "save frozen CLI diagnostics")
    assert diagnostic["if"] == "always()"
    assert diagnostic["with"]["name"] == "frozen-check-${{ matrix.artifact }}"
    assert all(suffix in diagnostic["with"]["path"] for suffix in ("*.stdout", "*.stderr", "*.status.json"))
    assert "if" not in steps[upload]  # Broken executables never become release assets.
