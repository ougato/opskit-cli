"""Real named-Creator SDK acceptance, isolated from operator configuration/projects."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    # Match the production entry point before Rich binds console streams.
    # Windows redirected stdout otherwise defaults to a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--creator", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--offline", type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    tool = args.creator.resolve(strict=True)
    template = args.template.resolve(strict=True)
    offline = args.offline.resolve(strict=True)
    output.mkdir(exist_ok=False)
    os.environ["OPSKIT_DATA_DIR"] = str(output / "data")
    from core import i18n
    from core import plugin_host_tools
    from core.native_sandbox import GrantChoice
    from core.plugin import load_manifest
    from core.plugin_capabilities import CapabilityAuthority
    from core.plugin_runtime import build
    from core.plugin_sandbox import invoke
    from core.plugin_tool_config import configure_creator
    from core.plugin_trust import compute_fingerprint, grant

    i18n.init(language=args.language)
    configure_creator("acceptance", str(tool))
    project = output / "project"
    shutil.copytree(template, project, symlinks=False)
    plugin = output / "plugin"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "name: generated_creator_probe\nversion: 1.0.0\napi_version: 2\n"
        "kind: python\nentry: creator_probe\n", encoding="utf-8")
    (plugin / "creator_probe.py").write_text(
        "from core.sdk import creator_build, plugin_data_dir, current_lang\n"
        "import json\n"
        "def cli(argv):\n"
        "    result=creator_build(argv[0],argv[1],{'platform':'web-mobile','debug':True})\n"
        "    if not result['success']: raise RuntimeError('Creator failed')\n"
        "    (plugin_data_dir('generated_creator_probe')/'completed.txt').write_text(current_lang())\n"
        "    (plugin_data_dir('generated_creator_probe')/'result.json').write_text(json.dumps(result))\n"
        "    return 0\n", encoding="utf-8")
    build(plugin, [], offline / "python", wheels=offline / "wheels",
          lock_file=offline / "requirements.lock")
    fingerprint = compute_fingerprint(plugin)
    grant("generated_creator_probe", fingerprint, "1.0.0")
    required = CapabilityAuthority.require
    scopes = []

    def authorize(self, scope, **kwargs):
        resource = json.loads(scope.resource)
        if (scope.plugin != "generated_creator_probe" or scope.fingerprint != fingerprint
                or scope.operation != "tool.execute" or resource["operation"] != "creator.build"
                or Path(resource["executable"]) != tool or Path(resource["project"]) != project):
            raise RuntimeError("acceptance refuses unexpected authorization scope")
        scopes.append(scope.resource)
        return required(self, scope, prompt=lambda _: GrantChoice.ONCE, **kwargs)

    CapabilityAuthority.require = authorize
    creator_build = plugin_host_tools.creator_build

    def diagnosed_build(*values, **options):
        try:
            return creator_build(*values, **options)
        except BaseException as error:
            # Retain only exception classes and frame locations, never values
            # that could contain host secrets or vendor response bodies.
            import traceback
            frames = traceback.extract_tb(error.__traceback__)
            (output / "host-failure.json").write_text(json.dumps({
                "exception": type(error).__name__,
                "frames": [{"file": frame.filename, "line": frame.lineno,
                            "function": frame.name} for frame in frames],
            }, indent=2), encoding="utf-8")
            raise

    plugin_host_tools.creator_build = diagnosed_build
    try:
        exit_code = invoke(load_manifest(plugin), "cli", args=["@creator/acceptance", str(project)])
    finally:
        CapabilityAuthority.require = required
        plugin_host_tools.creator_build = creator_build
    state = output / "data/plugin-data/generated_creator_probe/self/state/completed.txt"
    result = json.loads(state.with_name("result.json").read_text())
    artifact = Path(result["artifact"]["directory"]) / "index.html"
    if (exit_code != 0 or result["success"] is not True or result["returncode"] not in {0, 36}
            or state.read_text() != args.language or len(scopes) != 1
            or not artifact.is_file() or artifact.stat().st_size == 0):
        raise RuntimeError("real named Creator acceptance failed")
    (output / "verification.json").write_text(json.dumps({
        "result": result, "formal_sdk_verified": True, "named_configuration": True,
        "language": args.language, "one_time_authorization": True,
        "state_committed": True, "artifact": str(artifact), "frozen_cli_verified": False,
        "fresh_output_inspected": True, "output_summary": result["artifact"],
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
