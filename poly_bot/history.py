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


# --- the coin's own price inside each past round, for simulating the move mode -----------------------------------
# Polymarket's live feed only reaches back a minute, so past paths come from Binance's <COIN>USDT candles (Chainlink,
# which the rounds settle on, follows the big exchanges closely; the level can differ by a few dollars, the moves
# hardly). 1-second candles for 5m and 15m rounds, 1-minute ones for longer. Per round: "o" = the price at its start,
# "k" = [[seconds in, highest - o so far, lowest - o so far], ...] at each second that set a new high or low (when a
# move was first reached), "c" = the close - o of every candle in order, one per "s" seconds (a Move condition:
# where the price is at any moment), "a" = the 60 s TWAP Polymarket settles on (prices.TWAP_FROM/TO) minus the TWAP
# at the round's start, one per "s" seconds (a Move condition, like the bot; needs the minute before the round, so
# each fetch starts PRE_SEC early). Rounds cached before "a" existed are fetched again. Kept in
# data/history/<COIN>-<tf>-coin.json.
PRE_SEC = 70
BINANCE = "https://api.binance.com/api/v3/klines"


def twap_path(candles: list, start: int, end: int, step: int) -> list:
    """The TWAP's move from the round's start, one per `step` seconds: at each moment t the mean close over
    [t - TWAP_FROM, t - TWAP_TO] (a second with no trade keeps the last close) minus that mean at the start."""
    from .prices import TWAP_FROM, TWAP_TO
    closes = {c[0]: c[4] for c in candles}
    first = start - TWAP_FROM
    filled, last = {}, None
    for t in range(min(closes) if closes else first, end + 1):
        last = closes.get(t, last)
        if t >= first and last is not None:
            filled[t] = last

    keys = sorted(filled)
    sums, counts, tot, n = {}, {}, 0.0, 0          # running sums: each window's mean in O(1)
    for k in keys:
        tot, n = tot + filled[k], n + 1
        sums[k], counts[k] = tot, n

    def upto(t):                                   # (sum, count) of the filled seconds <= t
        t = min(t, keys[-1]) if keys else t
        while keys and t >= keys[0] and t not in sums:
            t -= 1
        return (sums[t], counts[t]) if keys and t >= keys[0] else (0.0, 0)

    def twap(t):
        (s1, n1), (s0, n0) = upto(t - TWAP_TO), upto(t - TWAP_FROM - 1)
        return (s1 - s0) / (n1 - n0) if n1 > n0 else None

    base = twap(start)
    if base is None:
        return []
    return [round((twap(start + i * step) or base) - base, 2) for i in range((end - start) // step)]


class CoinPaths:
    def __init__(self, folder: Path = HISTORY_DIR):
        self.folder = folder
        self._lock = threading.Lock()
        self._jobs: dict[tuple, dict] = {}
        self._cache: dict[tuple, dict] = {}

    def path(self, coin: str, tf: str) -> Path:
        return self.folder / f"{coin.upper()}-{tf}-coin.json"

    def paths(self, coin: str, tf: str, rounds: list) -> tuple[dict, Optional[dict]]:
        """({round slug: {"o", "k"}} for the rounds cached so far, progress of a load still running or None)."""
        key = (coin.upper(), tf)
        with self._lock:
            if key not in self._cache:
                try:
                    self._cache[key] = json.loads(self.path(*key).read_text())["paths"]
                except (OSError, ValueError, KeyError):
                    self._cache[key] = {}
            have = self._cache[key]
            missing = [r for r in rounds if "a" not in have.get(r["slug"], {})]
            job = self._jobs.get(key)
            running = job and job["thread"].is_alive()
            if missing and not running and not (job and job.get("failed_for") == len(missing)):
                job = {"done": 0, "total": len(missing), "error": ""}
                job["thread"] = threading.Thread(target=self._load, args=(key, job, missing), daemon=True,
                                                 name=f"coinpaths-{coin}-{tf}")
                self._jobs[key] = job
                job["thread"].start()
                running = True
        progress = {k: job[k] for k in ("done", "total", "error")} if running else None
        return have, progress

    def _load(self, key: tuple, job: dict, rounds: list) -> None:
        coin, tf = key
        sec = ROUND_SECONDS[tf] <= 900
        step = 1 if sec else 60
        got: dict = {}
        # one request covers 1000 candles: walk the rounds in time order, fetching each stretch once
        rounds = sorted(rounds, key=lambda r: r["start"])
        chunks, cur = [], None
        pre = PRE_SEC if sec else 120                      # the minute before each round, for its TWAP start
        for r in rounds:
            if cur and r["end"] <= cur[0] + 1000 * step:
                cur[1].append(r)
            else:
                cur = [int(r["start"]) - pre, [r]]
                chunks.append(cur)

        def fetch(chunk):
            start, rs = chunk
            try:
                k = get_json(BINANCE, {"symbol": f"{coin.upper()}USDT", "interval": "1s" if sec else "1m",
                                       "startTime": start * 1000, "limit": 1000})
            except MarketError as e:
                job["error"] = str(e)
                k = []
            candles = [(int(c[0]) // 1000, float(c[1]), float(c[2]), float(c[3]), float(c[4])) for c in k]
            out = {}
            for r in rs:
                inside = [c for c in candles if r["start"] <= c[0] < r["end"]]
                if not inside:
                    continue
                o, hi, lo, k = inside[0][1], 0.0, 0.0, []
                for c in inside:          # only the candles that set a new high or low: a move of any size is
                    if c[2] - o > hi or c[3] - o < lo:       # first reached at one of them
                        hi, lo = max(hi, c[2] - o), min(lo, c[3] - o)
                        k.append([c[0] - int(r["start"]), round(hi, 4), round(lo, 4)])
                closes = {(c[0] - int(r["start"])) // step: c[4] for c in inside}
                path, last = [], o                       # a second with no trade keeps the last close
                for i in range(int(r["end"] - r["start"]) // step):
                    last = closes.get(i, last)
                    path.append(round(last - o, 2))
                out[r["slug"]] = {"o": o, "k": k, "c": path, "s": step,
                                  "a": twap_path(candles, int(r["start"]), int(r["end"]), step)}
            job["done"] += len(rs)
            return out

        with ThreadPoolExecutor(4) as ex:
            for part in ex.map(fetch, chunks):
                got.update(part)
        with self._lock:
            merged = {**self._cache.get(key, {}), **got}
            keep = {r["slug"] for r in rounds} | set(self._cache.get(key, {}))
            self._cache[key] = {s: v for s, v in merged.items() if s in keep}
            if not got:
                job["failed_for"] = len(rounds)
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path(coin, tf).with_suffix(".tmp")                   # compact: it's big
        tmp.write_text(json.dumps({"coin": coin, "timeframe": tf, "paths": self._cache[key]}, separators=(",", ":")))
        tmp.replace(self.path(coin, tf))
        log.info("Coin paths %s %s: %d rounds (%d new)", coin, tf, len(self._cache[key]), len(got))
