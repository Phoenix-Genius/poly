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


# --- final volume of finished rounds, for the History table ------------------------------------------------------
SETTLED_SEC = 600          # ask for a round's volume this long after it ended (allows for late counting)
FETCH_GAP_SEC = 0.2        # between Gamma requests while filling in old rounds


class RoundVolumes:
    """Each finished round's total traded volume (USDC, Polymarket.final_volume()), kept in
    data/round_volumes.json as {slug: usd} and shared by every bot. get() answers from the file at once; rounds it
    doesn't know yet are looked up in the background, so a page refresh shows them a moment later."""

    def __init__(self, pm, path, clock=time.time):
        self.pm, self.path, self.clock = pm, path, clock
        self._lock = threading.Lock()
        self._known: dict[str, float] = {}
        self._wanted: set[str] = set()
        self._retry: dict[str, float] = {}         # slug -> don't ask Gamma again before this time
        self._thread: Optional[threading.Thread] = None
        try:
            self._known = self._load(json.loads(path.read_text()))
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    @staticmethod
    def _load(data: dict) -> dict:
        return {str(k): float(v) for k, v in data.items() if float(v) > 0}

    def get(self, slugs) -> dict[str, float]:
        """{slug: usd} for the rounds already known; the others are fetched in the background."""
        now = self.clock()
        with self._lock:
            out = {s: self._known[s] for s in slugs if s in self._known}
            new = {s for s in slugs if s and s not in self._known and self._retry.get(s, 0) <= now}
            self._wanted |= new
            start = new and (self._thread is None or not self._thread.is_alive())
            if start:
                self._thread = threading.Thread(target=self._fill, name="round-volumes", daemon=True)
        if start:
            self._thread.start()
        return out

    def fill_now(self) -> None:
        """Look up every wanted round in this thread (tests)."""
        self._fill(gap=0)

    def _lookup(self, slug: str):
        """(value, final, end): the round's volume, whether it's the final one to keep, the round's end."""
        if hasattr(self.pm, "final_volume"):
            usd, end = self.pm.final_volume(slug)
        else:
            r = self.pm.round(slug)
            usd, end = float(r.volume or 0), r.end
        return round(usd, 2), self.clock() >= end + SETTLED_SEC and usd > 0, end   # 0 = not known yet: ask later

    def _fill(self, gap: float = FETCH_GAP_SEC) -> None:
        while True:
            with self._lock:
                if not self._wanted:
                    return
                slug = self._wanted.pop()
            try:
                value, final, end = self._lookup(slug)
                usd = value
            except Exception as e:  # noqa: BLE001 - network: try again later
                log.debug("%s of %s: %s", type(self).__name__, slug, e)
                final, usd, end = False, None, 0
            with self._lock:
                if final:
                    self._known[slug] = value
                    self._save()
                else:
                    self._retry[slug] = self.clock() + 120 if usd is None or self.clock() < end + SETTLED_SEC \
                        else self.clock() + 900
            if gap:
                time.sleep(gap)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._known))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("Couldn't save round volumes: %s", e)


class RoundPrices(RoundVolumes):
    """Each finished round's official [Price To Beat, final price] (Polymarket.settled_prices(): the Chainlink TWAP
    the round settled on), kept in data/round_prices.json as {slug: [beat, final]}, looked up like the volumes."""

    @staticmethod
    def _load(data: dict) -> dict:
        return {str(k): [float(v[0]), float(v[1])] for k, v in data.items() if isinstance(v, list) and len(v) == 2}

    def _lookup(self, slug: str):
        beat, final, end = self.pm.settled_prices(slug)
        ok = beat is not None and final is not None
        return ([beat, final] if ok else None), ok, end
