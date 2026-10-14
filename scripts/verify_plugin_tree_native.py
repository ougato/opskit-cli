"""Verify guest state discovery limits in an immutable local SDK image."""
import argparse
import json
import os
from pathlib import Path
import re
import uuid

from core import plugin_resources, plugin_sandbox as sandbox

PROBE = """
import base64,io,json,sys,tarfile
from pathlib import Path
from core.plugin_worker import _pack_state
mode=sys.argv[1]
root=Path('/data')
if mode=='entries':
    for i in range(4097): (root/str(i)).mkdir()
elif mode=='depth':
    path=root
    for i in range(65):
        path=path/'d'
        path.mkdir()
    (path/'file').write_text('x')
elif mode=='link':
    (root/'link').symlink_to('/etc/passwd')
elif mode=='preserve':
    (root/'.git').mkdir()
    (root/'.git/data').write_bytes(b'user state')
try:
    payload=_pack_state()
except ValueError as error:
    if mode=='preserve': raise
    message=str(error)
    if ('tree limits' if mode in {'entries','depth'} else 'links forbidden') not in message: raise
    print(json.dumps({'mode':mode,'rejected':True}))
else:
    if mode!='preserve': raise RuntimeError('unsafe tree accepted')
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload))) as archive:
        assert archive.extractfile('.git/data').read()==b'user state'
    print(json.dumps({'mode':mode,'preserved':True}))
"""


def verify(image, output):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("immutable local image required")
    output.mkdir(parents=True, exist_ok=False)
    previous = os.environ.get("OPSKIT_DATA_DIR")
    os.environ["OPSKIT_DATA_DIR"] = str(output / "platform-state")
    try:
        command = sandbox.docker_command()
        sandbox._control(command, "image", "inspect", image)
        cases, workers = [], []
        for mode in ("entries", "depth", "link", "preserve"):
            name = "opskit-" + uuid.uuid4().hex
            with sandbox._resource_scope(command, "container", name) as labels:
                result = sandbox._control(command, "run", "--rm", "--name", name,
                    "--pull=never", *labels, "--network=none", "--read-only", "--user=65534:65534",
                    "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=32",
                    "--memory=128m", "--memory-swap=128m", "--cpus=1", "--log-driver=none",
                    "--tmpfs", "/data:rw,nosuid,nodev,noexec,size=32m,uid=65534,gid=65534",
                    image, "python", "-c", PROBE, mode)
                cases.append(json.loads(result))
            remaining = sandbox._control(command, "ps", "-a", "--filter", "name=" + name,
                                         "--format", "{{.Names}}")
            if name in remaining.splitlines():
                raise RuntimeError("owned state probe remains")
            workers.append(name)
        recovery = plugin_resources.reconcile(command, lambda *args: sandbox._cleanup_control(command, *args))
        if any(recovery.values()):
            raise RuntimeError("state probe resource recovery incomplete")
        report = {"image": image, "cases": cases, "removed_workers": workers, "recovery": recovery,
                  "scope": "guest packing entries/depth/links and cache-named data; not host copy race or hard I/O deadlines"}
        (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        if previous is None:
            os.environ.pop("OPSKIT_DATA_DIR", None)
        else:
            os.environ["OPSKIT_DATA_DIR"] = previous


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.image, args.output.resolve())))
