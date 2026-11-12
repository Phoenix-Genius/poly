"""Past rounds for the "real" simulation: each finished round's result and its Up price minute by minute.

Gamma lists a series' closed rounds (50 per request, newest first); the CLOB's prices-history gives the Up token's
price path (1-minute points for 5m-1h rounds, coarser for 4h and daily, so about 60 points per round). The Down
price is 1 - Up. Kept in data/history/<COIN>-<tf>.json and topped up with the rounds that finished since, at most
MAX_ROUNDS (about 7 days of 5m rounds). The first load takes a minute or so, so it runs in the background and
the page shows its progress.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .config import write_json
from .instance import ROOT
from .markets import CLOB, GAMMA, MarketError, Polymarket, get_json, parse_round
from .strategy import ROUND_SECONDS

HISTORY_DIR = ROOT / "data" / "history"
MAX_ROUNDS = 2016             # 7 days of 5m rounds; other timeframes keep as many rounds (further back)
PAGE = 50                     # Gamma refuses bigger pages when sorting
STALE_SEC = 30 * 60           # top up when the newest cached round ended longer ago than this
MIN_ROUNDS = 100              # fewer than this: not enough to simulate on

log = logging.getLogger("history")


class History:
    def __init__(self, pm: Polymarket, folder: Path = HISTORY_DIR):
        self.pm, self.folder = pm, folder
        self._lock = threading.Lock()
        self._jobs: dict[tuple, dict] = {}        # (coin, tf) -> {"done", "total", "error", "thread"}
        self._cache: dict[tuple, list] = {}

    def path(self, coin: str, tf: str) -> Path:
        return self.folder / f"{coin.upper()}-{tf}.json"

    def rounds(self, coin: str, tf: str) -> tuple[list[dict], Optional[dict]]:
        """(cached rounds oldest first, progress of a load still running or None). Starts a top-up when the
        cache is missing or stale; the rounds already cached can be used meanwhile."""
        key = (coin.upper(), tf)
        with self._lock:
            if key not in self._cache:
                try:
                    self._cache[key] = json.loads(self.path(*key).read_text())["rounds"]
                except (OSError, ValueError, KeyError):
                    self._cache[key] = []
            rounds = self._cache[key]
            job = self._jobs.get(key)
            running = job and job["thread"].is_alive()
            stale = not rounds or time.time() - rounds[-1]["end"] > STALE_SEC
            if stale and not running:
                job = {"done": 0, "total": 0, "error": ""}
                job["thread"] = threading.Thread(target=self._load, args=(key, job), daemon=True,
                                                 name=f"history-{coin}-{tf}")
                self._jobs[key] = job
                job["thread"].start()
                running = True
        progress = {k: job[k] for k in ("done", "total", "error")} if running or (job and job["error"]) else None
        return rounds, progress

    # --- loading -------------------------------------------------------------------------------------------
    def _load(self, key: tuple, job: dict) -> None:
        coin, tf = key
        try:
            with self._lock:
                have = list(self._cache.get(key) or [])
            known = {r["slug"] for r in have}
            sid = self.pm.series_id(coin, tf)
            new_rounds, offset = [], 0
            while len(new_rounds) + len(have) < MAX_ROUNDS + PAGE:
                events = get_json(f"{GAMMA}/events", {"series_id": sid, "closed": "true", "limit": PAGE,
                                                       "offset": offset, "order": "endDate", "ascending": "false"})
                if not events:
                    break
                offset += len(events)
                fresh = 0
                for e in events:
                    try:
                        r = parse_round(e)
                    except (MarketError, KeyError, ValueError):
                        continue
                    if r.slug in known:
                        continue
                    fresh += 1
                    if r.result in ("up", "down") and r.end <= time.time():
                        new_rounds.append(r)
                if not fresh:                       # reached what's cached already
                    break
                if len(new_rounds) >= MAX_ROUNDS:
                    break
            job["total"] = len(new_rounds)
            length = ROUND_SECONDS[tf]
            fidelity = max(1, length // 3600)       # minutes between points: ~60 points per round at most

            def path(r) -> Optional[dict]:
                try:
                    h = get_json(f"{CLOB}/prices-history", {"market": r.up_token, "startTs": int(r.start) - 120,
                                                            "endTs": int(r.end), "fidelity": fidelity})["history"]
                except (MarketError, KeyError, TypeError):
                    h = None
                job["done"] += 1
                if not h:
                    return None
                return {"slug": r.slug, "start": r.start, "end": r.end, "result": r.result,
                        "h": [[round(p["t"] - r.start), round(float(p["p"]), 4)] for p in h]}

            with ThreadPoolExecutor(8) as ex:
                got = [x for x in ex.map(path, new_rounds) if x]
            merged = sorted({r["slug"]: r for r in have + got}.values(), key=lambda r: r["start"])[-MAX_ROUNDS:]
            self.folder.mkdir(parents=True, exist_ok=True)
            write_json(self.path(coin, tf), {"coin": coin, "timeframe": tf, "rounds": merged})
            with self._lock:
                self._cache[key] = merged
            log.info("Price history %s %s: %d rounds (%d new)", coin, tf, len(merged), len(got))
        except Exception as e:  # noqa: BLE001 - shown on the page; the next request tries again
            job["error"] = str(e)
            log.warning("Price history %s %s failed: %s", coin, tf, e)
