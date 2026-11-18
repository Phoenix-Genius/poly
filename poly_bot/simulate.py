"""Monte Carlo simulation of the martingale ladder: what these settings do over many bets.

Pure logic, no I/O. Two ways to get each bet's price and result:

- **real** (the default): replay Polymarket's past rounds of the bot's coin and timeframe (history.py: each round's
  result and Up price minute by minute) through the bot's own entry rule for its Bet mode, so every bet has the
  price the bot would really have paid then, and wins only if that round really went that way:
    side: the side the Side mode picks, at the first moment in the Entry window its price is within Min/Max price;
    conditions: every COND_STEP seconds, strategy.sides_allowed (the bot's own rule) on the time left, where the
          coin is then (Binance 1 s candles, history.CoinPaths "c") and the token's price then; the leader (dearer
          side) when both sides pass, within Price to. Priced at the next recorded point when Move is a condition
          (the market reprices within seconds of the coin's move), HIT_FILL of the way from Price from to the next
          recorded price when Price was just reached (prices jump between the bot's 2 s checks; calibrated on the
          paper bots' real hit fills).
  The price paid is the path's price plus SPREAD (the ask is a little above the mid). A bet is only placed once the
  previous one is paid out, like the bot: SETTLE_SEC after its round ends with Result = official, FAST_SETTLE_SEC
  with Result = fast unless the round ended close (settle_after()). Runs stitch random half-day stretches
  of the history together, so each run sees real streaks but runs differ.
  Then the **price gap** is added: 1-minute history can't show how fast prices move, so the replay's prices come
  out cheaper than what the bots really pay. price_gap() measures it on the owner's own bots' real bets (same
  mode, same rounds: real price paid minus the replay's), falling back to default_gap() (from DEFAULT_GAP, measured
  2026-11-14 on BTC 5m in the old modes: side -0.2c, hit +4.6c, time +4.0c, move +13.7c) until there are
  MIN_GAP_BETS of them.
- **fair** (tests only, not offered on the page since 2026-11-14: "everything ... based on real history"): every
  bet at one price, winning with that chance.

Sizing, fees, the 5-share minimum, Recover over N wins, Steps to survive, Max losses in a row and the budget are the
bot's own (strategy.py). Not modelled: walking the order book beyond SPREAD, Until earn. A ladder end restarts at
step 1 with what's left, like "When the ladder ends = reset".
"""
from __future__ import annotations

import random
import statistics
from types import SimpleNamespace
from typing import Optional

from .strategy import (ROUND_SECONDS, buy_cap, choose_side, cost_of, entry_span, fee_per_share, leader_of, sides_allowed,
                       shares_for,
                       streak_sizing)

MAX_WORK = 1_000_000          # runs x bets per request, so one click can't freeze the app
SAMPLE_PATHS = 5              # P&L curves returned for the chart
LOG_ROWS = 1000               # bets of run 1 listed one by one
PATH_POINTS = 200             # points per curve (downsampled)
SPREAD = 0.01                 # the ask is about a cent above the path's (mid) price
HIT_FILL = 0.6                # just reached Price from: how far from it to the next recorded price the fill lands
SETTLE_SEC = 170              # a round's result comes about this long after it ends (from the bots' history)
FAST_SETTLE_SEC = 30          # Result = fast: the bot's own TWAP and the market agree about this long after the end
FAST_CLOSE = (0.15, 0.85)     # ... unless the round ended close (Up price still in here near the end, no coin path)
BLOCK_SEC = 12 * 3600         # runs are stitched from random stretches of this length
# what the bots really paid above the replay's price, per Bet mode (BTC 5m paper bots' bets, 2026-11-14; hit, time and
# move were the Bet modes the conditions replaced: a Price, Time left and Move condition alone)
DEFAULT_GAP = {"side": 0.0, "hit": 0.046, "time": 0.04, "move": 0.137}
COND_STEP = 2                 # conditions mode: seconds between checks in the replay (the bot checks every ~2 s)


def default_gap(cfg) -> float:
    """The price gap to assume before the bot's own bets measure it. conditions mode: with Price as an AND the bot
    always sees the price before buying, like the old hit mode, so its gap; else the biggest of its conditions' old
    modes (move's 13.7c comes from buying the instant the coin crosses, whatever the price)."""
    if getattr(cfg, "entry_mode", "side") != "conditions":
        return DEFAULT_GAP["side"]
    conds = cfg.conditions()
    if conds.get("price") == "and":
        return DEFAULT_GAP["hit"]
    like = {"move": "move", "price": "hit", "time": "time"}
    return max(DEFAULT_GAP[like[c]] for c in conds)


def needs_coin(cfg) -> bool:
    """Whether the replay needs the coin's own price inside each round (CoinPaths)."""
    return getattr(cfg, "entry_mode", "side") == "conditions" and "move" in cfg.conditions()


def ladder_free(cfg) -> bool:
    """Whether the replay's entry in a round is the same whatever the ladder did (worked out once per round)."""
    return getattr(cfg, "entry_mode", "side") == "conditions"
GAP_MATCH_SEC = 90            # a real bet counts if placed within this of where the replay buys
MIN_GAP_BETS = 30             # own real bets needed before the measured gap replaces DEFAULT_GAP


# --- entries: where the bot would have bought in one past round ---------------------------------------------------
def settle_after(cfg, rnd: dict) -> float:
    """Seconds after round rnd's end until the bot can bet again (its result is in)."""
    if getattr(cfg, "result_wait", "official") != "fast":
        return SETTLE_SEC
    from .engine import FAST_MARGIN
    coin = rnd.get("coin") or {}
    path = coin.get("a") or coin.get("c")
    if path and coin.get("o"):
        return SETTLE_SEC if abs(path[-1]) < FAST_MARGIN * coin["o"] else FAST_SETTLE_SEC
    p = _price_at(rnd.get("h") or [], rnd["end"] - rnd["start"] - 5)
    return SETTLE_SEC if p is not None and FAST_CLOSE[0] < p < FAST_CLOSE[1] else FAST_SETTLE_SEC


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
    side mode needs the `side` the Side mode picked; in conditions mode the conditions pick it."""
    length = rnd["end"] - rnd["start"]
    opens, closes = entry_span(cfg, length)
    h = rnd["h"]
    times = [opens] + [t for t, _ in h if opens < t <= closes]
    mode = getattr(cfg, "entry_mode", "side")
    cap = buy_cap(cfg)
    if mode == "conditions":
        return _entry_conditions(cfg, rnd, opens, closes, cap)
    for t in times:                                         # side mode: the picked side within Min/Max price
        up = _price_at(h, t)
        a = _ask(up if side == "up" else 1 - up)
        if cfg.min_price - 1e-9 <= a <= cfg.max_price + 1e-9:
            return t, side, a
    return None


def _entry_conditions(cfg, rnd: dict, opens: float, closes: float, cap: float) -> Optional[tuple]:
    h, conds = rnd["h"], cfg.conditions()
    coin = rnd.get("coin")
    if "move" in conds and not (coin and (coin.get("a") or coin.get("c"))):
        return None
    length = rnd["end"] - rnd["start"]
    step = max(COND_STEP, int(length // 300))         # long rounds: about 300 checks
    prev = None
    t = opens
    while t <= closes + 1e-9:
        up = _price_at(h, t)
        asks = {"up": _ask(up), "down": _ask(1 - up)}
        move = None
        if coin and (coin.get("a") or coin.get("c")):   # the 60 s TWAP's move, like the bot (else the close's)
            c = coin.get("a") or coin["c"]
            move = c[min(len(c) - 1, int(t // coin.get("s", 1)))]
        sides, _ = sides_allowed(cfg, length - t, move, asks)
        if sides:
            s = leader_of(sides, asks)
            later = [p for tp, p in h if tp >= t]
            nxt = later[0] if later else h[-1][1]
            if "move" in conds:          # the market reprices within seconds of the coin's move
                fill = _ask(nxt if s == "up" else 1 - nxt)
            elif "price" in conds and cfg.cond_price_min and prev is not None and prev[s] < cfg.cond_price_min - 1e-9:
                a = _ask(nxt if s == "up" else 1 - nxt)  # just reached Price from: prices jump
                fill = round(cfg.cond_price_min + HIT_FILL * max(0.0, a - cfg.cond_price_min), 2)
            else:
                fill = asks[s]
            if fill <= cap + 1e-9:
                return t, s, fill
        prev = asks
        t += step
    return None


def with_gap(e: Optional[tuple], gap: float, cap: float) -> Optional[tuple]:
    """An entry with the price gap added; None if that real price is above Price to (or Max price) (the bot wouldn't buy)."""
    if e is None or not gap:
        return e
    price = min(0.99, max(0.01, round(e[2] + gap, 2)))
    return None if price > cap + 1e-9 else (e[0], e[1], price)


def price_gap(rounds: list, bets: list) -> dict:
    """How much more the bots really paid than the replay says: bets = [{"cfg", "round" (slug), "side", "price",
    "at" (seconds into the round it was placed, or None)}]
    of the owner's bots in this Bet mode; each is compared with entry_for(its bot's settings) on the same round,
    when the replay picks the same side. {"gap", "bets", "measured"}; DEFAULT_GAP while there are too few."""
    by = {r["slug"]: r for r in rounds}
    diffs = []
    for b in bets:
        r = by.get(b["round"])
        e = entry_for(b["cfg"], r, b["side"]) if r else None
        if not e or e[1] != b["side"]:
            continue
        # a bet placed far from where the replay buys was made under other settings (the bot's mode or prices were
        # changed since): it says nothing about this mode's prices
        at = b.get("at")
        if at is not None and abs(at - e[0]) > GAP_MATCH_SEC:
            continue
        diffs.append(float(b["price"]) - e[2])
    mode = getattr(bets[0]["cfg"], "entry_mode", "side") if bets else None
    if len(diffs) >= MIN_GAP_BETS:
        return {"gap": round(statistics.fmean(diffs), 4), "bets": len(diffs), "measured": True}
    return {"gap": None, "bets": len(diffs), "measured": False, "mode": mode}


class RealFeed:
    """Hands out one run's bets from the past rounds, in order, with random jumps between half-day stretches."""

    def __init__(self, cfg, rounds: list, rng: random.Random, gap: float = 0.0, shared: Optional[dict] = None):
        """shared: one dict for every run of a simulation, so each round's entries are worked out once."""
        self.cfg, self.rounds, self.rng, self.gap, self.cap = cfg, rounds, rng, gap, buy_cap(cfg)
        shared = {} if shared is None else shared
        self.side_mode = not ladder_free(cfg)
        length = rounds[0]["end"] - rounds[0]["start"] if rounds else 300
        self.block = max(20, int(BLOCK_SEC // length))
        if not self.side_mode:   # conditions mode: the entry doesn't depend on the ladder, so work it out once
            if "raw" not in shared:
                shared["raw"] = [entry_for(cfg, r) for r in rounds]   # the replay's own price (shown as "Best")
                shared["fixed"] = [with_gap(e, gap, self.cap) for e in shared["raw"]]
            self.raw, self.fixed = shared["raw"], shared["fixed"]
            if not any(self.fixed):
                raise ValueError("No past round had an entry with these settings (the conditions were never all met "
                                 "within Price to): nothing to simulate.")
        self._by_side: dict = shared.setdefault("by_side", {})
        self.skipped = 0
        self._jump()

    def _jump(self) -> None:
        self.i = self.rng.randrange(len(self.rounds))
        self.left_in_block = self.block
        self.free_at = float("-inf")
        self.last_result = None
        self.last_bet_k = None              # the previous bet's round, for History's "(N missed)"; none after a jump

    def next(self, lad) -> tuple[float, bool, dict]:
        """(price, won, info) of the next bet, after the previous one is paid out."""
        for _ in range(len(self.rounds) * 3):
            if self.i >= len(self.rounds) or self.left_in_block <= 0:
                self._jump()
            k = self.i
            r = self.rounds[k]
            self.i += 1
            self.left_in_block -= 1
            if self.side_mode:                 # the side depends on the ladder or the last result
                p = _price_at(r["h"], 0)
                side = choose_side(self.cfg, lad, self.last_result, {"up": _ask(p), "down": _ask(1 - p)}, self.rng)
                if (k, side) not in self._by_side:   # the entry depends only on the round and the side wanted
                    self._by_side[(k, side)] = entry_for(self.cfg, r, side)
                raw = self._by_side[(k, side)]
                e = with_gap(raw, self.gap, self.cap)
            else:
                raw, e = self.raw[k], self.fixed[k]
            last, self.last_result = self.last_result, r["result"]
            if e is None:                      # no entry in this round with these settings
                self.skipped += 1
                continue
            if r["start"] + e[0] < self.free_at:
                continue                       # still waiting for the previous bet's result
            t, side, price = e
            wait = settle_after(self.cfg, r)
            self.free_at = r["end"] + wait
            missed = k - self.last_bet_k - 1 if self.last_bet_k is not None else 0
            self.last_bet_k = k
            return price, r["result"] == side, {"round": r["start"], "end": r["end"], "slug": r.get("slug", ""),
                                                "paid_at": r["end"] + wait, "side": side, "at": round(t),
                                                "best": raw[2] if raw else price, "missed": missed}
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
             rounds: Optional[list] = None, gap: float = 0.0) -> dict:
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
        if cfg.sizing == "percent":            # Bet % of what's left now (base = that bet, so it's cached apart)
            return streak_sizing(cfg, 1, 0.0, left)
        if not cfg.survive_steps:
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
    shared: dict = {}                      # the past rounds' entries, worked out once for all runs
    for run in range(runs):
        feed = RealFeed(cfg, rounds, rng, gap, shared) if rounds is not None else FairFeed(price, p_win, rng)
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
                shares, cost = bet_terms(sizing, base, 1 if cfg.sizing == "percent" else step, owed, wins_left, p)
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
        "ladder_ends_median": statistics.median(ends_per_run), "ladder_ends_max": max(ends_per_run),
        "ladder_ends_run1": ends_per_run[0],
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
        out["history"] = history_summary(cfg, rounds, gap)
    return out


def history_summary(cfg, rounds: list, gap: float = 0.0) -> dict:
    """What the replay was based on: the rounds' span, and how the entries' prices and results came out per round
    (each past round once, whatever the ladder did)."""
    side = not ladder_free(cfg)
    cap = buy_cap(cfg)
    pairs = [(r, with_gap(entry_for(cfg, r, getattr(cfg, "first_side", "up")) if side else entry_for(cfg, r), gap, cap))
             for r in rounds]
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
