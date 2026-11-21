"""Powerline 交互原语 — 菜单渲染、选择、确认、输入"""
from __future__ import annotations

import io
import os
import signal
import sys
from contextlib import contextmanager
from contextvars import ContextVar
import time
from typing import Any, Callable, Iterator

from rich.console import Console
from rich.cells import cell_len
from rich.text import Text

from core.windows_console_input import read_control_keys

console = Console()

_input_guard = ContextVar("prompt_input_guard", default=None)
_input_wait_scope = ContextVar("prompt_input_wait_scope", default=None)


@contextmanager
def input_wait_scope(factory) -> Iterator[None]:
    """Install the supervisor's budget scope for actual terminal waits."""
    token = _input_wait_scope.set(factory)
    try:
        yield
    finally:
        _input_wait_scope.reset(token)


@contextmanager
def _waiting_for_input() -> Iterator[None]:
    factory = _input_wait_scope.get()
    if factory is None or _input_guard.get() is None:
        yield
    else:
        with factory():
            yield


@contextmanager
def guard_input(check: Callable[[], None]) -> Iterator[None]:
    """Scope deadline/authorization checks to a host-mediated UI call."""
    token = _input_guard.set(check)
    try:
        yield
    finally:
        _input_guard.reset(token)


def _check_input() -> None:
    check = _input_guard.get()
    if check is not None:
        check()
        if not _stdin_is_tty():
            raise PermissionError("sandbox UI requires terminal input")


def _getwch() -> str:
    with read_control_keys():
        return _read_windows_key()


def _read_windows_key(read=None) -> str:
    import msvcrt
    from core.cleanup import (_begin_console_input, _console_interrupt_pending,
                              _end_console_input)

    from core.windows_console_input import key_available

    if read is None:
        read = msvcrt.getwch
        def ready():
            return msvcrt.kbhit() or (_input_guard.get() is not None and key_available())
    else:
        ready = key_available
    active = _begin_console_input()
    interrupted = False
    try:
        if active:
            # Poll instead of injecting a synthetic key, which could remain in
            # the console queue if a real key wins the race with Ctrl+C.
            with _waiting_for_input():
                while not _console_interrupt_pending():
                    if _input_guard.get() is not None:
                        _check_input()
                        if ready():
                            break
                    elif ready():
                        break
                    time.sleep(0.05)
            value = "\x03" if _console_interrupt_pending() else read()
        elif _input_guard.get() is not None:
            with _waiting_for_input():
                while True:
                    _check_input()
                    if ready():
                        break
                    time.sleep(0.05)
            value = read()
        else:
            value = read()
    finally:
        if active:
            interrupted = _end_console_input()
    if interrupted:
        value = "\x03"
    if _input_guard.get() is not None and value in ("\x03", "\x04"):
        raise KeyboardInterrupt
    return value


def _read_multi_select_key(pressed: set[int]) -> str:
    if os.name != "nt" or not _stdin_is_tty():
        return _read_key_seq()
    from core.windows_console_input import read_key_event

    def read():
        event = read_key_event()
        if event is None:
            return ""
        key, down, character = event
        if not down:
            pressed.discard(key)
            return ""
        if key in (0x26, 0x28, 0x20):  # Up, Down, Space
            if key in pressed:
                return ""
            pressed.add(key)
            return {0x26: "UP", 0x28: "DOWN", 0x20: " "}[key]
        return character if character != "\x00" else ""

    while True:
        value = _read_windows_key(read)
        if value:
            return value


def _read_fd(fd: int, count: int) -> bytes:
    if _input_guard.get() is not None:
        import select as _select
        with _waiting_for_input():
            while True:
                _check_input()
                if _select.select([fd], [], [], 0.05)[0]:
                    break
    value = os.read(fd, count)
    if _input_guard.get() is not None and value in (b"\x03", b"\x04"):
        raise KeyboardInterrupt
    return value


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


class UserExit(SystemExit):
    """用户按 Ctrl+C / Ctrl+D 主动退出程序，属正常退出（区别于插件 sys.exit()）"""

    def __init__(self) -> None:
        super().__init__(0)


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

_ACTIVE_SCHEME = _CATPPUCCIN_MOCHA
_PALETTE: list[Seg] = _ACTIVE_SCHEME['header']
_INPUT_SEG: Seg = _ACTIVE_SCHEME['input']
_PIPE_COLOR: str = _ACTIVE_SCHEME['pipe']


def _rebuild_ansi() -> str:
    r, g, b = int(_PIPE_COLOR[1:3], 16), int(_PIPE_COLOR[3:5], 16), int(_PIPE_COLOR[5:7], 16)
    return f'38;2;{r};{g};{b}'


_PIPE_COLOR_ANSI: str = _rebuild_ansi()


# ─── 按键读取 ─────────────────────────────────────────────────────────────────

def _stdin_is_tty() -> bool:
    """stdin 是否为 TTY（fileno 不可用时视为非 TTY）"""
    try:
        return os.isatty(sys.stdin.fileno())
    except (ValueError, OSError, AttributeError, io.UnsupportedOperation):
        return False


def _read_key() -> str:
    """读取单个按键（无需回车），跨平台"""
    _check_input()
    if not _stdin_is_tty():
        try:
            return sys.stdin.read(1)
        except (OSError, EOFError):
            return ""
    if os.name == 'nt':
        return _getwch()
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
            # 直接读 fd：sys.stdin 是带缓冲的 TextIOWrapper，会把后续字节
            # （如方向键序列的 '[A'）预读进自己的缓冲区，导致 select 探不到
            data = _read_fd(fd, 1)
            ch = data.decode(errors='ignore')
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
    _check_input()
    if os.name == 'nt':
        import msvcrt
        while msvcrt.kbhit():
            if _getwch() in ('\x03', '\x04'):
                raise UserExit
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
                _check_input()
                if not (_read_fd(fd, 1) if _input_guard.get() is not None else sys.stdin.read(1)):
                    break
        except Exception:
            if _input_guard.get() is not None:
                raise
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)


# ─── 渲染原语 ─────────────────────────────────────────────────────────────────

def _seg_text(text: str, s: Seg) -> Text:
    label = text.strip().rstrip('。．.').rstrip()
    t = Text(f' {label} ')
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


def _render_info_lines(lines: list[str] | None, *, markup: bool = True) -> None:
    """Render the common information rows used by interactive menus."""
    for line in lines or []:
        for part in line.splitlines() or ['']:
            row = Text()
            row.append(f' {_TEE} ', style=_PIPE_COLOR)
            row.append_text(Text.from_markup(part) if markup else Text(part))
            # Let the terminal wrap long paths naturally without moving the
            # whole resource onto a new line after the row prefix.
            console.print(row, soft_wrap=True)


# ─── 公开 API ─────────────────────────────────────────────────────────────────

def clear_screen() -> None:
    """清除终端屏幕（跨平台）"""
    from core.cli import non_interactive
    if not non_interactive.get():
        if os.name == 'nt':
            console.clear()
        else:
            os.system('clear')


def print_header(breadcrumb: list[str]) -> None:
    """在清屏后渲染 Powerline 分段色块面包屑标题（复用 select 菜单的通用标题样式）"""
    _render_header(breadcrumb)


# Keep Ctrl+C local while rendering and running on_ready, before the first read.
@read_control_keys()
def select(
    breadcrumb: list[str],
    subtitle: str,
    choices: list[dict[str, Any]],
    theme_key: str = 'root',
    show_shortcuts: list[tuple[str, str, str]] | None = None,
    back_label: str = '',
    on_ready=None,
    description: str = '',
    info_lines: list[str] | None = None,
) -> str | None:
    """
    Powerline 风格选择菜单（单键无回车）。

    choices 格式：[{"key": "1", "label": "📦 软件管理"}]
    show_shortcuts 格式：[("t", "🎨 切换主题", "theme"), ...]
    info_lines：标题和选项之间逐行显示的信息。
    on_ready：菜单完全渲染、即将开始等待按键前回调一次（用于在首屏渲染
              完成后再启动联网/重型后台任务，保证"进菜单"接近瞬时）。

    连续数字选项超过 9 项时自动分页，并返回原始选项的 key。
    返回选中的 key 字符串；用户按 0 返回 None；Esc 抛 UserCancel。
    """
    from core.cli import require_interaction
    require_interaction()
    numbered = [c for c in choices if c['key'] != '0']
    if len(numbered) > 9 and [c['key'] for c in numbered] == [str(i + 1) for i in range(len(numbered))]:
        return _select_numbered_pages(breadcrumb, subtitle, numbered, theme_key, show_shortcuts,
                                      back_label, on_ready, description, info_lines)
    clear_screen()
    labels = [*breadcrumb, subtitle] if subtitle else breadcrumb
    _render_header(labels)
    _render_info_lines([*([description] if description else []), *(info_lines or [])], markup=False)

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

    if on_ready is not None:
        try:
            on_ready()
        except Exception as exc:
            # Callback errors may contain secrets; keep the menu usable and
            # log only the exception type, even if the log destination fails.
            try:
                import logging
                logging.getLogger("opskit.startup").warning(
                    "menu ready callback failed: error=%s", type(exc).__name__
                )
            except Exception:
                pass

    while True:
        ch = _read_key_seq()
        if not ch:
            if not _stdin_is_tty():
                console.print()
                return None
            continue
        if ch in ('UP', 'DOWN', 'NAV'):
            continue
        if ch == '0':
            console.print()
            return None
        if ch == CANCEL_KEY:
            console.print()
            raise UserCancel
        if ch in ('\x03', '\x04'):
            console.print()
            raise UserExit
        if ch in valid:
            console.print(ch)
            return ch


def _select_numbered_pages(breadcrumb, subtitle, choices, theme_key, show_shortcuts,
                           back_label, on_ready, description, info_lines):
    """Page standard numbered menus while preserving the caller's original keys."""
    from core.i18n import t
    from core.theme import get_icon

    shortcuts = {key for key, _, _ in show_shortcuts or []}
    available = (key for key in dict.fromkeys('pn<>[]abcdefghijklmnopqrstuvwxyz') if key not in shortcuts)
    previous, following = next(available), next(available)
    total_pages = (len(choices) + 8) // 9
    page = 0
    while True:
        start = page * 9
        page_items = choices[start:start + 9]
        visible = [{**item, 'key': str(i + 1)} for i, item in enumerate(page_items)]
        if page:
            visible.append({'key': previous, 'label': f"{get_icon('back')} {t('prompt.prev_page')}"})
        if page + 1 < total_pages:
            visible.append({'key': following, 'label': f"{get_icon('next_page')} {t('prompt.next_page')}"})
        key = select(breadcrumb, f'{subtitle} ({page + 1}/{total_pages})', visible, theme_key,
                     show_shortcuts, back_label, on_ready, description, info_lines)
        on_ready = None
        if key is None:
            return None
        if key == previous and page:
            page -= 1
        elif key == following and page + 1 < total_pages:
            page += 1
        elif key.isdecimal() and 1 <= int(key) <= len(page_items):
            return page_items[int(key) - 1]['key']
        else:
            return key


@read_control_keys()
def select_input(breadcrumb: list[str], subtitle: str, choices: list[dict[str, Any]],
                 back_label: str = '', description: str = '', default: str = '',
                 info_lines: list[str] | None = None) -> str:
    """Display the standard numbered menu and accept a full line of input."""
    from core.cli import require_interaction
    require_interaction()
    clear_screen()
    _render_header([*breadcrumb, subtitle] if subtitle else breadcrumb)
    _render_info_lines([*([description] if description else []), *(info_lines or [])], markup=False)
    _render_options([(c['key'], c['label']) for c in choices if c['key'] != '0'], back_label=back_label)
    return _read_line() or default


def paged_select(
    *,
    breadcrumb: list[str],
    items: list[Any],
    choice_of: Callable[[Any, int], dict[str, Any]],
    subtitle_of: Callable[[int, int], str],
    back_label: str,
    nav_labels: tuple[str, str] | None = None,
    theme_key: str = 'root',
    page_size: int = 9,
    on_select: Callable[[Any], None] | None = None,
    initial_page: int = 0,
) -> Any | None:
    """分页单选 — 统一「列表分页 + p/n 翻页 + 选中」交互。

    各菜单此前各自重复一套 page/total_pages/n/p 循环（5 处），此处收敛为一：

    Args:
        items: 待选原始对象列表（任意类型）。
        choice_of: ``(item, idx_on_page) -> choice dict``；返回的 dict 若缺
            ``key`` 字段会自动补为页内序号（从 1 起）。
        subtitle_of: ``(page, total_pages) -> str``，由调用方决定副标题文案。
        nav_labels: ``(上一页文案, 下一页文案)``，缺省用 ``prompt.prev_page`` /
            ``prompt.next_page`` 通用文案。
        on_select: 为 None → 选中即返回该 item（pick 模式）；否则对选中 item
            调用此回调并继续停留在列表（browse 模式，翻页状态保持）。
        initial_page: 初始页的零起始下标；只定位页面，不自动选择。

    Returns:
        pick 模式返回选中 item；browse 模式或用户取消/返回时返回 None。
    """
    if type(page_size) is not int or not 1 <= page_size <= 9:
        raise ValueError('page_size must be an integer between 1 and 9')
    total = len(items)
    total_pages = (total + page_size - 1) // page_size
    if type(initial_page) is not int or not 0 <= initial_page < max(total_pages, 1):
        raise ValueError('initial_page must identify an existing page')
    if nav_labels is None:
        from core.i18n import t
        from core.theme import get_icon
        nav_labels = (f"{get_icon('back')} {t('prompt.prev_page')}",
                      f"{get_icon('next_page')} {t('prompt.next_page')}")
    prev_label, next_label = nav_labels
    page = initial_page

    while True:
        start = page * page_size
        page_items = items[start:min(start + page_size, total)]

        choices: list[dict[str, Any]] = []
        items_by_key: dict[str, Any] = {}
        for i, item in enumerate(page_items):
            ch = dict(choice_of(item, i))
            ch.setdefault('key', str(i + 1))
            key = ch['key']
            if (not isinstance(key, str) or len(key) != 1 or key in {'0', 'p', 'n'}
                    or key in items_by_key):
                raise ValueError('page choice keys must be unique single characters other than 0, p, or n')
            items_by_key[key] = item
            choices.append(ch)
        if page > 0:
            choices.append({'key': 'p', 'label': prev_label})
        if page < total_pages - 1:
            choices.append({'key': 'n', 'label': next_label})

        try:
            key = select(
                breadcrumb=breadcrumb,
                subtitle=subtitle_of(page, total_pages),
                choices=choices,
                theme_key=theme_key,
                back_label=back_label,
            )
        except UserCancel:
            return None
        if key is None:
            return None
        if key == 'n':
            page += 1
            continue
        if key == 'p':
            page -= 1
            continue

        if key not in items_by_key:
            continue
        selected = items_by_key[key]
        if on_select is None:
            return selected
        on_select(selected)


def _read_key_seq() -> str:
    """读取一个按键，方向键归一化为 'UP' / 'DOWN'（跨平台）。

    Unix：ESC 后 50ms 内跟随 '[' 视为 ANSI 序列（方向键），否则视为 ESC。
    Windows：msvcrt 前缀 '\\x00' / '\\xe0' 后跟 'H'（上）/ 'P'（下）。
    """
    _check_input()
    if not _stdin_is_tty():
        return _read_key()
    if os.name == 'nt':
        ch = _getwch()
        if ch in ('\x00', '\xe0'):
            ch2 = _getwch()
            if ch2 == 'H':
                return 'UP'
            if ch2 == 'P':
                return 'DOWN'
            return ''
        return ch
    import select as _sel
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        # 整个转义序列必须在同一个 raw 窗口内读完：恢复 canonical 模式后
        # 缓冲区里的后续字节（如 '[A'）没有换行不会被 select 报告可读
        tty.setraw(fd)
        ch = _read_fd(fd, 1).decode(errors='ignore')
        if ch != '\x1b':
            return ch
        if not _sel.select([fd], [], [], 0.05)[0]:
            return ch
        seq = _read_fd(fd, 1).decode(errors='ignore')
        if seq not in ('[', 'O'):  # 'O' = application mode 方向键（ESC O A 等）
            return ch
        code = _read_fd(fd, 1).decode(errors='ignore')
        # 吞掉序列剩余字节（如 Home/End/Delete 的 '~'）
        while _sel.select([fd], [], [], 0)[0]:
            if not _read_fd(fd, 1):
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if code == 'A':
        return 'UP'
    if code == 'B':
        return 'DOWN'
    return 'NAV'


@read_control_keys()
def multi_select(
    breadcrumb: list[str],
    subtitle: str,
    options: list[str],
    theme_key: str = 'root',
    back_label: str = '',
    all_index: int | None = None,
) -> list[int] | None:
    """Powerline 风格多选菜单：↑↓ 移动光标、空格勾选、回车确认。

    subtitle  — 标题下的提示，传空字符串可隐藏
    options   — 选项文案列表
    all_index — 「全选」项下标（可选）：勾选它全选、取消它全不选；
                其余项全部勾上时自动连带勾上，任一项取消时自动连带取消

    返回勾选项的下标列表（可为空）；用户按 0 返回 None；ESC 抛 UserCancel。
    """
    if not options:
        return []
    from core.cli import require_interaction
    require_interaction()
    cursor = 0
    checked: set[int] = set()
    pressed: set[int] = set()
    page_size = 9
    total = len(options)
    from core.i18n import t
    from core.theme import get_icon

    def _render() -> None:
        clear_screen()
        _render_header(breadcrumb)
        if subtitle:
            row2 = Text(f' {_TEE} ', style=_PIPE_COLOR)
            row2.append_text(_cap_text(_INPUT_SEG))
            row2.append_text(_seg_text(subtitle, _INPUT_SEG))
            row2.append_text(_tail_text(_INPUT_SEG))
            console.print(row2)
        # Text 明文拼接（[ ] 不参与 rich 标记解析）；光标显示在括号内（[•]），勾选为 [✔]
        start = cursor // page_size * page_size
        end = min(start + page_size, total)
        for i in range(start, end):
            label = options[i]
            row = Text()
            tee = Text(f' {_TEE} ')
            tee.stylize(_PIPE_COLOR)
            row.append_text(tee)
            row.append('[')
            if i in checked:
                mark_style = 'bold bright_cyan' if i == cursor else 'green'
                row.append('✔', style=mark_style)
            elif i == cursor:
                row.append('•', style='bold bright_cyan')
            else:
                row.append(' ')
            row.append('] ')
            row.append(_pad_label(label), style='bold' if i == cursor else '')
            console.print(row)
        navigation = []
        if start:
            navigation.append(('p', f"{get_icon('back')} {t('prompt.prev_page')}"))
        if end < total:
            navigation.append(('n', f"{get_icon('next_page')} {t('prompt.next_page')}"))
        if back_label:
            navigation.append(('0', back_label))
        for key, label in navigation:
            row = Text()
            tee = Text(f' {_TEE} ')
            tee.stylize(_PIPE_COLOR)
            row.append_text(tee)
            row.append(f'({key}) {_pad_label(label)}', style='dim' if key == '0' else '')
            console.print(row)
        console.print(Text(f' {_BEND}', style=_PIPE_COLOR), end=' ')

    while True:
        # Commit a whole frame before reading the next key. In a Windows
        # terminal, individual row writes can leave the previous focus visible
        # while the new title and rows are still being drawn.
        with console:
            _render()
        ch = _read_multi_select_key(pressed)
        if not ch:
            if not _stdin_is_tty():
                console.print()
                return None
            continue
        if ch == 'UP':
            cursor = (cursor - 1) % len(options)
        elif ch == 'DOWN':
            cursor = (cursor + 1) % len(options)
        elif ch.lower() == 'n':
            next_start = (cursor // page_size + 1) * page_size
            if next_start < total:
                cursor = next_start
        elif ch.lower() == 'p':
            previous_start = (cursor // page_size - 1) * page_size
            if previous_start >= 0:
                cursor = previous_start
        elif ch == ' ':
            if cursor in checked:
                checked.discard(cursor)
                if all_index is not None and cursor == all_index:
                    checked.clear()
            else:
                checked.add(cursor)
                if all_index is not None and cursor == all_index:
                    checked.update(range(len(options)))
            if all_index is not None and cursor != all_index:
                others = set(range(len(options))) - {all_index}
                if others <= checked:
                    checked.add(all_index)
                else:
                    checked.discard(all_index)
        elif ch in ('\r', '\n'):
            console.print()
            return sorted(checked)
        elif ch == '0':
            console.print()
            return None
        elif ch == CANCEL_KEY:
            console.print()
            raise UserCancel
        elif ch in ('\x03', '\x04'):
            console.print()
            raise UserExit


@read_control_keys()
def confirm(
    breadcrumb: list[str],
    prompt: str,
    theme_key: str = 'root',
    info_lines: list[str] | None = None,
    require_explicit: bool = False,
) -> bool:
    """Powerline 风格确认（y/N 单键）。

    info_lines — 标题与选项之间的信息行（├─ 前缀，如插件概要）
    非交互模式（--yes）下直接返回 True。
    """
    from core.cli import require_interaction
    if require_explicit or not _auto_yes:
        require_interaction()
    if _auto_yes and not require_explicit:
        return True
    clear_screen()
    _render_header([*breadcrumb, prompt])
    _render_info_lines(info_lines)
    from core.i18n import t as _t
    _render_options([('y', _t('prompt.yes')), ('n', _t('prompt.no'))], back_label=_t('menu.back'))

    while True:
        ch = _read_key().lower()
        if not ch:
            console.print('n')
            return False
        if ch == 'y':
            console.print('y')
            return True
        if ch in ('n', '0', '\r', '\n', ' ') or ch == CANCEL_KEY:
            console.print('n')
            return False
        if ch in ('\x03', '\x04'):
            console.print()
            raise UserExit


def _read_line(secret: bool = False, *, initial: str = '') -> str:
    """读取一行文本输入（带回显，secret 时回显 *），Ctrl+C / ESC 安全。

    Windows：msvcrt 逐字符读取。
    Unix：termios raw 模式逐字符读取，行为与 Windows 对齐。
    两端均支持 ESC 抛 UserCancel、Ctrl+C 退出、退格删字符。
    """
    _check_input()
    buf = list(initial)
    cursor = len(buf)

    def display(characters):
        return '*' * len(characters) if secret else ''.join(characters)

    def edit(key):
        nonlocal cursor
        if key == 'LEFT' and cursor:
            cursor -= 1
            output = '\b' * cell_len(display(buf[cursor:cursor + 1]))
        elif key == 'RIGHT' and cursor < len(buf):
            output = display(buf[cursor:cursor + 1])
            cursor += 1
        elif key in ('\b', '\x7f') and cursor:
            cursor -= 1
            width = cell_len(display([buf.pop(cursor)]))
            tail = display(buf[cursor:])
            output = '\b' * width + tail + ' ' * width + '\b' * (cell_len(tail) + width)
        elif key == 'DELETE' and cursor < len(buf):
            width = cell_len(display([buf.pop(cursor)]))
            tail = display(buf[cursor:])
            output = tail + ' ' * width + '\b' * (cell_len(tail) + width)
        elif len(key) == 1 and key >= ' ' and key != '\x7f':
            tail = display(buf[cursor:])
            buf.insert(cursor, key)
            cursor += 1
            output = display(key) + tail + '\b' * cell_len(tail)
        else:
            return
        sys.stdout.write(output)
        sys.stdout.flush()

    if initial:
        sys.stdout.write(display(initial))
        sys.stdout.flush()
    if os.name == 'nt':
        while True:
            ch = _getwch()
            if ch in ('\x00', '\xe0'):
                edit({'K': 'LEFT', 'M': 'RIGHT', 'S': 'DELETE'}.get(_getwch(), ''))
                continue
            if ch in ('\r', '\n'):
                sys.stdout.write('\n')
                sys.stdout.flush()
                return ''.join(buf).strip()
            if ch in ('\x03', '\x04'):
                sys.stdout.write('\n')
                sys.stdout.flush()
                raise UserExit
            if ch == CANCEL_KEY:
                sys.stdout.write('\n')
                sys.stdout.flush()
                raise UserCancel
            edit(ch)
    else:
        import select as _sel
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)

        def _read_char() -> str:
            # 直接读 fd（同 _read_key）：sys.stdin 缓冲会吞掉后续字节导致
            # select 探不到；首字节为多字节 UTF-8 前缀时补齐剩余字节
            data = _read_fd(fd, 1)
            if not data:
                return ''
            first = data[0]
            need = 0
            if first >= 0xF0:
                need = 3
            elif first >= 0xE0:
                need = 2
            elif first >= 0xC0:
                need = 1
            while need > 0 and _sel.select([fd], [], [], 0.05)[0]:
                data += _read_fd(fd, 1)
                need -= 1
            return data.decode(errors='ignore')

        try:
            tty.setraw(fd)
            while True:
                ch = _read_char()
                if not ch or ch in ('\r', '\n'):
                    sys.stdout.write('\r\n')
                    sys.stdout.flush()
                    return ''.join(buf).strip()
                if ch in ('\x03', '\x04'):
                    sys.stdout.write('\r\n')
                    sys.stdout.flush()
                    raise UserExit
                if ch == CANCEL_KEY:
                    # 与 _read_key_seq 对齐：ESC 后 50ms 内跟随 '[' / 'O' 视为
                    # 导航键序列。只消费当前序列，保留后续正常输入。
                    if _sel.select([fd], [], [], 0.05)[0]:
                        seq = _read_fd(fd, 1).decode(errors='ignore')
                        if seq == '[':
                            code = ''
                            while _sel.select([fd], [], [], 0.05)[0]:
                                b = _read_fd(fd, 1).decode(errors='ignore')
                                code += b
                                if not b or '@' <= b <= '~':
                                    break
                            edit({'D': 'LEFT', 'C': 'RIGHT', '3~': 'DELETE'}.get(code, ''))
                            continue
                        if seq == 'O':
                            if _sel.select([fd], [], [], 0.05)[0]:
                                code = _read_fd(fd, 1)
                                edit({b'D': 'LEFT', b'C': 'RIGHT'}.get(code, ''))
                            continue
                        # ESC + 其他字符（如 Alt 组合键）：忽略该字符，视为取消
                    sys.stdout.write('\r\n')
                    sys.stdout.flush()
                    raise UserCancel
                edit(ch)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _mask_secret(value: str) -> str:
    """敏感默认值脱敏显示：只露首尾各 4 位"""
    if not value:
        return ''
    if len(value) <= 8:
        return '****'
    return f'{value[:4]}****{value[-4:]}'


@read_control_keys()
def text_input(
    breadcrumb: list[str],
    prompt: str,
    default: str = '',
    hint: str = '',
    theme_key: str = 'root',
    info_lines: list[str] | None = None,
    secret: bool = False,
    *,
    initial: str = '',
) -> str:
    """Powerline 风格文本输入，ESC 抛出 UserCancel，由调用方返回上一层。

    prompt     — 字段标签，显示在色块 pill 里
    hint       — 输入行提示（如默认值），显示在 ╰─ 右侧
    default    — 回车时的返回值（未填写时使用）
    info_lines — 标签与输入行之间的信息行（├─ 前缀，如「当前 1.2.3」）
    secret     — 敏感输入（如密码），回显 *
    initial    — 预先写入可编辑输入缓冲区的单行文本，光标位于末尾
    """
    from core.cli import require_interaction
    require_interaction()
    if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in initial):
        raise ValueError('initial text must be a single line without control characters')
    clear_screen()
    _render_header(breadcrumb)

    row2 = Text()
    tee = Text(f' {_TEE} ')
    tee.stylize(_PIPE_COLOR)
    row2.append_text(tee)
    row2.append_text(_cap_text(_INPUT_SEG))
    row2.append_text(_seg_text(prompt, _INPUT_SEG))
    row2.append_text(_tail_text(_INPUT_SEG))
    console.print(row2)

    _render_info_lines(info_lines, markup=False)

    _hint = hint or (_mask_secret(default) if secret else default)
    sys.stdout.write(f' \033[{_PIPE_COLOR_ANSI}m{_BEND}\033[0m ')
    if _hint:
        sys.stdout.write(_hint + ' ')
    sys.stdout.flush()
    raw = _read_line(secret=secret, initial=initial) if initial else _read_line(secret=secret)
    return raw if raw else default


_pause_suppressed: ContextVar[bool] = ContextVar("prompt_pause_suppressed", default=False)


@contextmanager
def suppress_pause() -> Iterator[None]:
    """Let an outer menu own the final pause while nested operations print results."""
    token = _pause_suppressed.set(True)
    try:
        yield
    finally:
        _pause_suppressed.reset(token)


@read_control_keys()
def pause(msg: str = '') -> None:
    """操作完成后等待用户按任意键（屏蔽 SIGINT 防止二次中断）。

    非交互模式（--yes）下直接返回，不等待按键。
    """
    from core.cli import non_interactive
    if _pause_suppressed.get() or non_interactive.get():
        return
    if _auto_yes:
        return
    import signal
    if not msg:
        from core.i18n import t as _t
        msg = _t('prompt.pause')

    # Windows: 用 SetConsoleCtrlHandler 彻底屏蔽 Ctrl+C
    _handler_ref = None
    shield = _input_guard.get() is None
    if shield and os.name == 'nt':
        import ctypes
        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def _handler(event):
            return 1  # 吞掉所有控制台事件
        _handler_ref = _handler
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
    elif shield:
        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        _drain_input()
        sys.stdout.write(f'\n\033[{_PIPE_COLOR_ANSI}m{msg}\033[0m ')
        sys.stdout.flush()
        try:
            if _read_key() in ('\x03', '\x04'):
                raise UserExit
        except KeyboardInterrupt:
            if not shield:
                raise
        except (EOFError, OSError):
            pass
        console.print()
    finally:
        if shield and os.name == 'nt':
            import ctypes
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, False)
        elif shield:
            signal.signal(signal.SIGINT, old_handler)


def print_pause_hint(msg: str = '') -> None:
    """只打印返回提示，不等待按键（供已消费按键的实时刷新界面用）"""
    if not msg:
        from core.i18n import t as _t
        msg = _t('prompt.pause')
    sys.stdout.write(f'\n\033[{_PIPE_COLOR_ANSI}m{msg}\033[0m\n')
    sys.stdout.flush()
