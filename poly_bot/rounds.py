"""Polymarket's own past rounds, for the page's 🔎 Rounds research table (not the bots' bets).

Per finished round of the last ~7 days (history.History: Gamma's results and the CLOB's Up price, about one point a
minute): the result, the Up price and the coin's move from the Price To Beat at fixed times left (history.CoinPaths:
the 60 s TWAP on Binance 1 s prices, like the bot's Move condition), the highest and lowest move in the round, and,
where data/research has them, the official Price To Beat and final price.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional

from .instance import ROOT
from .strategy import ROUND_SECONDS

LEFTS = {"5m": [300, 240, 180, 120, 90, 60, 30], "15m": [900, 600, 300, 180, 120, 90, 60, 30]}   # seconds left
CACHE_SEC = 60
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


def up_at(h: list, t: float) -> Optional[float]:
    """The Up price at t seconds into the round: the last price-history point at or before t."""
    got = None
    for sec, p in h:
        if sec > t:
            break
        got = p
    return got


def official(tf: str) -> dict:
    """{start unix: [Price To Beat, final price]} from data/research (refreshed by its tools), else {}."""
    try:
        return {int(k): v for k, v in json.loads((ROOT / "data" / "research" / f"polymarket-twap-{tf}.json").read_text()).items()}
    except (OSError, ValueError):
        return {}


def build(rounds: list, paths: dict, tf: str, twaps: dict) -> list:
    length, lefts = ROUND_SECONDS[tf], LEFTS.get(tf, LEFTS["5m"])
    out = []
    for r in reversed(rounds):                         # newest first
        p = paths.get(r["slug"]) or {}
        a, step = p.get("a") or [], p.get("s") or 1
        k = p.get("k") or []
        start = int(r["start"])
        tw = twaps.get(start)
        row = {"slug": r["slug"], "start": start, "result": r["result"],
               "up": [up_at(r.get("h") or [], length - left) for left in lefts],
               "move": [round(a[i], 2) if (i := int((length - left) / step)) < len(a) else None for left in lefts]
               if a else [None] * len(lefts),
               "high": round(k[-1][1], 2) if k else None, "low": round(k[-1][2], 2) if k else None,
               "beat": round(tw[0], 2) if tw else None, "final": round(tw[1], 2) if tw else None}
        out.append(row)
    return out


def rounds_view(history, coin_paths, coin: str, tf: str) -> dict:
    key = (coin.upper(), tf)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SEC:
            return hit[1]
    rounds, progress = history.rounds(coin, tf)
    paths, path_progress = coin_paths.paths(coin, tf, rounds) if rounds else ({}, None)
    view = {"coin": coin.upper(), "timeframe": tf, "lefts": LEFTS.get(tf, LEFTS["5m"]),
            "rounds": build(rounds, paths, tf, official(tf) if coin.lower() == "btc" else {}),
            "loading": progress or path_progress}
    if not view["loading"]:
        with _lock:
            _cache[key] = (time.time(), view)
    return view
