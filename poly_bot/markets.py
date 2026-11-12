"""Polymarket's crypto Up or Down rounds: finding the current round, its order book, and how it ended.

Public read-only APIs, standard library only:
- Gamma (https://gamma-api.polymarket.com): series and events. Each coin and timeframe is a series, e.g.
  btc-up-or-down-5m; each round is an event with one market whose outcomes are ["Up", "Down"].
- CLOB (https://clob.polymarket.com): the order book of each outcome's token.

A round resolves from Chainlink's price feed, 1 to 5 minutes after it ends: the market closes and its
outcomePrices become ["1", "0"] (Up won) or ["0", "1"] (Down won).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .strategy import DEFAULT_FEE_RATE

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SERIES_TF = {"5m": "5m", "15m": "15m", "1h": "hourly", "4h": "4h", "daily": "daily"}   # our name -> slug part
USER_AGENT = "poly-martingale-bot"


class MarketError(RuntimeError):
    """Polymarket couldn't be reached or answered something unexpected (the bot retries)."""


def get_json(url: str, params: Optional[dict] = None, timeout: float = 10.0, tries: int = 2):
    """GET a JSON answer; a server error (5xx) or a timeout is retried once."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            err = e
            if e.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            err = e
        if attempt + 1 < tries:
            time.sleep(0.5)
    raise MarketError(f"Polymarket didn't answer ({url.split('?')[0]}): {err}") from err


def parse_time(s: Optional[str]) -> float:
    if not s:
        return 0.0
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


@dataclass
class Round:
    slug: str
    title: str
    start: float                 # unix seconds
    end: float
    up_token: str
    down_token: str
    accepting: bool              # the market takes orders
    closed: bool
    result: Optional[str]        # "up" / "down" once resolved, else None
    fee_rate: float = DEFAULT_FEE_RATE
    fee_exponent: float = 1.0
    min_size: float = 5.0
    condition_id: str = ""        # the market's id on chain; live trading redeems winnings with it
    volume: float = 0.0           # USDC traded in this round so far (Gamma; lags the trades by a little)

    def token(self, side: str) -> str:
        return self.up_token if side == "up" else self.down_token

    @property
    def url(self) -> str:
        return f"https://polymarket.com/event/{self.slug}"


def parse_round(event: dict) -> Round:
    """A Gamma event -> Round."""
    m = (event.get("markets") or [{}])[0]
    outcomes = [o.lower() for o in json.loads(m.get("outcomes") or "[]")]
    tokens = json.loads(m.get("clobTokenIds") or "[]")
    if len(tokens) != 2 or sorted(outcomes) != ["down", "up"]:
        raise MarketError(f"{event.get('slug')}: not an Up/Down market")
    by = dict(zip(outcomes, tokens))
    result = None
    prices = json.loads(m.get("outcomePrices") or "[]")
    if m.get("closed") and len(prices) == 2:
        won = [o for o, p in zip(outcomes, prices) if float(p) >= 0.99]
        result = won[0] if len(won) == 1 else None
    fees = m.get("feeSchedule") or {}
    return Round(
        slug=event.get("slug") or m.get("slug", ""),
        title=event.get("title") or m.get("question", ""),
        start=parse_time(m.get("eventStartTime") or event.get("startTime")),
        end=parse_time(m.get("endDate") or event.get("endDate")),
        up_token=by["up"], down_token=by["down"],
        accepting=bool(m.get("acceptingOrders")) and not m.get("closed"),
        closed=bool(m.get("closed")),
        result=result,
        fee_rate=float(fees.get("rate", DEFAULT_FEE_RATE)) if m.get("feesEnabled", True) else 0.0,
        fee_exponent=float(fees.get("exponent", 1.0)),
        min_size=float(m.get("orderMinSize") or 5),
        condition_id=m.get("conditionId") or "",
        volume=float(m.get("volumeNum") or m.get("volume") or event.get("volume") or 0),
    )


class Polymarket:
    """Read access to the rounds of one or many series. Thread-safe; series ids are cached."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._series: dict[tuple, Optional[str]] = {}
        self._results: dict[str, str] = {}       # slug -> result, final once known
        self._lock = threading.Lock()
        from .volume import VolumeFeed
        self.volumes = VolumeFeed(clock)         # real-time traded volume per round (one socket for every bot)

    def series_id(self, coin: str, timeframe: str) -> str:
        key = (coin.upper(), timeframe)
        with self._lock:
            if key in self._series and self._series[key]:
                return self._series[key]
        slug = f"{coin.lower()}-up-or-down-{SERIES_TF[timeframe]}"
        found = get_json(f"{GAMMA}/series", {"slug": slug})
        sid = str(found[0]["id"]) if found else None
        with self._lock:
            self._series[key] = sid
        if not sid:
            raise ValueError(f"Polymarket has no {timeframe} Up or Down rounds for {coin}")
        return sid

    def open_rounds(self, coin: str, timeframe: str, limit: int = 3) -> list[Round]:
        """The rounds that haven't ended yet, soonest first (the current one, then the next ones)."""
        events = get_json(f"{GAMMA}/events", {"series_id": self.series_id(coin, timeframe),
                                               "end_date_min": iso(self.clock()), "closed": "false",
                                               "order": "endDate", "ascending": "true", "limit": limit})
        out = []
        for e in events:
            try:
                out.append(parse_round(e))
            except (MarketError, KeyError, ValueError):
                continue
        return sorted(out, key=lambda r: r.end)

    def current_round(self, coin: str, timeframe: str) -> tuple[Optional[Round], Optional[Round]]:
        """(the round running now or None, the next round or None)."""
        now = self.clock()
        current = nxt = None
        for r in self.open_rounds(coin, timeframe):
            if r.start <= now < r.end and current is None:
                current = r
            elif r.start > now and nxt is None:
                nxt = r
        return current, nxt

    def round(self, slug: str) -> Round:
        """A round by slug, with its result as soon as Polymarket knows it.

        Gamma (the public market data) marks a round resolved minutes after the trading server (CLOB) does, so
        when Gamma has no result yet the CLOB's winner flag is asked too.
        """
        found = get_json(f"{GAMMA}/events", {"slug": slug})
        if not found:
            raise MarketError(f"round {slug} not found")
        r = parse_round(found[0])
        if not r.result and r.condition_id and self.clock() >= r.end:
            r.result = self.clob_result(r)
        if r.result:
            with self._lock:
                self._results[slug] = r.result
        return r

    def clob_result(self, r: Round) -> Optional[str]:
        """"up"/"down" when the trading server has closed the round with a winner, else None."""
        try:
            m = get_json(f"{CLOB}/markets/{r.condition_id}")
        except MarketError:
            return None
        if not m.get("closed"):
            return None
        winners = [t for t in m.get("tokens") or [] if t.get("winner")]
        if len(winners) != 1:
            return None
        tok = str(winners[0].get("token_id"))
        return "up" if tok == r.up_token else ("down" if tok == r.down_token else None)

    def last_result(self, coin: str, timeframe: str) -> Optional[tuple[str, str]]:
        """(slug, "up"/"down") of the most recent round that has resolved, or None."""
        events = get_json(f"{GAMMA}/events", {"series_id": self.series_id(coin, timeframe), "closed": "true",
                                               "end_date_max": iso(self.clock()), "order": "endDate",
                                               "ascending": "false", "limit": 3})
        for e in events:
            try:
                r = parse_round(e)
            except (MarketError, KeyError, ValueError):
                continue
            if r.result:
                return r.slug, r.result
        return None

    def book(self, token_id: str) -> dict:
        """{"asks": [(price, size)...] cheapest first, "bids": [...] best first}."""
        b = get_json(f"{CLOB}/book", {"token_id": token_id})
        asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks") or [])
        bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids") or []), reverse=True)
        return {"asks": asks, "bids": bids}

    def best_asks(self, r: Round) -> dict:
        """Best ask of each side: {"up": 0.51, "down": 0.50} (None for an empty side)."""
        out = {}
        for side in ("up", "down"):
            asks = self.book(r.token(side))["asks"]
            out[side] = asks[0][0] if asks else None
        return out

    def midpoint(self, token_id: str) -> Optional[float]:
        try:
            return float(get_json(f"{CLOB}/midpoint", {"token_id": token_id}).get("mid"))
        except (MarketError, TypeError, ValueError):
            return None


def available(pm: Polymarket, coin: str, timeframe: str) -> bool:
    try:
        pm.series_id(coin, timeframe)
        return True
    except ValueError:
        return False
