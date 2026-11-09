"""A fake Polymarket with a hand-driven clock, so the engine can be tested round by round without the network."""
from __future__ import annotations

from poly_bot.markets import Round


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakePM:
    """5-minute rounds starting every 300 s. results[slug] = "up"/"down" once a round has resolved."""

    def __init__(self, clock: Clock, length: int = 300, up: float = 0.50, down: float = 0.51, depth: float = 1e6):
        self.clock, self.length = clock, length
        self.prices = {"up": up, "down": down}
        self.depth = depth
        self.results: dict[str, str] = {}
        self.missing = set()          # (coin, timeframe) without a series

    def _round(self, start: float) -> Round:
        slug = f"btc-updown-5m-{int(start)}"
        res = self.results.get(slug)
        return Round(slug=slug, title=f"Round {int(start)}", start=start, end=start + self.length,
                     up_token=f"{slug}:up", down_token=f"{slug}:down", accepting=res is None and self.clock() < start + self.length,
                     closed=res is not None, result=res)

    def series_id(self, coin, timeframe):
        if (coin, timeframe) in self.missing:
            raise ValueError(f"Polymarket has no {timeframe} Up or Down rounds for {coin}")
        return "1"

    def current_round(self, coin, timeframe):
        start = self.clock() // self.length * self.length
        return self._round(start), self._round(start + self.length)

    def round(self, slug):
        return self._round(float(slug.rsplit("-", 1)[1]))

    def last_result(self, coin, timeframe):
        done = sorted(self.results.items(), key=lambda kv: int(kv[0].rsplit("-", 1)[1]))
        return done[-1] if done else None

    def book(self, token):
        side = token.rsplit(":", 1)[1]
        p = self.prices[side]
        return {"asks": [] if p is None else [(p, self.depth), (min(0.99, p + 0.01), self.depth)], "bids": []}

    def best_asks(self, r):
        return dict(self.prices)

    def midpoint(self, token):
        return 0.5
