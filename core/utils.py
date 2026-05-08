"""通用工具函数 — 格式化 / 字符串处理"""
from __future__ import annotations


def fmt_bytes(b: int | float) -> str:
    """将字节数格式化为人类可读字符串"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def fmt_uptime(seconds: float) -> str:
    """将秒数格式化为 'Xd Xh Xm' 形式"""
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def fmt_percent_bar(percent: float, width: int = 20) -> str:
    """生成文字进度条，如 '████░░░░░░ 42%'"""
    filled = int(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {percent:.1f}%"


def truncate(s: str, max_len: int, ellipsis: str = "…") -> str:
    """截断字符串，超出时末尾加省略号"""
    if len(s) <= max_len:
        return s
    return s[: max_len - len(ellipsis)] + ellipsis
