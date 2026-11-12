"""Real-time traded volume per round, from Polymarket's market WebSocket.

Gamma's `volume` for a running 5-minute round lags by minutes (it said $2 three minutes into a round that had traded
over $1,000), and the data API's live-volume answers differ from server to server. The CLOB market channel instead
pushes every trade as it happens (`last_trade_price`: price and size), so one socket for the whole app adds them up
per round. Rounds are subscribed while they're still upcoming (the bots ask for the current and the next round), so
the count starts before the round does; a round first seen mid-way (e.g. after an app restart) is marked partial.

Needs the `websockets` package (installed with the live-trading SDK). Without it, volume() returns None and the page
falls back to Gamma's number.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
KEEP_SEC = 3600            # forget a round this long after it ended
PING_SEC = 10

log = logging.getLogger("volume")


class VolumeFeed:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._token_slug: dict[str, str] = {}       # outcome token -> round slug
        self._rounds: dict[str, dict] = {}          # slug -> {"usd", "shares", "trades", "since", "start", "end"}
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        try:
            from websockets.sync.client import connect  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False

    def watch(self, r) -> None:
        """Count round r's trades from now on (no-op when already watched)."""
        if not self.available or r is None or r.slug in self._rounds:
            return
        now = self.clock()
        with self._lock:
            self._rounds[r.slug] = {"usd": 0.0, "shares": 0.0, "trades": 0, "since": now, "start": r.start,
                                    "end": r.end, "partial": now > r.start + 2}
            for t in (r.up_token, r.down_token):
                self._token_slug[t] = r.slug
            ws = self._ws
        if ws is not None:
            try:
                ws.send(json.dumps({"assets_ids": [r.up_token, r.down_token], "operation": "subscribe"}))
            except Exception:  # noqa: BLE001 - the reader thread reconnects with every token
                pass
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="volume-feed", daemon=True)
            self._thread.start()

    def volume(self, slug: str) -> Optional[dict]:
        """{"usd", "shares", "trades", "since", "partial"} for a watched round, else None."""
        with self._lock:
            v = self._rounds.get(slug)
            return dict(v) if v else None

    # --- the socket ---------------------------------------------------------------------------------------
    def _run(self) -> None:
        from websockets.sync.client import connect
        while True:
            try:
                with connect(URL, open_timeout=10) as ws:
                    with self._lock:
                        tokens = list(self._token_slug)
                        self._ws = ws
                    ws.send(json.dumps({"assets_ids": tokens, "type": "market"}))
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
                log.debug("volume feed reconnecting: %s", e)
            with self._lock:
                self._ws = None
                for v in self._rounds.values():        # trades during the gap are missed
                    if v["start"] <= self.clock() < v["end"]:
                        v["partial"] = True
            time.sleep(3)

    def _handle(self, msg) -> None:
        if not isinstance(msg, str) or not msg[:1] in "[{":
            return                                      # PONG
        try:
            data = json.loads(msg)
        except ValueError:
            return
        with self._lock:
            for ev in data if isinstance(data, list) else [data]:
                if not isinstance(ev, dict) or ev.get("event_type") != "last_trade_price":
                    continue
                v = self._rounds.get(self._token_slug.get(ev.get("asset_id"), ""))
                if v is None:
                    continue
                try:
                    size, price = float(ev["size"]), float(ev["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                v["shares"] += size
                v["usd"] += size * price
                v["trades"] += 1

    def _prune(self) -> None:
        now = self.clock()
        with self._lock:
            old = {s for s, v in self._rounds.items() if now > v["end"] + KEEP_SEC}
            for s in old:
                del self._rounds[s]
            self._token_slug = {t: s for t, s in self._token_slug.items() if s not in old}
