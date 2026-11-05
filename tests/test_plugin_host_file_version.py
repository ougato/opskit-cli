import os
from pathlib import Path

import pytest

from core.native_sandbox import GrantChoice
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied
from core.plugin_host_tools import file_version


CREATOR = Path(r"D:\cocos\creator\editors\Creator\3.8.7\CocosCreator.exe")


@pytest.mark.skipif(os.name != "nt" or not CREATOR.is_file(), reason="Creator 3.8.7 fixture unavailable")
def test_windows_metadata_uses_authorized_directory(tmp_path, monkeypatch):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    require = authority.require
    observed = []
    def allow(scope, **kwargs):
        observed.append(scope)
        return require(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs)
    monkeypatch.setattr(authority, "require", allow)
    result = file_version("cocos", "a" * 64, str(CREATOR), authority=authority,
                          check_identity=lambda *args: True, checkpoint=lambda: None)
    assert result["product_version"] == [3, 8, 7, 0]
    assert observed[0].operation == "directory.read"
    assert observed[0].resource == str(CREATOR.parent)
    monkeypatch.setattr(authority, "require", lambda *a, **kw: (_ for _ in ()).throw(CapabilityDenied("denied")))
    with pytest.raises(CapabilityDenied):
        file_version("cocos", "a" * 64, str(CREATOR), authority=authority,
                     check_identity=lambda *args: True, checkpoint=lambda: None)
