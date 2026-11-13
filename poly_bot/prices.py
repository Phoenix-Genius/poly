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
import threading
import time
from typing import Optional

from .markets import MarketError, get_json, iso

URL = "wss://ws-live-data.polymarket.com"
OPEN_API = "https://polymarket.com/api/crypto/crypto-price"
KEEP_SEC = 15 * 60
PING_SEC = 5
FRESH_SEC = 15                 # a price older than this isn't "live" any more
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

    # --- the socket ---------------------------------------------------------------------------------------
    def _run(self) -> None:
        from websockets.sync.client import connect
        while True:
            try:
                with connect(URL, open_timeout=10) as ws:
                    with self._lock:
                        subs = [_sub(s) for s in self._symbols]
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


def _sub(sym: str) -> dict:
    # no spaces: the server matches the filter text exactly (with a space it sends the backfill but no updates)
    return {"topic": "crypto_prices_chainlink", "type": "*", "filters": json.dumps({"symbol": sym}, separators=(",", ":"))}
