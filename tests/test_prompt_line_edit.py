from io import StringIO
import os
import sys

import pytest

from core import prompt
from core.windows_console_input import _has_key, _InputRecord


@pytest.mark.skipif(os.name != 'nt', reason='Windows console input')
@pytest.mark.parametrize('backspace', ['\x08', '\x7f'])
@pytest.mark.parametrize('enter', ['\r', '\n'])
def test_windows_chinese_backspace_and_enter(monkeypatch, backspace, enter):
    keys = iter(['游', '戏', backspace, '名', enter])
    output = StringIO()
    monkeypatch.setattr(prompt, '_getwch', lambda: next(keys))
    monkeypatch.setattr(sys, 'stdout', output)
    assert prompt._read_line() == '游名'
    assert output.getvalue() == '游戏\b\b  \b\b名\n'


@pytest.mark.skipif(os.name != 'nt', reason='Windows console input')
def test_windows_secret_backspace_erases_one_mask_cell(monkeypatch):
    keys = iter(['中', '\b', '文', '\r'])
    output = StringIO()
    monkeypatch.setattr(prompt, '_getwch', lambda: next(keys))
    monkeypatch.setattr(sys, 'stdout', output)
    assert prompt._read_line(secret=True) == '文'
    assert output.getvalue() == '*\b \b*\n'


@pytest.mark.skipif(os.name != 'nt', reason='Windows console input')
def test_windows_backspace_empty_and_ascii_remain_usable(monkeypatch):
    keys = iter(['\b', 'a', 'b', '\b', 'c', '\r'])
    output = StringIO()
    monkeypatch.setattr(prompt, '_getwch', lambda: next(keys))
    monkeypatch.setattr(sys, 'stdout', output)
    assert prompt._read_line() == 'ac'
    assert output.getvalue() == 'ab\b \bc\n'


@pytest.mark.parametrize('character', ['\b', '\r', '中'])
def test_pending_key_after_release_event_is_detected(character):
    released = _InputRecord()
    released.event_type = 1
    released.event.key.key_down = False
    released.event.key.character = '中'
    pressed = _InputRecord()
    pressed.event_type = 1
    pressed.event.key.key_down = True
    pressed.event.key.character = character
    assert not _has_key([released])
    assert _has_key([released, pressed])


def test_pending_extended_key_after_release_is_detected():
    released = _InputRecord()
    released.event_type = 1
    pressed = _InputRecord()
    pressed.event_type = 1
    pressed.event.key.key_down = True
    pressed.event.key.virtual_key = 0x26  # Up arrow
    assert _has_key([released, pressed])


def test_non_input_key_does_not_trigger_blocking_read():
    pressed = _InputRecord()
    pressed.event_type = 1
    pressed.event.key.key_down = True
    pressed.event.key.virtual_key = 0x2c  # Print Screen
    assert not _has_key([pressed])
