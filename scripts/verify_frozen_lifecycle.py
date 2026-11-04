"""Frozen local Git install/update/run/remove acceptance in fresh test directories."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from contextlib import ExitStack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "output", "offline", "git"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    parser.add_argument("--transport", choices=("local", "https", "ssh"), default="local")
    args = parser.parse_args()
    binary, offline, git = (getattr(args, name).resolve(strict=True)
                            for name in ("binary", "offline", "git"))
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["OPSKIT_DATA_DIR"] = str(output / "data")
    from core.config import set_config_value
    from core.plugin_trust import compute_fingerprint
    set_config_value({}, "language", args.language)
    plugins = output / "plugins"
    environment = dict(os.environ, OPSKIT_PLUGINS_DIR=str(plugins), PYTHONIOENCODING="utf-8")
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(key, None)
    private_git = output / "git-runtime"
    private_git.mkdir()
    vendor = git.parents[1] / "mingw64/bin"
    if not (vendor / "git.exe").is_file():
        raise RuntimeError("Git for Windows mingw64 input required")
    for path in vendor.iterdir():
        if path.name == "git.exe" or path.suffix.lower() == ".dll":
            shutil.copyfile(path, private_git / path.name)
    repository = output / "repository"
    repository.mkdir()
    def host_git(*values):
        return subprocess.run([str(git), "-C", str(repository), "-c", "user.name=OpsKit Fixture",
            "-c", "user.email=fixture@test.invalid", "-c", "commit.gpgSign=false", *values],
            check=True, capture_output=True, timeout=30)
    host_git("init", "--initial-branch=main")
    def commit(version):
        (repository / "plugin.yaml").write_text(
            "name: lifecycle_probe\nversion: " + version + "\napi_version: 3\nkind: python\nentry: lifecycle_probe\n",
            encoding="utf-8")
        (repository / "lifecycle_probe.py").write_text(
            "from core.sdk import plugin_data_dir,current_lang\n"
            "def cli(argv):\n"
            "    (plugin_data_dir('lifecycle_probe')/'version.txt').write_text('" + version + "|'+current_lang())\n"
            "    return 0\n", encoding="utf-8")
        host_git("add", ".")
        host_git("commit", "-m", "synthetic " + version)
    commit("1.0.0")
    resources = ExitStack()
    try:
        source, options = str(repository), []
        requests = []
        if args.transport != "local":
            from frozen_git_fixture import https_repository, ssh_repository
            from core.cli_secrets import export_secret
            source, profile, requests, server_mode = resources.enter_context(
                (https_repository if args.transport == "https" else ssh_repository)(output, repository, git))
            profile_path = output / "fetch-profile.json"
            export_secret(str(profile_path), profile)
            options = ["--fetch-profile", str(profile_path), "--allow-private"]
        records = []
        def run(label, arguments, success=True):
            result = subprocess.run([str(binary), "--non-interactive", "--yes", "--output", "json",
                                     "plugin", *arguments], cwd=output, env=environment,
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=900)
            (output / (label + ".stdout")).write_bytes(result.stdout)
            (output / (label + ".stderr")).write_bytes(result.stderr)
            records.append({"step": label, "exit_code": result.returncode})
            if (result.returncode == 0) != success:
                raise RuntimeError("frozen lifecycle failed at " + label)
            # Plugin business CLI output is intentionally not a lifecycle envelope.
            return json.loads(result.stdout) if success and arguments[0] != "run" else None
        run("prepare-fetch", ["prepare-fetch", "--python-runtime", str(offline / "python"),
                              "--git-runtime", str(private_git)])
        if args.transport == "ssh":
            import copy
            import paramiko
            run("private-default-denied", ["install", source, "--fetch-profile", str(profile_path)], False)
            if requests:
                raise RuntimeError("private SSH denial contacted authentication")
            wrong = copy.deepcopy(profile)
            other = paramiko.RSAKey.generate(2048)
            wrong["authentication"]["host_keys"] = [other.get_name() + " " + other.get_base64()]
            wrong_path = output / "wrong-host-profile.json"
            export_secret(str(wrong_path), wrong)
            run("wrong-host-denied", ["install", source, "--fetch-profile", str(wrong_path), "--allow-private"], False)
            if requests:
                raise RuntimeError("private key offered before host verification")
            wrong = copy.deepcopy(profile)
            wrong["authentication"]["passphrase"] = "incorrect"
            wrong_path = output / "wrong-passphrase-profile.json"
            export_secret(str(wrong_path), wrong)
            run("wrong-passphrase-denied", ["install", source, "--fetch-profile", str(wrong_path), "--allow-private"], False)
            if requests:
                raise RuntimeError("invalid encrypted key reached authentication")
        if args.transport == "https":
            run("private-default-denied", ["install", source, "--fetch-profile", str(profile_path)], False)
            if requests:
                raise RuntimeError("private denial contacted server")
            run("untrusted-certificate-denied", ["install", source, "--allow-private"], False)
            if requests:
                raise RuntimeError("untrusted TLS reached HTTP handler")
            server_mode["redirect"] = True
            run("redirect-denied", ["install", source, *options], False)
            if len(requests) != 1 or requests[0]["path"] == "/must-not-follow":
                raise RuntimeError("redirect followed or fixture not reached")
            server_mode["redirect"] = False
        installed = run("install", ["install", source, "--branch", "main", *options])["data"]
        if installed["name"] != "lifecycle_probe" or installed["version"] != "1.0.0":
            raise RuntimeError("installed identity mismatch")
        root = plugins / "lifecycle_probe"
        # Local acquisition may select a repository directory name; discover by manifest.
        matches = list(plugins.glob("*/plugin.yaml"))
        if len(matches) != 1:
            raise RuntimeError("unexpected installed plugin inventory")
        root = matches[0].parent
        def prepare_and_trust(label):
            run(label + "-prepare", ["prepare", str(root), "--python-runtime", str(offline / "python"),
                "--wheels", str(offline / "wheels"), "--lock-file", str(offline / "requirements.lock")])
            run(label + "-trust", ["trust", "lifecycle_probe", "--fingerprint", compute_fingerprint(root)])
        prepare_and_trust("v1")
        run("run-v1", ["run", "lifecycle_probe"])
        state = output / "data/plugin-data/lifecycle_probe/self/state/version.txt"
        if state.read_text() != "1.0.0|" + args.language:
            raise RuntimeError("v1 state mismatch")
        commit("1.1.0")
        if args.transport != "local":
            count = len(requests)
            run("no-ambient-profile", ["update", "lifecycle_probe", "--allow-private"], False)
            if len(requests) != count:
                raise RuntimeError("update reused ambient profile or trusted CA")
        run("update", ["update", "lifecycle_probe", *options])
        run("untrusted-update-denied", ["run", "lifecycle_probe"], False)
        if state.read_text() != "1.0.0|" + args.language:
            raise RuntimeError("untrusted update changed state")
        prepare_and_trust("v2")
        run("run-v2", ["run", "lifecycle_probe"])
        if state.read_text() != "1.1.0|" + args.language:
            raise RuntimeError("v2 state mismatch")
        run("remove", ["remove", "lifecycle_probe"])
        if root.exists() or state.read_text() != "1.1.0|" + args.language:
            raise RuntimeError("removal/state preservation mismatch")
        report = {
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "language": args.language,
            "local_git_lifecycle_verified": args.transport == "local", "host_git_used_only_for_fixture": True,
            "transport": args.transport, "https_lifecycle_verified": args.transport == "https",
            "ssh_lifecycle_verified": args.transport == "ssh",
            "server_requests": requests,
            "update_requires_retrust": True, "state_preserved_on_remove": True,
            "remote_https_ssh_verified": False, "steps": records,
        }
    finally:
        resources.close()
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
