from io import StringIO
from rich.console import Console
from core import prompt,theme


def test_description_between_title_and_options(monkeypatch):
    stream=StringIO();console=Console(file=stream,width=100,color_system=None)
    monkeypatch.setattr(prompt,'console',console)
    monkeypatch.setattr(prompt.os,'system',lambda *a:0)
    monkeypatch.setattr(prompt,'_read_key_seq',lambda:'1')
    assert prompt.select(['OpsKit'],'选择对应属性',[{'key':'1','label':'spr_headFrame'}],description='源属性：sp_head_frame（类型：cc.Sprite）')=='1'
    text=stream.getvalue()
    assert text.index('选择对应属性')<text.index('源属性')<text.index('(1)')<text.index('(0)')
    assert '🔨' not in text


def test_info_optional_icon_preserves_default(monkeypatch):
    stream=StringIO();monkeypatch.setattr(theme,'console',Console(file=stream,color_system=None))
    monkeypatch.setattr(theme,'get_icon',lambda key:'🔨')
    theme.print_info('plain',show_icon=False)
    assert '🔨' not in stream.getvalue()
    theme.print_info('default')
    assert '🔨 default' in stream.getvalue()
