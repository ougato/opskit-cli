from io import StringIO
from types import SimpleNamespace
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


def test_title_segments_have_single_space_padding_and_no_terminal_period():
    assert prompt._seg_text('  文件冲突：ContentAdapter.ts。  ',prompt._INPUT_SEG).plain==' 文件冲突：ContentAdapter.ts '
    assert prompt._seg_text('OpsKit.',prompt._INPUT_SEG).plain==' OpsKit '


def test_windows_menu_clear_erases_scrollback_after_viewport(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(output, "isatty", lambda: True)
    monkeypatch.setattr(prompt, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(prompt, "console", SimpleNamespace(file=output, legacy_windows=False))

    with prompt.screen_transition(lambda: output.write("finish previous animation\n")):
        prompt.clear_screen()
    assert output.getvalue() == "finish previous animation\n\x1b[2J\x1b[3J\x1b[H"


def test_menu_clear_skips_non_terminal_output(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(prompt, "console", SimpleNamespace(file=output))
    prompt.clear_screen()
    assert output.getvalue() == ""
