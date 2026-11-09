"""Japan time (JST, UTC+9) for every timestamp the bot shows or saves: logs, trade log, sheet, page, bot names.

JST has no daylight saving, so a fixed offset is exact and doesn't depend on the machine's time zone setting.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

JST = timezone(timedelta(hours=9), "JST")


def now_jst() -> datetime:
    return datetime.now(JST)


def jst_struct(secs: Optional[float] = None) -> time.struct_time:
    """Like time.localtime(), but always in JST."""
    return time.gmtime((time.time() if secs is None else secs) + 9 * 3600)


def iso_jst(value: str) -> str:
    """An ISO time with any offset ('2026-09-24T02:05:23+00:00') -> the same moment in JST ('...T11:05:23+09:00')."""
    try:
        return datetime.fromisoformat(value).astimezone(JST).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return value


def use_jst_in_logs() -> None:
    """Make every log formatter's %(asctime)s print JST."""
    logging.Formatter.converter = staticmethod(jst_struct)
