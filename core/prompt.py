"""Powerline 交互原语 — 菜单渲染、选择、确认、输入"""
from __future__ import annotations

import os
import signal
import sys
from contextlib import contextmanager
from typing import Any, Iterator

from rich.console import Console
from rich.text import Text

console = Console()


# ─── 非交互模式全局标志 ─────────────────────────────────────────────────────
# --yes / -y / OPSKIT_YES=1 时启用，跳过所有确认弹窗和 pause
_auto_yes: bool = False


def set_auto_yes(value: bool = True) -> None:
    """设置全局非交互模式（--yes / OPSKIT_YES=1）"""
    global _auto_yes
    _auto_yes = value


def is_auto_yes() -> bool:
    """查询当前是否处于非交互模式"""
    return _auto_yes


# ─── 取消/返回热键（只改这一行即可全局切换）────────────────────────────────────
# '\x1b' = ESC   '\x03' = Ctrl+C   '0' = 数字 0
CANCEL_KEY: str = '\x1b'


class UserCancel(Exception):
    """用户按 CANCEL_KEY（默认 ESC）主动取消，由调用方决定是返回上层还是退出"""


@contextmanager
def shield_ctrlc() -> Iterator[None]:
    """在代码块执行期间屏蔽 Ctrl+C，退出后若收到过则安全 raise KeyboardInterrupt。

    适用于所有耗时操作（subprocess / httpx / 流式输出）——Ctrl+C 不会在操作
    中途崩溃进程，而是等待代码块结束后在主线程安全抛出。

    Windows：SetConsoleCtrlHandler 吞掉 Ctrl+C 事件 + signal.SIG_IGN
    Unix   ：自定义 SIGINT handler 只记录标志位
    """
    _interrupted = False
    _handler_ref = None
    _old_sigint = signal.getsignal(signal.SIGINT)

    if sys.platform == "win32":
        import ctypes

        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def _win_handler(event: int) -> int:
            nonlocal _interrupted
            if event == 0:
                _interrupted = True
                return 1
            return 0

        _handler_ref = _win_handler
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    else:
        def _unix_handler(signum: int, frame: object) -> None:
            nonlocal _interrupted
            _interrupted = True

        signal.signal(signal.SIGINT, _unix_handler)

    try:
        yield
    finally:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, False)
        try:
            signal.signal(signal.SIGINT, _old_sigint)
        except (OSError, ValueError):
            pass

    if _interrupted:
        raise KeyboardInterrupt


# ─── Powerline 特殊字符 ────────────────────────────────────────────────────────
_PL_ARROW = '\ue0b0'
_PL_CAP_L = '\ue0b6'

# ─── 连接符 ───────────────────────────────────────────────────────────────────
_PIPE = '│'
_TEE = '├─'
_BEND = '╰─'

# ─── 色块数据类 ───────────────────────────────────────────────────────────────

class Seg:
    """一个色块：前景色 fg + 背景色 bg，永远成对"""
    __slots__ = ('fg', 'bg')

    def __init__(self, fg: str, bg: str) -> None:
        self.fg = fg
        self.bg = bg

    def style(self) -> str:
        return f'{self.fg} on {self.bg}'


# ─── 配色方案 ─────────────────────────────────────────────────────────────────

_CATPPUCCIN_MOCHA = {
    'name': 'Catppuccin Mocha',
    'header': [
        Seg('bold #1e1e2e', '#fab387'),
        Seg('bold #1e1e2e', '#a6e3a1'),
        Seg('bold #1e1e2e', '#89b4fa'),
        Seg('bold #1e1e2e', '#f5c2e7'),
    ],
    'input': Seg('bold #1e1e2e', '#cba6f7'),
    'pipe': '#6c7086',
}

_TOKYO_NIGHT = {
    'name': 'Tokyo Night',
    'header': [
        Seg('bold #1a1b26', '#ff9e64'),
        Seg('bold #1a1b26', '#9ece6a'),
        Seg('bold #1a1b26', '#7aa2f7'),
        Seg('bold #1a1b26', '#bb9af7'),
    ],
    'input': Seg('bold #1a1b26', '#e0af68'),
    'pipe': '#565f89',
}

_DRACULA = {
    'name': 'Dracula',
    'header': [
        Seg('bold #282a36', '#ffb86c'),
        Seg('bold #282a36', '#50fa7b'),
        Seg('bold #282a36', '#8be9fd'),
        Seg('bold #282a36', '#ff79c6'),
    ],
    'input': Seg('bold #282a36', '#bd93f9'),
    'pipe': '#6272a4',
}

_SCHEME_MAP = {
    'catppuccin': _CATPPUCCIN_MOCHA,
    'catppuccin_mocha': _CATPPUCCIN_MOCHA,
    'tokyo_night': _TOKYO_NIGHT,
    'dracula': _DRACULA,
}

_ACTIVE_SCHEME = _CATPPUCCIN_MOCHA
_PALETTE: list[Seg] = _ACTIVE_SCHEME['header']
_INPUT_SEG: Seg = _ACTIVE_SCHEME['input']
_PIPE_COLOR: str = _ACTIVE_SCHEME['pipe']


def _rebuild_ansi() -> str:
    r, g, b = int(_PIPE_COLOR[1:3], 16), int(_PIPE_COLOR[3:5], 16), int(_PIPE_COLOR[5:7], 16)
    return f'38;2;{r};{g};{b}'


_PIPE_COLOR_ANSI: str = _rebuild_ansi()


def switch_scheme(name: str) -> None:
    """按名称切换 Powerline 配色方案（供 theme 切换时调用）"""
    global _ACTIVE_SCHEME, _PALETTE, _INPUT_SEG, _PIPE_COLOR, _PIPE_COLOR_ANSI
    scheme = _SCHEME_MAP.get(name.lower().replace(' ', '_'), _CATPPUCCIN_MOCHA)
    _ACTIVE_SCHEME = scheme
    _PALETTE = scheme['header']
    _INPUT_SEG = scheme['input']
    _PIPE_COLOR = scheme['pipe']
    _PIPE_COLOR_ANSI = _rebuild_ansi()


# ─── 按键读取 ─────────────────────────────────────────────────────────────────

def _read_key() -> str:
    """读取单个按键（无需回车），跨平台"""
    if os.name == 'nt':
        import msvcrt
        return msvcrt.getwch()
    else:
        import tty
        import termios
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            # 非 TTY 环境（如 SSH pipe / 重定向），直接 readline 降级
            try:
                sys.stdin.readline()
            except Exception:
                pass
            return ""
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch


def _kbhit() -> bool:
    """非阻塞检测是否有按键输入，跨平台"""
    if os.name == 'nt':
        import msvcrt
        return msvcrt.kbhit()
    else:
        import select as _select
        return bool(_select.select([sys.stdin], [], [], 0)[0])


def _drain_input() -> None:
    """清空 stdin 中残留的待读字节，跨平台。

    单键确认（如 confirm 的 y/n）只读取一个字节，用户随手按下的回车等
    残留字节会留在缓冲区里，污染随后的"按任意键"等待，导致返回需要多按
    一次。这里在等待前先丢弃残留输入，保证只消费一次用户主动按键。
    """
    if os.name == 'nt':
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()
    else:
        import select as _select
        import termios
        import tty
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while _select.select([sys.stdin], [], [], 0)[0]:
                if not sys.stdin.read(1):
                    break
        except Exception:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)


# ─── 渲染原语 ─────────────────────────────────────────────────────────────────

def _seg_text(text: str, s: Seg) -> Text:
    t = Text(f' {text} ')
    t.stylize(s.style())
    return t


def _sep_text(prev: Seg, nxt: Seg) -> Text:
    t = Text(_PL_ARROW)
    t.stylize(f'{prev.bg} on {nxt.bg}')
    return t


def _cap_text(s: Seg) -> Text:
    t = Text(_PL_CAP_L)
    t.stylize(s.bg)
    return t


def _tail_text(s: Seg) -> Text:
    t = Text(_PL_ARROW)
    t.stylize(s.bg)
    return t


def _render_header(labels: list[str]) -> None:
    """渲染 Powerline 分段色块头部"""
    result = Text()
    segs = [_PALETTE[i % len(_PALETTE)] for i in range(len(labels))]
    result.append_text(_cap_text(segs[0]))
    for i, (lbl, seg) in enumerate(zip(labels, segs)):
        result.append_text(_seg_text(lbl, seg))
        if i < len(segs) - 1:
            result.append_text(_sep_text(seg, segs[i + 1]))
        else:
            result.append_text(_tail_text(seg))
    console.print(result)


def _pad_label(label: str) -> str:
    """在每个 \\uFE0F variation selector 后插入补偿空格，修复终端宽度错位"""
    chars: list[str] = []
    for index, ch in enumerate(label):
        chars.append(ch)
        if ch == '\uFE0F':
            next_ch = label[index + 1] if index + 1 < len(label) else ''
            if next_ch and not next_ch.isspace():
                chars.append(' ')
    return ''.join(chars)


def _render_options(items: list[tuple[str, str]], back_label: str = '') -> None:
    """渲染选项列表 + ╰─ 光标行"""
    pipe = f'[{_PIPE_COLOR}]'
    for key, label in items:
        console.print(f' {pipe}{_TEE}[/] [bold]({key})[/] {_pad_label(label)}')
    console.print(f' {pipe}{_TEE}[/] [dim](0) {_pad_label(back_label)}[/]')
    sys.stdout.write(f' \033[{_PIPE_COLOR_ANSI}m{_BEND}\033[0m ')
    sys.stdout.flush()


# ─── 公开 API ─────────────────────────────────────────────────────────────────

def clear_screen() -> None:
    """清除终端屏幕（跨平台）"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header(breadcrumb: list[str]) -> None:
    """在清屏后渲染 Powerline 分段色块面包屑标题（复用 select 菜单的通用标题样式）"""
    _render_header(breadcrumb)


def render_banner(title: str, subtitle: str) -> None:
    """渲染 Powerline 风格渐变色 Banner（仅在主菜单调用一次）"""
    from core.theme import get_banner_config
    cfg = get_banner_config()
    gradient: list[str] = cfg.get('gradient', [])

    if gradient and title:
        colored = Text()
        for i, ch in enumerate(title):
            color = gradient[i % len(gradient)]
            colored.append(ch, style=f'bold {color}')
        title_text = colored
    else:
        title_text = Text(title, style='bold bright_white')

    sub_text = Text(f'  {subtitle}  ', style='dim white', justify='center')
    console.print()
    console.print(title_text, justify='left')
    console.print(sub_text, justify='left')


def select(
    breadcrumb: list[str],
    subtitle: str,
    choices: list[dict[str, Any]],
    theme_key: str = 'root',
    show_shortcuts: list[tuple[str, str, str]] | None = None,
    back_label: str = '',
) -> str | None:
    """
    Powerline 风格选择菜单（单键无回车）。

    choices 格式：[{"key": "1", "label": "📦 软件管理"}]
    show_shortcuts 格式：[("t", "🎨 切换主题", "theme"), ...]

    返回选中的 key 字符串；用户按 0 返回 None；Ctrl+C 抛 UserCancel。
    """
    os.system('cls' if os.name == 'nt' else 'clear')
    labels = [*breadcrumb, subtitle] if subtitle else breadcrumb
    _render_header(labels)

    numeric_items: list[tuple[str, str]] = [
        (c['key'], c['label']) for c in choices if c['key'] != '0'
    ]
    _render_options(numeric_items, back_label=back_label)

    shortcut_keys: set[str] = set()
    if show_shortcuts:
        pipe = f'[{_PIPE_COLOR}]'
        parts = []
        for sk, sl, _ in show_shortcuts:
            parts.append(f'[dim]({sk})[/dim] {sl}')
            shortcut_keys.add(sk)
        console.print(f'\n {pipe}[/] ' + '  '.join(parts))

    valid: set[str] = {c['key'] for c in choices} | shortcut_keys

    while True:
        ch = _read_key()
        if ch == '0':
            console.print()
            return None
        if ch == CANCEL_KEY:
            console.print()
            raise UserCancel
        if ch in ('\x03', '\x04'):
            console.print()
            sys.exit(0)
        if ch in valid:
            console.print(ch)
            return ch


def confirm(
    breadcrumb: list[str],
    prompt: str,
    theme_key: str = 'root',
) -> bool:
    """Powerline 风格确认（y/N 单键）。

    非交互模式（--yes）下直接返回 True。
    """
    if _auto_yes:
        return True
    os.system('cls' if os.name == 'nt' else 'clear')
    _render_header([*breadcrumb, prompt])
    from core.i18n import t as _t
    _render_options([('y', _t('prompt.yes')), ('n', _t('prompt.no'))], back_label=_t('prompt.no'))

    while True:
        ch = _read_key().lower()
        if ch == 'y':
            console.print('y')
            return True
        if ch in ('n', '0', '\r', '\n', ' ') or ch == CANCEL_KEY:
            console.print('n')
            return False
        if ch in ('\x03', '\x04'):
            console.print()
            sys.exit(0)


def _read_line() -> str:
    """读取一行文本输入（带回显），Ctrl+C / ESC 安全。

    Windows：msvcrt 逐字符读取。
    Unix：termios raw 模式逐字符读取，行为与 Windows 对齐。
    两端均支持 ESC 抛 UserCancel、Ctrl+C 退出、退格删字符。
    """
    if os.name == 'nt':
        import msvcrt
        buf: list[str] = []
        while True:
            ch = msvcrt.getwch()
            if ch in ('\r', '\n'):
                sys.stdout.write('\n')
                sys.stdout.flush()
                return ''.join(buf).strip()
            if ch in ('\x03', '\x04'):
                sys.stdout.write('\n')
                sys.stdout.flush()
                sys.exit(0)
            if ch == '\x08':
                if buf:
                    buf.pop()
                    sys.stdout.write('\x08 \x08')
                    sys.stdout.flush()
                continue
            if ch == CANCEL_KEY:
                sys.stdout.write('\n')
                sys.stdout.flush()
                raise UserCancel
            buf.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        buf: list[str] = []
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ('\r', '\n'):
                    sys.stdout.write('\r\n')
                    sys.stdout.flush()
                    return ''.join(buf).strip()
                if ch in ('\x03', '\x04'):
                    sys.stdout.write('\r\n')
                    sys.stdout.flush()
                    sys.exit(0)
                if ch in ('\x7f', '\x08'):
                    if buf:
                        buf.pop()
                        sys.stdout.write('\x08 \x08')
                        sys.stdout.flush()
                    continue
                if ch == CANCEL_KEY:
                    # 吞掉 ESC 后可能跟随的 ANSI 序列字节（如方向键）
                    import select as _sel
                    while _sel.select([sys.stdin], [], [], 0.05)[0]:
                        sys.stdin.read(1)
                    sys.stdout.write('\r\n')
                    sys.stdout.flush()
                    raise UserCancel
                if ch >= ' ':
                    buf.append(ch)
                    sys.stdout.write(ch)
                    sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def text_input(
    breadcrumb: list[str],
    prompt: str,
    default: str = '',
    hint: str = '',
    theme_key: str = 'root',
) -> str:
    """Powerline 风格文本输入。

    prompt  — 字段标签，显示在色块 pill 里
    hint    — 输入行提示（如默认值），显示在 ╰─ 右侧
    default — 回车时的返回值（未填写时使用）
    """
    os.system('cls' if os.name == 'nt' else 'clear')
    _render_header(breadcrumb)

    row2 = Text()
    tee = Text(f' {_TEE} ')
    tee.stylize(_PIPE_COLOR)
    row2.append_text(tee)
    row2.append_text(_cap_text(_INPUT_SEG))
    row2.append_text(_seg_text(prompt, _INPUT_SEG))
    row2.append_text(_tail_text(_INPUT_SEG))
    console.print(row2)

    _hint = hint or default
    hint_str = f'  ({_hint})' if _hint else ''
    sys.stdout.write(f' \033[{_PIPE_COLOR_ANSI}m{_BEND}\033[0m{hint_str} ')
    sys.stdout.flush()
    raw = _read_line()
    return raw if raw else default


def pause(msg: str = '') -> None:
    """操作完成后等待用户按任意键（屏蔽 SIGINT 防止二次中断）。

    非交互模式（--yes）下直接返回，不等待按键。
    """
    if _auto_yes:
        return
    import signal
    if not msg:
        from core.i18n import t as _t
        msg = _t('prompt.pause')

    # Windows: 用 SetConsoleCtrlHandler 彻底屏蔽 Ctrl+C
    _handler_ref = None
    if os.name == 'nt':
        import ctypes
        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def _handler(event):
            return 1  # 吞掉所有控制台事件
        _handler_ref = _handler
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
    else:
        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        _drain_input()
        sys.stdout.write(f'\n\033[{_PIPE_COLOR_ANSI}m{msg}\033[0m ')
        sys.stdout.flush()
        try:
            _read_key()
        except (KeyboardInterrupt, EOFError, OSError):
            pass
        console.print()
    finally:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, False)
        else:
            signal.signal(signal.SIGINT, old_handler)


def print_pause_hint(msg: str = '') -> None:
    """只打印返回提示，不等待按键（供已消费按键的实时刷新界面用）"""
    if not msg:
        from core.i18n import t as _t
        msg = _t('prompt.pause')
    sys.stdout.write(f'\n\033[{_PIPE_COLOR_ANSI}m{msg}\033[0m\n')
    sys.stdout.flush()
