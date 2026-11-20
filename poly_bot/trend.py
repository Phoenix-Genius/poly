"""How often each side won lately, for the trend side modes (trend_random, trend_follow).

Only the result of every finished round is needed (no prices), so it is quick to fetch: Gamma lists a series' closed
rounds newest first, 50 per request. Kept in data/history/<COIN>-<tf>-results.json as {start_unix: "u"/"d"} for the
last KEEP_DAYS days and topped up in the background; a bot reads what is cached, so it never waits on the fetch.
"""
from __future__ import annotations

import bisect
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .clock import JST
from .config import write_json
from .history import HISTORY_DIR
from .markets import GAMMA, MarketError, get_json, iso, parse_round

KEEP_DAYS = 32                 # enough for "this month" on the last day of a 31-day month
PAGE = 50
TOPUP_SEC = 60                 # ask Gamma for new rounds at most this often
MIN_ROUNDS = 20                # fewer rounds in the window: no trend yet (coin flip / first side)
WINDOWS = {"month": None, "30d": 30 * 86400, "7d": 7 * 86400, "3d": 3 * 86400, "1d": 86400,
           "12h": 12 * 3600, "6h": 6 * 3600}

log = logging.getLogger("trend")


def window_start(window: str, now: float) -> float:
    """Unix time the window begins: the 1st of this month 00:00 JST for "month", else now minus its length."""
    if window not in WINDOWS:
        raise ValueError(f"unknown trend window {window!r}")
    span = WINDOWS[window]
    if span is None:
        d = datetime.fromtimestamp(now, JST)
        return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return now - span


def share(starts: list, ups: list, since: float, until: float) -> tuple[Optional[float], int]:
    """(Up share, rounds) of the rounds that started in [since, until). starts: sorted; ups: prefix counts of Up
    with ups[0] = 0 (ups[i] = Up rounds among the first i). Share is None with fewer than MIN_ROUNDS rounds."""
    a, b = bisect.bisect_left(starts, since), bisect.bisect_left(starts, until)
    n = b - a
    if n < MIN_ROUNDS:
        return None, n
    return (ups[b] - ups[a]) / n, n


def prefix_ups(results: list) -> list:
    """[0, ups after 1 round, after 2, ...] for results in order ("up"/"down" or "u"/"d")."""
    out = [0]
    for r in results:
        out.append(out[-1] + (1 if r in ("up", "u") else 0))
    return out


class Results:
    """The won side of every recent round, per coin and timeframe. Thread-safe."""

    def __init__(self, series_id: Callable[[str, str], str], folder: Path = HISTORY_DIR,
                 clock: Callable[[], float] = time.time, fetch: Callable = get_json):
        self.series_id, self.folder, self.clock, self.fetch = series_id, folder, clock, fetch
        self._lock = threading.Lock()
        self._data: dict[tuple, dict] = {}        # key -> {start: "u"/"d"}
        self._sorted: dict[tuple, tuple] = {}     # key -> (starts, ups) for share()
        self._busy: set = set()
        self._asked: dict[tuple, float] = {}

    def path(self, coin: str, tf: str) -> Path:
        return self.folder / f"{coin.upper()}-{tf}-results.json"

    def up_share(self, coin: str, tf: str, window: str) -> tuple[Optional[float], int]:
        """(Up share, rounds counted) over the window so far; starts a top-up when due."""
        key = (coin.upper(), tf)
        now = self.clock()
        with self._lock:
            self._load(key)
            if key not in self._busy and now - self._asked.get(key, 0) >= TOPUP_SEC:
                self._asked[key] = now
                self._busy.add(key)
                threading.Thread(target=self._topup, args=(key,), daemon=True, name=f"trend-{coin}-{tf}").start()
            starts, ups = self._sorted[key]
        return share(starts, ups, window_start(window, now), now)

    def _load(self, key: tuple) -> None:
        if key in self._data:
            return
        try:
            self._data[key] = {int(k): v for k, v in json.loads(self.path(*key).read_text())["results"].items()}
        except (OSError, ValueError, KeyError, AttributeError):
            self._data[key] = {}
        self._index(key)

    def _index(self, key: tuple) -> None:
        items = sorted(self._data[key].items())
        self._sorted[key] = ([k for k, _ in items], prefix_ups([v for _, v in items]))

    def _page(self, sid: str, before: float) -> list:
        """One page of rounds that ended before `before`, newest first; a few tries (Gamma sometimes says 500)."""
        for attempt in range(3):
            try:
                return self.fetch(f"{GAMMA}/events", {"series_id": sid, "closed": "true", "limit": PAGE,
                                                      "end_date_max": iso(before), "order": "endDate",
                                                      "ascending": "false"})
            except MarketError:
                if attempt == 2:
                    raise
                time.sleep(2 + 3 * attempt)
        return []

    def _walk(self, sid: str, before: float, stop: float, got: dict) -> None:
        """Results of the rounds that ended before `before`, back to `stop`, into got. Stops at an error (what came
        in so far is kept)."""
        while before > stop:
            try:
                events = self._page(sid, before)
            except MarketError as e:
                log.warning("Results fetch stopped at %s: %s", iso(before), e)
                return
            ends = []
            for e in events or []:
                try:
                    r = parse_round(e)
                except (MarketError, KeyError, ValueError, TypeError):
                    continue
                ends.append(r.end)
                if r.result in ("up", "down"):
                    got[int(r.start)] = r.result[0]
            if not ends or min(ends) >= before:
                return
            before = min(ends) - 1

    def _topup(self, key: tuple) -> None:
        """New rounds since the newest cached one, then older ones back to KEEP_DAYS (a first load, or one an error
        cut short, finishes on later top-ups)."""
        coin, tf = key
        try:
            with self._lock:
                have = dict(self._data[key])
            now = self.clock()
            oldest_wanted = now - KEEP_DAYS * 86400
            sid = self.series_id(coin, tf)
            got: dict = {}
            self._walk(sid, now, max(have) if have else oldest_wanted, got)
            if not have or min(have) > oldest_wanted + 86400:
                self._walk(sid, min(have) if have else min(got, default=now), oldest_wanted, got)
            if not got:
                return
            have.update(got)
            have = {k: v for k, v in have.items() if k >= oldest_wanted - 86400}
            self.folder.mkdir(parents=True, exist_ok=True)
            write_json(self.path(coin, tf), {"coin": coin, "timeframe": tf,
                                             "results": {str(k): v for k, v in sorted(have.items())}})
            with self._lock:
                self._data[key] = have
                self._index(key)
            log.info("Results %s %s: %d rounds (%d fetched)", coin, tf, len(have), len(got))
        except Exception as e:  # noqa: BLE001 - tried again on the next top-up
            log.warning("Results %s %s failed: %s", coin, tf, e)
        finally:
            with self._lock:
                self._busy.discard(key)
