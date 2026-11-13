"""Monte Carlo simulation of the martingale ladder: what these settings do over many bets.

Pure logic, no I/O. Two ways to get each bet's price and result:

- **real** (the default): replay Polymarket's past rounds of the bot's coin and timeframe (history.py: each round's
  result and Up price minute by minute) through the bot's own entry rule for its Bet mode, so every bet has the
  price the bot would really have paid then, and wins only if that round really went that way:
    side: the side the Side mode picks, at the first moment in the Entry window its price is within Min/Max price;
    hit:  the first side to reach the Hit price, filled HIT_FILL of the way from the Hit price to the next recorded
          price (prices jump between the bot's 2 s checks; calibrated on the paper bots' real fills), within Pay
          at most;
    time: the leader when Time left is reached, if within Leader at least / Pay at most (else the first later
          moment it is, until 5 s before the end);
    move: the first second the coin itself (Binance 1 s candles, history.CoinPaths) is Move ($) from the round's
          start, the way it moved, at the token's price then (within Pay at most).
  The price paid is the path's price plus SPREAD (the ask is a little above the mid). A bet is only placed once the
  previous one is paid out (SETTLE_SEC after its round ends), like the bot. Runs stitch random half-day stretches
  of the history together, so each run sees real streaks but runs differ.
- **fair**: every bet at one price (the ladder table's), winning with that chance (or the Win chance given): a
  market the bot can't predict.

Sizing, fees, the 5-share minimum, Recover over N wins, Steps to survive, Max losses in a row and the budget are the
bot's own (strategy.py). Not modelled: walking the order book beyond SPREAD, Until earn. A ladder end restarts at
step 1 with what's left, like "When the ladder ends = reset".
"""
from __future__ import annotations

import random
import statistics
from types import SimpleNamespace
from typing import Optional

from .strategy import ROUND_SECONDS, buy_cap, choose_side, cost_of, entry_span, fee_per_share, shares_for, streak_sizing

MAX_WORK = 1_000_000          # runs x bets per request, so one click can't freeze the app
SAMPLE_PATHS = 5              # P&L curves returned for the chart
LOG_ROWS = 1000               # bets of run 1 listed one by one
PATH_POINTS = 200             # points per curve (downsampled)
SPREAD = 0.01                 # the ask is about a cent above the path's (mid) price
HIT_FILL = 0.6                # hit mode: how far from the Hit price to the next recorded price the fill lands
SETTLE_SEC = 170              # a round's result comes about this long after it ends (from the bots' history)
BLOCK_SEC = 12 * 3600         # runs are stitched from random stretches of this length


# --- entries: where the bot would have bought in one past round ---------------------------------------------------
def _price_at(h: list, t: float) -> Optional[float]:
    """The Up price at t seconds into the round (linear between points; the last point before the round = start)."""
    if not h:
        return None
    if t <= h[0][0]:
        return h[0][1]
    for (t0, p0), (t1, p1) in zip(h, h[1:]):
        if t0 <= t <= t1:
            return p0 + (p1 - p0) * (t - t0) / (t1 - t0) if t1 > t0 else p1
    return h[-1][1]


def _ask(mid: float) -> float:
    """The best ask for a side whose (mid) price is `mid`, on Polymarket's 1-cent ticks."""
    return min(0.99, max(0.01, round(mid + SPREAD, 2)))


def entry_for(cfg, rnd: dict, side: Optional[str] = None) -> Optional[tuple]:
    """(seconds into the round, side, price paid) where the bot would have bought in this round, or None.
    side mode needs the `side` the Side mode picked; hit and time mode pick it themselves."""
    length = rnd["end"] - rnd["start"]
    opens, closes = entry_span(cfg, length)
    h = rnd["h"]
    times = [opens] + [t for t, _ in h if opens < t <= closes]
    mode = getattr(cfg, "entry_mode", "side")
    cap = buy_cap(cfg)
    if mode == "hit":
        # Prices jump: the bot (checking every 2 s) fills somewhere between the Hit price and the next recorded
        # (1-minute) price. HIT_FILL of the way, from the paper bots' real hit fills (0.83 on average for 0.75).
        prev = None
        for t in times:
            up = _price_at(h, t)
            asks = {"up": _ask(up), "down": _ask(1 - up)}
            for a, s in sorted(((a, s) for s, a in asks.items()), reverse=True):
                if a < cfg.hit_price - 1e-9:
                    continue
                fill = a if prev is None or prev[s] >= cfg.hit_price else \
                    round(cfg.hit_price + HIT_FILL * (a - cfg.hit_price), 2)
                if fill <= cap + 1e-9:
                    return t, s, fill                                    # (above Pay at most: the bot waits)
            prev = asks
        return None
    if mode == "move":
        # the coin's own path (rnd["coin"], from Binance candles): the first moment it is Move ($) from the start,
        # then the token's price at that moment (the bot waits while that side costs more than Pay at most)
        coin = rnd.get("coin")
        if not coin:
            return None
        for t, hi, lo in coin["k"]:
            if t < opens or t > closes:
                continue
            side = "up" if hi >= cfg.move_usd else "down" if -lo >= cfg.move_usd else None
            if hi >= cfg.move_usd and -lo >= cfg.move_usd:          # both within one candle: the bigger move
                side = "up" if hi >= -lo else "down"
            if side is None:
                continue
            # the token's price at the next recorded point (pessimistic): the market reprices within seconds of
            # the coin's move, and 1-minute points can't show how fast; between the two points it looked +10
            # points better, at the next point +2 to +3 (BTC 5m, Nov 6-13). Re-check against real move fills.
            later = [p for tp, p in h if tp >= t]
            up = later[0] if later else h[-1][1]
            a = _ask(up if side == "up" else 1 - up)
            if a <= cap + 1e-9:
                return t, side, a
        return None
    if mode == "time":
        for t in times:
            up = _price_at(h, t)
            s = "up" if up > 0.5 else "down" if up < 0.5 else None
            if s is None:
                continue
            a = _ask(up if s == "up" else 1 - up)
            if a >= cfg.time_min_price - 1e-9 and a <= cap + 1e-9:
                return t, s, a
        return None
    for t in times:                                         # side mode: the picked side within Min/Max price
        up = _price_at(h, t)
        a = _ask(up if side == "up" else 1 - up)
        if cfg.min_price - 1e-9 <= a <= cfg.max_price + 1e-9:
            return t, side, a
    return None


class RealFeed:
    """Hands out one run's bets from the past rounds, in order, with random jumps between half-day stretches."""

    def __init__(self, cfg, rounds: list, rng: random.Random):
        self.cfg, self.rounds, self.rng = cfg, rounds, rng
        self.side_mode = getattr(cfg, "entry_mode", "side") == "side"
        length = rounds[0]["end"] - rounds[0]["start"] if rounds else 300
        self.block = max(20, int(BLOCK_SEC // length))
        if not self.side_mode:   # hit and time mode: the entry doesn't depend on the ladder, so work it out once
            self.fixed = [entry_for(cfg, r) for r in rounds]
            if not any(self.fixed):
                raise ValueError("No past round had an entry with these settings (e.g. the price never reached the "
                                 "Hit price within Pay at most): nothing to simulate.")
        self.skipped = 0
        self._jump()

    def _jump(self) -> None:
        self.i = self.rng.randrange(len(self.rounds))
        self.left_in_block = self.block
        self.free_at = float("-inf")
        self.last_result = None

    def next(self, lad) -> tuple[float, bool, dict]:
        """(price, won, info) of the next bet, after the previous one is paid out."""
        for _ in range(len(self.rounds) * 3):
            if self.i >= len(self.rounds) or self.left_in_block <= 0:
                self._jump()
            k = self.i
            r = self.rounds[k]
            self.i += 1
            self.left_in_block -= 1
            if self.side_mode:
                p = _price_at(r["h"], 0)
                side = choose_side(self.cfg, lad, self.last_result, {"up": _ask(p), "down": _ask(1 - p)}, self.rng)
                e = entry_for(self.cfg, r, side)
            else:
                e = self.fixed[k]
            last, self.last_result = self.last_result, r["result"]
            if e is None:                      # no entry in this round with these settings
                self.skipped += 1
                continue
            if r["start"] + e[0] < self.free_at:
                continue                       # still waiting for the previous bet's result
            t, side, price = e
            self.free_at = r["end"] + SETTLE_SEC
            return price, r["result"] == side, {"round": r["start"], "side": side, "at": round(t)}
        raise ValueError("No past round had an entry with these settings: nothing to simulate.")


class FairFeed:
    def __init__(self, price: float, p_win: float, rng: random.Random):
        self.price, self.p_win, self.rng = price, p_win, rng
        self.skipped = 0

    def next(self, lad) -> tuple[float, bool, dict]:
        return self.price, self.rng.random() < self.p_win, {}


# --- the ladder over many runs ----------------------------------------------------------------------------------
def simulate(cfg, price: float, bankroll: float, bets: int = 1000, runs: int = 1000,
             win_chance: Optional[float] = None, seed: Optional[int] = None,
             rounds: Optional[list] = None) -> dict:
    """Run the ladder `runs` times for up to `bets` bets each, starting with `bankroll` USDC to lose.
    rounds: past rounds (history.py) to replay; None = a fair market at `price` (win chance = price or win_chance)."""
    if bets < 1 or runs < 1:
        raise ValueError("Bets and runs must be at least 1")
    if bets * runs > MAX_WORK:
        raise ValueError(f"Too much at once: bets x runs must be at most {MAX_WORK:,}")
    if bankroll <= 0:
        raise ValueError("Enter a budget above $0 to simulate")
    if not 0.02 <= price <= 0.99:
        raise ValueError("Price must be between 0.02 and 0.99")
    p_win = price if win_chance is None else win_chance
    if not 0 <= p_win <= 1:
        raise ValueError("Win chance must be between 0% and 100%")
    rng = random.Random(seed)
    rw = int(getattr(cfg, "recover_wins", 1) or 1) if cfg.sizing == "recover" else 1

    bases: dict = {}                       # Steps to survive: the streak's base per (whole) dollar left
    terms: dict = {}                       # (base, step, owed, wins_left, price) -> (shares, cost)

    def sizing_for(step: int, base: float, left: float, carried: bool):
        if not cfg.target_steps:
            return cfg, 0.0
        if (step == 1 and not carried) or not base:
            key = int(left)
            if key not in bases:
                bases[key] = streak_sizing(cfg, 1, 0.0, float(key))
            return bases[key]
        return streak_sizing(cfg, step, base, left, carried)

    def bet_terms(sizing, base: float, step: int, owed: float, wins_left: int, p: float) -> tuple[float, float]:
        key = (base, step, round(owed, 2), wins_left, p)    # base 0 = the settings' own sizing
        if key not in terms:
            shares = shares_for(sizing, step, owed, p, wins_left=wins_left)
            terms[key] = (shares, cost_of(shares, p))
        return terms[key]

    finals, ends_per_run, streaks, drawdowns, bet_counts = [], [], [], [], []
    n_bets = n_wins = 0
    sum_price = sum_need = 0.0
    log: list[dict] = []
    broke = skipped = 0
    paths = []
    every = max(1, bets // PATH_POINTS)
    for run in range(runs):
        feed = RealFeed(cfg, rounds, rng) if rounds is not None else FairFeed(price, p_win, rng)
        lad = SimpleNamespace(rounds_bet=0, last_side="", last_won=None)     # what the Side mode looks at
        pnl = peak = worst_dd = 0.0
        step, owed, wins_left, base = 1, 0.0, 0, 0.0
        streak = longest = ends = done = 0
        path = [0.0] if run < SAMPLE_PATHS else None
        pending = None
        while done < bets:
            if pending is None:
                pending = feed.next(lad)
            p, won, info = pending
            left = bankroll + pnl
            sizing, base = sizing_for(step, base, left, owed > 0)
            logging = run == 0 and len(log) < LOG_ROWS
            fits = sizing is not None
            if fits:
                shares, cost = bet_terms(sizing, base, step, owed, wins_left, p)
                fits = cost <= left + 1e-9 and not (cfg.max_steps and step > cfg.max_steps)
            if not fits:
                if step == 1 and owed <= 0:    # even the first bet doesn't fit any more: out of money
                    broke += 1
                    if logging:
                        log.append({"event": "broke", "after": done})
                    break
                ends += 1
                if logging:
                    log.append({"event": "ladder_end", "after": done})
                step, owed, wins_left, base = 1, 0.0, 0, 0.0
                continue                       # the same round, now at step 1
            pending = None
            done += 1
            n_bets += 1
            n_wins += won
            sum_price += p
            sum_need += p + fee_per_share(p)
            got = shares - cost if won else -cost
            if logging:
                log.append({"n": done, "step": step, "price": p, "shares": shares, "cost": round(cost, 2),
                            "fee": round(shares * fee_per_share(p), 4), "win": won,
                            "payout": shares if won else 0.0, "pnl": round(got, 2), "total": round(pnl + got, 2),
                            "left": round(left + got, 2), **info})
            pnl += got
            lad.rounds_bet += 1
            lad.last_side, lad.last_won = info.get("side", ""), won
            if won:
                step, streak = 1, 0
                n = wins_left or rw
                if n <= 1 or owed <= 0:
                    owed, wins_left, base = 0.0, 0, 0.0
                else:
                    owed, wins_left = owed - owed / n, n - 1
            else:
                step += 1
                owed += cost
                wins_left = 0
                streak += 1
                longest = max(longest, streak)
            peak = max(peak, pnl)
            worst_dd = max(worst_dd, peak - pnl)
            if path is not None and done % every == 0:
                path.append(round(pnl, 2))
        skipped += feed.skipped
        finals.append(pnl)
        ends_per_run.append(ends)
        streaks.append(longest)
        drawdowns.append(worst_dd)
        bet_counts.append(done)
        if path is not None:
            paths.append(path)

    s = sorted(finals)
    pct = lambda q: s[min(len(s) - 1, int(q * len(s)))]  # noqa: E731
    sizing0, base0 = sizing_for(1, 0.0, bankroll, False)
    first = None
    if sizing0 is not None:
        sh, c = bet_terms(sizing0, base0, 1, 0.0, 0, price)
        first = {"cost": c, "win": sh - c, "loss": -c, "fits": c <= bankroll}
    avg_price = sum_price / n_bets if n_bets else price
    out = {
        "mode": "real" if rounds is not None else "fair",
        "runs": runs, "bets": bets, "bankroll": bankroll, "price": avg_price if rounds is not None else price,
        "p_win": n_wins / n_bets if rounds is not None and n_bets else p_win,
        "break_even": sum_need / n_bets if n_bets else price + fee_per_share(price), "first_bet": first,
        "average": statistics.fmean(finals), "median": statistics.median(finals),
        "p5": pct(0.05), "p95": pct(0.95), "best": s[-1], "worst": s[0],
        "profit_chance": sum(f > 0 for f in finals) / runs,
        "ladder_end_chance": sum(e > 0 for e in ends_per_run) / runs,
        "ladder_ends_avg": statistics.fmean(ends_per_run),
        "broke_chance": broke / runs,
        "longest_streak_median": statistics.median(streaks), "longest_streak_max": max(streaks),
        "longest_streak_run1": streaks[0],
        "drawdown_median": statistics.median(drawdowns),
        "bets_avg": statistics.fmean(bet_counts),
        "histogram": histogram(finals),
        "paths": paths, "path_every": every,
        "log": log, "log_bets": bet_counts[0],
    }
    if rounds is not None:
        out["history"] = history_summary(cfg, rounds)
    return out


def history_summary(cfg, rounds: list) -> dict:
    """What the replay was based on: the rounds' span, and how the entries' prices and results came out per round
    (each past round once, whatever the ladder did)."""
    side = getattr(cfg, "entry_mode", "side") == "side"
    pairs = [(r, entry_for(cfg, r, getattr(cfg, "first_side", "up")) if side else entry_for(cfg, r)) for r in rounds]
    es = [(r, e) for r, e in pairs if e]
    wins = sum(1 for r, e in es if r["result"] == e[1])
    prices = sorted(e[2] for _, e in es)
    q = lambda f: prices[min(len(prices) - 1, int(f * len(prices)))] if prices else None  # noqa: E731
    return {"rounds": len(rounds), "from": rounds[0]["start"], "to": rounds[-1]["end"],
            "with_entry": len(es), "entry_wins": wins,
            "price_low": q(0.05), "price_median": q(0.5), "price_high": q(0.95)}


def histogram(values: list[float], bins: int = 24) -> list[dict]:
    """Equal-width bins over [min, max]: [{"lo", "hi", "count"}]."""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [{"lo": lo, "hi": hi, "count": len(values)}]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / width))] += 1
    return [{"lo": lo + i * width, "hi": lo + (i + 1) * width, "count": c} for i, c in enumerate(counts)]
