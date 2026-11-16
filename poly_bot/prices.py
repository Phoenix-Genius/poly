"""Live coin prices and each round's "Price To Beat", for the move Bet mode.

Polymarket's Up/Down rounds settle on Chainlink's price: Up wins if the price at the end is >= the price at the
start (the "Price To Beat" on the round's page). Polymarket's real-time data socket pushes that same Chainlink price
about once a second (topic crypto_prices_chainlink, e.g. symbol "btc/usd"), and on subscribing sends the last minute
or so of it. One socket for the whole app keeps every watched coin's prices of the last 15 minutes, so a round's
Price To Beat is simply the price at its start second (checked against Polymarket's own number). A round that started
before the app was watching (e.g. right after a restart) asks Polymarket's crypto-price endpoint once instead.

Needs the `websockets` package (installed with the live-trading SDK); without it the move mode can't run.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .markets import MarketError, get_json, iso

URL = "wss://ws-live-data.polymarket.com"
OPEN_API = "https://polymarket.com/api/crypto/crypto-price"
KEEP_SEC = 20 * 60             # a 15m round's TWAP Price To Beat needs the minute before it
PING_SEC = 5
FRESH_SEC = 15                 # a price older than this isn't "live" any more
# Polymarket settles Up/Down rounds on Chainlink's 60 s TWAP: the mean of the per-second price from 62 to 3 s before
# the moment (checked 2026-11-16 against the events' eventMetadata priceToBeat/finalPrice: median error $0.00)
TWAP_FROM, TWAP_TO = 62, 3
TWAP_MIN_POINTS = 45           # fewer seconds than this in the window (a gap, a restart): no TWAP
VARIANT = {"5m": "fiveminute", "15m": "fifteen", "1h": "hourly", "4h": "fourhour", "daily": "daily"}

log = logging.getLogger("prices")


def symbol(coin: str) -> str:
    return f"{coin.lower()}/usd"


class PriceFeed:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._symbols: set[str] = set()
        self._series: dict[str, dict[int, float]] = {}    # symbol -> {unix second: price}
        self._last: dict[str, tuple[float, float]] = {}   # symbol -> (unix time, price)
        self._opens: dict[str, float] = {}                # round slug -> Price To Beat (once known)
        self._asked: dict[str, float] = {}                # round slug -> when the endpoint was last asked
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        try:
            from websockets.sync.client import connect  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False

    def watch(self, coin: str) -> None:
        if not self.available:
            return
        sym = symbol(coin)
        with self._lock:
            new = sym not in self._symbols
            self._symbols.add(sym)
            ws = self._ws
        if new and ws is not None:
            try:
                ws.send(json.dumps({"action": "subscribe", "subscriptions": [_sub(sym)]}))
            except Exception:  # noqa: BLE001 - the reader thread reconnects with every symbol
                pass
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="price-feed", daemon=True)
            self._thread.start()

    def twap(self, coin: str, at: Optional[float] = None) -> Optional[float]:
        """The 60 s TWAP Polymarket settles on, at time `at` (default now): the mean of the feed's prices from
        TWAP_FROM to TWAP_TO seconds before it. None when the feed doesn't cover that minute."""
        t = int(self.clock() if at is None else at)
        with self._lock:
            series = self._series.get(symbol(coin)) or {}
            vals = [series[s] for s in range(t - TWAP_FROM, t - TWAP_TO + 1) if s in series]
        return sum(vals) / len(vals) if len(vals) >= TWAP_MIN_POINTS else None

    def twap_to_beat(self, coin: str, r) -> Optional[float]:
        """Round r's real Price To Beat: the TWAP at its start (kept once known)."""
        key = "twap:" + r.slug
        with self._lock:
            if key in self._opens:
                return self._opens[key]
        if self.clock() < r.start - TWAP_TO:
            return None
        value = self.twap(coin, r.start)
        if value is not None:
            with self._lock:
                self._opens[key] = value
        return value

    def settle_prices(self, coin: str, r, timeframe: str) -> tuple[Optional[float], Optional[float], bool]:
        """(coin now, Price To Beat, exact): the 60 s TWAPs the round settles on when the feed covers them, else
        the spot price and spot Price To Beat (exact False), e.g. in the first minutes after a restart."""
        now, beat = self.twap(coin), self.twap_to_beat(coin, r)
        if now is not None and beat is not None:
            return now, beat, True
        return self.price(coin), self.price_to_beat(coin, r, timeframe), False

    def last_second(self, coin: str) -> int:
        """The unix second of the newest price received for the coin (0 = none)."""
        with self._lock:
            return int(self._last.get(symbol(coin), (0, 0))[0])

    def price(self, coin: str) -> Optional[float]:
        """The latest price, if it is live (younger than FRESH_SEC)."""
        with self._lock:
            got = self._last.get(symbol(coin))
        if got and self.clock() - got[0] <= FRESH_SEC:
            return got[1]
        return None

    def price_to_beat(self, coin: str, r, timeframe: str) -> Optional[float]:
        """Round r's Price To Beat: the price at its start second, from the feed, else Polymarket's endpoint
        (asked at most once a minute per round: it answers 429 when asked often)."""
        with self._lock:
            if r.slug in self._opens:
                return self._opens[r.slug]
            series = self._series.get(symbol(coin)) or {}
            start = int(r.start)
            value = series.get(start)
            if value is None and series and min(series) < start and any(start < s <= start + 2 for s in series):
                value = series[max(s for s in series if s <= start)]     # a second missing: the one before
            if value is not None:
                self._opens[r.slug] = value
                return value
            if self.clock() - self._asked.get(r.slug, 0) < 60:
                return None
            self._asked[r.slug] = self.clock()
        try:
            got = get_json(OPEN_API, {"symbol": coin.upper(), "eventStartTime": iso(r.start),
                                      "variant": VARIANT.get(timeframe, "fiveminute"), "endDate": iso(r.end)})
            value = float(got["openPrice"]) if got and got.get("openPrice") else None
        except (MarketError, KeyError, TypeError, ValueError):
            value = None
        if value is not None:
            with self._lock:
                self._opens[r.slug] = value
        return value

    def close_price(self, coin: str, start: float, end: float, timeframe: str) -> Optional[float]:
        """The coin's price at a round's end (what it settled on): the feed's price at the end second (kept 15 min),
        else Polymarket's endpoint once the round is complete."""
        with self._lock:
            series = self._series.get(symbol(coin)) or {}
            for s in range(int(end), int(end) - 3, -1):
                if s in series:
                    return series[s]
        try:
            got = get_json(OPEN_API, {"symbol": coin.upper(), "eventStartTime": iso(start),
                                      "variant": VARIANT.get(timeframe, "fiveminute"), "endDate": iso(end)})
            return float(got["closePrice"]) if got and got.get("completed") and got.get("closePrice") else None
        except (MarketError, KeyError, TypeError, ValueError):
            return None

    # --- the socket ---------------------------------------------------------------------------------------
    def _run(self) -> None:
        from websockets.sync.client import connect
        while True:
            try:
                with connect(URL, open_timeout=10) as ws:
                    with self._lock:
                        subs = [ALL] + [_sub(s) for s in self._symbols]
                        self._ws = ws
                    ws.send(json.dumps({"action": "subscribe", "subscriptions": subs}))
                    next_ping = time.time() + PING_SEC
                    while True:
                        if time.time() >= next_ping:
                            ws.send("PING")
                            next_ping = time.time() + PING_SEC
                            self._prune()
                        try:
                            msg = ws.recv(timeout=PING_SEC)
                        except TimeoutError:
                            continue
                        self._handle(msg)
            except Exception as e:  # noqa: BLE001 - network: try again shortly
                log.debug("price feed reconnecting: %s", e)
            with self._lock:
                self._ws = None
            time.sleep(3)

    def _handle(self, msg) -> None:
        if not isinstance(msg, str) or not msg.startswith("{"):
            return
        try:
            data = json.loads(msg)
        except ValueError:
            return
        p = data.get("payload") or {}
        sym = (p.get("symbol") or "").lower()        # updates and the backfill on subscribing both carry it
        points = p["data"] if isinstance(p.get("data"), list) else [p] if "value" in p else []
        if not sym or not points:
            return
        with self._lock:
            if sym not in self._symbols:             # ALL streams every coin: keep the watched ones
                return
            series = self._series.setdefault(sym, {})
            for d in points:
                try:
                    sec, v = int(d["timestamp"]) // 1000, float(d["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                series[sec] = v
                if sec >= self._last.get(sym, (0, 0))[0]:
                    self._last[sym] = (sec, v)

    def _prune(self) -> None:
        cut = self.clock() - KEEP_SEC
        with self._lock:
            for series in self._series.values():
                for s in [s for s in series if s < cut]:
                    del series[s]


class Recorder:
    """Keeps every Chainlink price Polymarket streams for `coins`, for research: <folder>/<coin>/<UTC day>.csv
    ("unix_sec,price"), each second once and in order; past days are gzipped. Its own socket, subscribed with no
    symbol filter (a filtered subscription only streams one symbol live per connection), so it can't disturb the
    feed the bots trade on."""

    def __init__(self, folder: Path, coins=("btc", "eth", "sol", "xrp")):
        self.folder = Path(folder)
        self.coins = {c.lower() for c in coins}
        self._last: dict[str, int] = {}          # symbol -> last second written
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._run, name="price-recorder", daemon=True).start()

    def _run(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            log.warning("price recorder: the websockets package is missing, not recording")
            return
        while True:
            try:
                with connect(URL, open_timeout=10) as ws:
                    ws.send(json.dumps({"action": "subscribe", "subscriptions": [ALL]}))
                    next_ping = time.time() + PING_SEC
                    while True:
                        if time.time() >= next_ping:
                            ws.send("PING")
                            next_ping = time.time() + PING_SEC
                        try:
                            msg = ws.recv(timeout=PING_SEC)
                        except TimeoutError:
                            continue
                        self._handle(msg)
            except Exception as e:  # noqa: BLE001 - network or disk: try again shortly
                log.debug("price recorder reconnecting: %s", e)
            time.sleep(3)

    def _handle(self, msg) -> None:
        if not isinstance(msg, str) or not msg.startswith("{"):
            return
        try:
            p = json.loads(msg).get("payload") or {}
        except ValueError:
            return
        sym = (p.get("symbol") or "").lower()
        if sym.split("/")[0] not in self.coins:
            return
        points = p["data"] if isinstance(p.get("data"), list) else [p] if "value" in p else []
        got = []
        for d in points:
            try:
                got.append((int(d["timestamp"]) // 1000, float(d["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        if got:
            self.add(sym, got)

    def _path(self, sym: str, sec: int) -> Path:
        day = time.strftime("%Y-%m-%d", time.gmtime(sec))
        return self.folder / sym.split("/")[0] / f"{day}.csv"

    def _last_written(self, sym: str, sec: int) -> int:
        path = self._path(sym, sec)               # after a restart: carry on from today's file
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - 64))
                tail = f.read().decode(errors="ignore").strip().splitlines()
            return int(tail[-1].split(",")[0]) if tail and tail[-1][:1].isdigit() else 0
        except (OSError, ValueError):
            return 0

    def add(self, sym: str, points: list) -> None:
        with self._lock:
            if sym not in self._last:
                self._last[sym] = self._last_written(sym, max(s for s, _ in points))
            lines: dict[Path, list] = {}
            for sec, v in sorted(points):
                if sec <= self._last[sym]:
                    continue
                self._last[sym] = sec
                lines.setdefault(self._path(sym, sec), []).append(f"{sec},{v!r}\n")
            for path, rows in lines.items():
                new = not path.exists()
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a") as f:
                    if new:
                        f.write("unix_sec,price\n")
                    f.writelines(rows)
                if new:
                    self._compress_before(path)

    @staticmethod
    def _compress_before(today: Path) -> None:
        """gzip the coin's finished days (every .csv older than today's)."""
        import gzip
        import shutil
        for old in today.parent.glob("*.csv"):
            if old.name < today.name:
                with open(old, "rb") as src, gzip.open(str(old) + ".gz.tmp", "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(str(old) + ".gz.tmp", str(old) + ".gz")
                old.unlink()


# Every coin's live price. A symbol-filtered subscription streams live prices for only one symbol per connection
# (a second coin gets its backfill, then nothing), so live prices come from this unfiltered one; the filtered
# ones (_sub) are still sent for the last minute of prices they backfill on subscribing.
ALL = {"topic": "crypto_prices_chainlink", "type": "*", "filters": ""}


def _sub(sym: str) -> dict:
    # no spaces: the server matches the filter text exactly (with a space it sends the backfill but no updates)
    return {"topic": "crypto_prices_chainlink", "type": "*", "filters": json.dumps({"symbol": sym}, separators=(",", ":"))}
