"""Collect target interpreter facts inside the native isolation backend."""
from pathlib import Path
import sys
import tempfile
import time

from core.native_runtime import inventory, stage, absolute_path
from core.native_process import open_worker
from core.native_protocol import exchange
from core.native_sandbox import get_backend


def probe(source, *, staging_parent):
    import packaging
    backend = get_backend()
    with tempfile.TemporaryDirectory(prefix="native-probe-", dir=absolute_path(staging_parent)) as temporary:
        root = absolute_path(temporary)
        stage(source, root / "python", inventory(source))
        package = (Path(__file__).parent / "native-probe-packaging" / "packaging"
                   if getattr(sys, "frozen", False) or "__compiled__" in globals()
                   else Path(packaging.__file__).parent)
        (root / "libraries").mkdir()
        stage(package, root / "libraries" / "packaging", inventory(package))
        code = ("import sys,json,hashlib,tarfile,tempfile,unicodedata,subprocess,threading,queue,"
                "ssl,socket,ctypes,bz2,lzma,zlib; sys.path.insert(0," + repr(str(root / "libraries")) + "); "
                "from packaging.tags import sys_tags; from packaging.markers import default_environment; "
                "print('OPSKIT_RPC:'+json.dumps({'rpc_version':1,'type':'result','state':'',"
                "'value':{'tags':[str(tag) for tag in sys_tags()], 'markers':default_environment()}}),flush=True)")
        with open_worker(backend, str(root / "python" / "python.exe"),
                         ["-I", "-S", "-B", "-u", "-c", code], readonly_trees=(root,)) as child:
            result = exchange(child, deadline=time.monotonic() + 30,
                              checkpoint=lambda: None, output=lambda _: None,
                              dispatch=lambda _: (_ for _ in ()).throw(ValueError("unexpected probe RPC")))
    value = result["value"]
    from packaging.tags import parse_tag
    from packaging.markers import default_environment
    if (not isinstance(value, dict) or set(value) != {"tags", "markers"}
            or not isinstance(value["tags"], list) or not 1 <= len(value["tags"]) <= 4096
            or any(not isinstance(tag, str) or len(tag) > 256 for tag in value["tags"])
            or not isinstance(value["markers"], dict)
            or set(value["markers"]) != set(default_environment())
            or any(not isinstance(item, str) or len(item) > 1024 for item in value["markers"].values())):
        raise ValueError("invalid isolated interpreter facts")
    tags = set()
    for tag in value["tags"]:
        parsed = parse_tag(tag)
        if len(parsed) != 1:
            raise ValueError("invalid isolated interpreter tag")
        tags.update(parsed)
    return tags, value["markers"]
