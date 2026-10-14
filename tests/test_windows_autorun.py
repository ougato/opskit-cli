from pathlib import Path
from types import SimpleNamespace
import pytest
from software._shared import mutation
from software.base import InstallError

from software._shared.windows_autorun import retire_legacy_autorun as retire


@pytest.mark.parametrize('recipe', ['python','nodejs','golang','java'])
@pytest.mark.parametrize('operation', ['_inject_path','_remove_path'])
def test_driver_preserves_administrator_autorun(tmp_path,monkeypatch,recipe,operation):
    import ctypes
    import importlib
    module=importlib.import_module(f'software.recipes.{recipe}.windows')
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    monkeypatch.setenv('PATH', 'C:\\Windows')
    values={'AutoRun':('administrator-init.cmd',1)}
    path_values={'HKCU':('C:\\Windows',2),'HKLM':('C:\\Windows',2)}
    def query(key,name):
        if name=='PATH':return path_values[key[0]]
        if name not in values:raise FileNotFoundError(name)
        return values[name]
    def write(key,name,reserved,kind,value):
        if name=='AutoRun':pytest.fail('driver overwrote administrator AutoRun')
        path_values[key[0]]=(value,kind)
    def delete(key,name):
        pytest.fail('driver deleted administrator AutoRun')
    registry=SimpleNamespace(HKEY_CURRENT_USER='HKCU',HKEY_LOCAL_MACHINE='HKLM',KEY_READ=1,KEY_SET_VALUE=2,REG_SZ=1,REG_EXPAND_SZ=2,OpenKey=lambda hive,*a:(hive,a[0]),QueryValueEx=query,SetValueEx=write,DeleteValue=delete,CloseKey=lambda key:None)
    monkeypatch.setitem(__import__('sys').modules,'winreg',registry)
    notification=SimpleNamespace(SendMessageTimeoutW=lambda *a:1)
    if hasattr(ctypes,'windll'):
        monkeypatch.setattr(ctypes.windll,'user32',notification)
    else:
        monkeypatch.setattr(ctypes,'windll',SimpleNamespace(user32=notification),raising=False)
    getattr(module.WindowsDriver(),operation)(str(tmp_path/'shims'))
    assert values=={'AutoRun':('administrator-init.cmd',1)}
    assert not list((tmp_path/'.opskit').glob('cmd_autorun*.bat'))


@pytest.mark.parametrize('recipe,index', [('python',0),('nodejs',0),('nodejs',1),('nodejs',2),('golang',0),('golang',1),('golang',2),('java',0),('java',1),('java',2)])
def test_recipe_retires_each_supported_legacy_route(tmp_path,monkeypatch,recipe,index):
    import importlib
    from software._shared import windows_autorun
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    driver=importlib.import_module(f'software.recipes.{recipe}.windows').WindowsDriver()
    captured=[]
    monkeypatch.setattr(windows_autorun,'retire_legacy_autorun',lambda *args:captured.append(args))
    driver._retire_legacy_autorun()
    path,known_paths,begin,end=captured[0]
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(f'@echo off\r\n@{begin}\r\n@set PATH={known_paths[index]};%PATH%\r\n@{end}\r\n'.encode())
    values={'AutoRun':(str(path),1)}
    def query(key,name):
        if name not in values:raise FileNotFoundError(name)
        return values[name]
    registry=SimpleNamespace(HKEY_CURRENT_USER='HKCU',KEY_READ=1,KEY_SET_VALUE=2,REG_SZ=1,OpenKey=lambda *a:'key',QueryValueEx=query,DeleteValue=lambda key,name:values.pop(name),CloseKey=lambda key:None)
    monkeypatch.setitem(__import__('sys').modules,'winreg',registry)
    monkeypatch.setattr(windows_autorun,'retire_legacy_autorun',retire)
    driver._retire_legacy_autorun()
    assert not values and not path.exists()

@pytest.mark.parametrize('case',['foreign','owned','malformed','permission','cancel','external_change'])
def test_retirement_preserves_authority_and_compensates(tmp_path,monkeypatch,case):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    monkeypatch.setattr(mutation,'lock_path',lambda:tmp_path/'locks'/'lock')
    monkeypatch.setattr(mutation,'system_lock_path',lambda:tmp_path/'system-locks'/'lock')
    path=tmp_path/'.opskit'/'legacy.bat'
    path.parent.mkdir()
    shims=str(tmp_path/'shims')
    begin,end='@rem begin','@rem end'
    body=f'@echo off\r\n@{begin}\r\n@set PATH={shims};%PATH%\r\n@{end}\r\n'
    path.write_bytes(('administrator command' if case=='malformed' else body).encode())
    before=path.read_bytes()
    values={'AutoRun':('administrator-init.cmd' if case=='foreign' else str(path),1)}
    prior=dict(values)
    closed=[]
    def query(key,name):
        if name not in values: raise FileNotFoundError(name)
        return values[name]
    registry=SimpleNamespace(HKEY_CURRENT_USER='HKCU',KEY_READ=1,KEY_SET_VALUE=2,REG_SZ=1,OpenKey=lambda *a:'key',QueryValueEx=query,DeleteValue=lambda key,name:values.pop(name),SetValueEx=lambda key,name,reserved,kind,value:values.update({name:(value,kind)}),CloseKey=lambda key:closed.append(key))
    monkeypatch.setitem(__import__('sys').modules,'winreg',registry)
    unlink=Path.unlink
    def fail(path_arg,*a,**kw):
        if path_arg==path:
            if case=='external_change':values['AutoRun']=('external.cmd',1)
            if case=='cancel':raise KeyboardInterrupt()
            raise PermissionError('denied')
        return unlink(path_arg,*a,**kw)
    if case in ('permission','cancel','external_change'):monkeypatch.setattr(Path,'unlink',fail)
    error={'malformed':FileExistsError,'permission':PermissionError,'cancel':KeyboardInterrupt,'external_change':InstallError}.get(case)
    if error:
        with pytest.raises(error):retire(path,(shims,),begin,end)
    else:retire(path,(shims,),begin,end)
    assert closed==['key']
    if case=='owned':
        assert not path.exists() and not values
    else:
        assert path.read_bytes()==before
        assert values==({'AutoRun':('external.cmd',1)} if case=='external_change' else prior)
