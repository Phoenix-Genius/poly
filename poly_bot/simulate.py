"""Monte Carlo simulation of the martingale ladder: what these settings do over many bets.

Pure logic, no I/O. Every bet is bought at one price p (the ladder table's price, see strategy.plan_price) and wins
with a chance that defaults to p itself: a fair market, where the price is the honest probability. That is the
baseline for a bot that can't predict the rounds; the fee then makes each bet lose a little on average. A
different "win chance" answers "what if my side really wins 77% at 0.75?". Sizing, fees, the 5-share minimum,
Recover over N wins, Steps to survive, Max losses in a row and the budget are the bot's own (strategy.py).

Not modelled: slippage (walking the order book), price changing from bet to bet, Until earn, and the time between
bets. A ladder end restarts at step 1 with what's left, like "When the ladder ends = reset".
"""
from __future__ import annotations

import random
import statistics
from typing import Optional

from .strategy import cost_of, fee_per_share, shares_for, streak_sizing

MAX_WORK = 1_000_000          # runs x bets per request, so one click can't freeze the app
SAMPLE_PATHS = 5              # P&L curves returned for the chart
LOG_ROWS = 1000               # bets of run 1 listed one by one
PATH_POINTS = 200             # points per curve (downsampled)


def simulate(cfg, price: float, bankroll: float, bets: int = 1000, runs: int = 1000,
             win_chance: Optional[float] = None, seed: Optional[int] = None) -> dict:
    """Run the ladder `runs` times for up to `bets` bets each, starting with `bankroll` USDC to lose."""
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
    terms: dict = {}                       # (sizing, step, owed, wins_left) -> (shares, cost)

    def sizing_for(step: int, base: float, left: float, carried: bool):
        if not cfg.target_steps:
            return cfg, 0.0
        if (step == 1 and not carried) or not base:
            key = int(left)
            if key not in bases:
                bases[key] = streak_sizing(cfg, 1, 0.0, float(key))
            return bases[key]
        return streak_sizing(cfg, step, base, left, carried)

    def bet_terms(sizing, base: float, step: int, owed: float, wins_left: int) -> tuple[float, float]:
        key = (base, step, round(owed, 2), wins_left)        # base 0 = the settings' own sizing
        if key not in terms:
            shares = shares_for(sizing, step, owed, price, wins_left=wins_left)
            terms[key] = (shares, cost_of(shares, price))
        return terms[key]

    finals, ends_per_run, streaks, drawdowns, bet_counts, wins_total = [], [], [], [], [], 0
    log: list[dict] = []
    broke = 0
    paths = []
    every = max(1, bets // PATH_POINTS)
    for run in range(runs):
        pnl = peak = worst_dd = 0.0
        step, owed, wins_left, base = 1, 0.0, 0, 0.0
        streak = longest = ends = done = 0
        path = [0.0] if run < SAMPLE_PATHS else None
        while done < bets:
            left = bankroll + pnl
            sizing, base = sizing_for(step, base, left, owed > 0)
            logging = run == 0 and len(log) < LOG_ROWS
            fits = sizing is not None
            if fits:
                shares, cost = bet_terms(sizing, base, step, owed, wins_left)
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
                continue
            done += 1
            won = rng.random() < p_win
            got = shares - cost if won else -cost
            if logging:
                log.append({"n": done, "step": step, "price": price, "shares": shares, "cost": round(cost, 2),
                            "fee": round(shares * fee_per_share(price), 4), "win": won,
                            "payout": shares if won else 0.0, "pnl": round(got, 2), "total": round(pnl + got, 2),
                            "left": round(left + got, 2)})
            pnl += got
            if won:
                wins_total += 1
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
        sh, c = bet_terms(sizing0, base0, 1, 0.0, 0)
        first = {"cost": c, "win": sh - c, "loss": -c, "fits": c <= bankroll}
    return {
        "runs": runs, "bets": bets, "bankroll": bankroll, "price": price, "p_win": p_win,
        "break_even": price + fee_per_share(price), "first_bet": first,
        "average": statistics.fmean(finals), "median": statistics.median(finals),
        "p5": pct(0.05), "p95": pct(0.95), "best": s[-1], "worst": s[0],
        "profit_chance": sum(f > 0 for f in finals) / runs,
        "ladder_end_chance": sum(e > 0 for e in ends_per_run) / runs,
        "ladder_ends_avg": statistics.fmean(ends_per_run),
        "broke_chance": broke / runs,
        "longest_streak_median": statistics.median(streaks), "longest_streak_max": max(streaks),
        "drawdown_median": statistics.median(drawdowns),
        "bets_avg": statistics.fmean(bet_counts),
        "histogram": histogram(finals),
        "paths": paths, "path_every": every,
        "log": log, "log_bets": bet_counts[0],
    }


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
