"""x-ui 流量快照与按时段统计。

x-ui.db 仅保存累计计数器（每个入站的 up/down），不留历史，故无法直接得出
今日/本周/本月用量。本模块通过 systemd 定时器每小时把计数器快照写入本地历史库，
再以「现值 − 周期起点基准」算出各周期增量；计数器被重置（现值 < 基准）时按 0 处理。
"""
from __future__ import annotations

import datetime
import sqlite3
import time

from xui.constants import (
    TRAFFIC_BYTE_STEP,
    TRAFFIC_BYTE_UNITS,
    TRAFFIC_HISTORY_SCHEMA,
    TRAFFIC_PERIOD_MONTH,
    TRAFFIC_PERIOD_TODAY,
    TRAFFIC_PERIOD_WEEK,
    XUI_DATABASE_FILE,
    XUI_TRAFFIC_HISTORY_FILE,
)


def _read_current() -> list[dict[str, object]]:
    """读取 x-ui.db 各入站的累计 up/down。"""
    if not XUI_DATABASE_FILE.exists():
        return []
    with sqlite3.connect(XUI_DATABASE_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select id, remark, up, down from inbounds").fetchall()
    return [
        {
            "id": int(row["id"]),
            "remark": row["remark"] or "",
            "up": int(row["up"] or 0),
            "down": int(row["down"] or 0),
        }
        for row in rows
    ]


def take_snapshot() -> None:
    """把当前累计计数器写入历史库（供 systemd 定时器调用）。"""
    current = _read_current()
    if not current:
        return
    ts = int(time.time())
    XUI_TRAFFIC_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(XUI_TRAFFIC_HISTORY_FILE) as conn:
        conn.execute(TRAFFIC_HISTORY_SCHEMA)
        conn.executemany(
            "insert into traffic_snapshots(ts, inbound_id, remark, up, down) "
            "values (?, ?, ?, ?, ?)",
            [(ts, c["id"], c["remark"], c["up"], c["down"]) for c in current],
        )


def _period_starts(now: datetime.datetime | None = None) -> dict[str, int]:
    now = now or datetime.datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - datetime.timedelta(days=now.weekday())
    month_start = day_start.replace(day=1)
    return {
        TRAFFIC_PERIOD_TODAY: int(day_start.timestamp()),
        TRAFFIC_PERIOD_WEEK: int(week_start.timestamp()),
        TRAFFIC_PERIOD_MONTH: int(month_start.timestamp()),
    }


def _baseline(conn: sqlite3.Connection, inbound_id: int, period_start: int) -> tuple[int, int] | None:
    """周期起点基准：取起点之前最近一次快照；无则取起点之后最早一次（尽力而为）。"""
    row = conn.execute(
        "select up, down from traffic_snapshots where inbound_id = ? and ts <= ? "
        "order by ts desc limit 1",
        (inbound_id, period_start),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "select up, down from traffic_snapshots where inbound_id = ? and ts >= ? "
            "order by ts asc limit 1",
            (inbound_id, period_start),
        ).fetchone()
    if row is None:
        return None
    return int(row["up"] or 0), int(row["down"] or 0)


def compute_stats(now: datetime.datetime | None = None) -> list[dict[str, object]]:
    """返回每个入站的累计与今日/本周/本月 上行/下行。

    无历史数据（定时器未跑过 / 无 systemd）时，周期值为 None，由调用方显示占位。
    """
    current = _read_current()
    if not current:
        return []
    starts = _period_starts(now)
    conn: sqlite3.Connection | None = None
    if XUI_TRAFFIC_HISTORY_FILE.exists():
        conn = sqlite3.connect(XUI_TRAFFIC_HISTORY_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute(TRAFFIC_HISTORY_SCHEMA)
    try:
        result: list[dict[str, object]] = []
        for c in current:
            node: dict[str, object] = {
                "remark": c["remark"],
                "total": {"up": c["up"], "down": c["down"]},
            }
            for label, period_start in starts.items():
                up = down = None
                if conn is not None:
                    base = _baseline(conn, int(c["id"]), period_start)
                    if base is not None:
                        up = max(0, int(c["up"]) - base[0])
                        down = max(0, int(c["down"]) - base[1])
                node[label] = {"up": up, "down": down}
            result.append(node)
        return result
    finally:
        if conn is not None:
            conn.close()


def human_bytes(num: int | None) -> str:
    if num is None:
        return "—"
    value = float(num)
    for unit in TRAFFIC_BYTE_UNITS:
        if value < TRAFFIC_BYTE_STEP:
            return f"{value:.0f} {unit}" if unit == TRAFFIC_BYTE_UNITS[0] else f"{value:.2f} {unit}"
        value /= TRAFFIC_BYTE_STEP
    return f"{value:.2f} {TRAFFIC_BYTE_UNITS[-1]}"
