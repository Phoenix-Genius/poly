"""The martingale maths for Polymarket Up/Down rounds. No I/O here, so all of it is unit-testable.

A bet buys `shares` of one side (Up or Down) at price p (0..1). Polymarket charges the taker a fee of
shares * rate * p * (1 - p) (rate 0.07 on crypto rounds), so the bet costs shares * p + fee. If the side wins, every
share pays $1; if it loses, the shares are worth $0. So:

    win:  profit = shares - cost          loss: profit = -cost

The ladder: after a loss the next bet is bigger so that one win pays back the streak; any win goes back to step 1.
With Recover over N wins (recover sizing), a win pays back 1/N of what the streak still owes, so bets grow more
slowly; the rest is carried to the next bets until N wins clear it (a loss spreads the debt over N wins again).
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

MIN_SHARES = 5.0                # Polymarket's minimum order size on these rounds
DEFAULT_FEE_RATE = 0.07         # crypto_fees_v2: fee = shares * rate * (p * (1 - p)) ** exponent
ROUND_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "daily": 86400}


def fee_per_share(p: float, rate: float = DEFAULT_FEE_RATE, exponent: float = 1.0) -> float:
    return rate * (p * (1 - p)) ** exponent


def cost_of(shares: float, p: float, rate: float = DEFAULT_FEE_RATE, exponent: float = 1.0) -> float:
    """USDC a buy of `shares` at price p costs, fee included."""
    return shares * (p + fee_per_share(p, rate, exponent))


def ceil2(x: float) -> float:
    return math.ceil(round(x * 100, 6)) / 100


def floor2(x: float) -> float:
    return math.floor(round(x * 100, 6)) / 100


def entry_window(timeframe: str, setting: int) -> float:
    """side mode: seconds after a round's start during which the bot may still bet (0 = auto: the first third)."""
    if setting > 0:
        return float(setting)
    return ROUND_SECONDS[timeframe] / 3


def mode(cfg) -> str:
    return getattr(cfg, "entry_mode", "side")


def entry_span(cfg, length: float) -> tuple[float, float]:
    """(open, close): seconds after a round's start between which the bot may bet. side mode: from the start through
    the Entry window. conditions mode: until 5 s before the end, from Time left before it when Time is an AND."""
    if mode(cfg) == "conditions":
        conds = cfg.conditions() if hasattr(cfg, "conditions") else {}
        ors_alone = getattr(cfg, "cond_join", "and") == "or" and "or" in conds.values()
        if getattr(cfg, "cond_time", "off") == "and" and not ors_alone:   # an OR alone may pass before Time left
            return max(0.0, length - cfg.cond_time_sec), length - 5
        return 0.0, length - 5
    return 0.0, entry_window(cfg.timeframe, cfg.entry_window_sec)


def buy_cap(cfg) -> float:
    """The most the bot pays per share (the order's limit): Max price (side mode), or Price to (conditions mode;
    empty or no Price condition: 0.99)."""
    if mode(cfg) == "conditions":
        return price_range(cfg)[1] if getattr(cfg, "cond_price", "off") != "off" else 0.99
    return cfg.max_price


def plan_price(cfg) -> float:
    """The price the ladder table assumes, the dearest the bot's settings let it pay: the Max price, or Price to in
    conditions mode (no limit: Price from if it is dearer than 0.75, else 0.75)."""
    if mode(cfg) == "conditions":
        return price_range(cfg)[1] if cfg.cond_price != "off" else 0.75
    return cfg.max_price


def price_range(cfg) -> tuple[float, float]:
    """conditions mode: the Price condition's (from, to); empty = 0.50 and 0.95. From 0.50 = the favourite only (with
    no minimum, the losing side at 0.02-0.10 passes whenever the favourite is above Price to, and those bets win
    ~1%); above 0.95 a win pays 5c a share or less."""
    return cfg.cond_price_min or 0.5, cfg.cond_price_max or 0.95


def price_range_text(cfg) -> str:
    lo, hi = price_range(cfg)
    return f"{lo:.2f}-{hi:.2f}"


def conditions_text(cfg) -> str:
    """conditions mode in words, e.g. "time <= 90s AND move >= $10 AND price 0.75-0.95"."""
    parts = []
    for c, op in cfg.conditions().items():
        what = {"time": f"time <= {cfg.cond_time_sec}s", "move": f"move >= ${cfg.cond_move_usd:g}",
                "price": f"price {price_range_text(cfg)}"}[c]
        parts.append((op.upper(), what))
    ands = [w for op, w in parts if op == "AND"]
    ors = [w for op, w in parts if op == "OR"]
    if getattr(cfg, "cond_join", "and") == "or" and ands and ors:    # ORs alone, or every AND together
        return " OR ".join(ors) + " OR " + ("(" + " AND ".join(ands) + ")" if len(ands) > 1 else ands[0])
    text = " AND ".join(ands)
    if ors:
        text += (" AND " if ands else "") + ("(" + " OR ".join(ors) + ")" if ands and len(ors) > 1 else " OR ".join(ors))
    return text


def leader_of(sides: list, prices: dict) -> str:
    """conditions mode, when both sides pass: the dearer one (the market's favourite); Up on a tie."""
    if len(sides) == 1:
        return sides[0]
    up, down = prices.get("up"), prices.get("down")
    return "down" if up is not None and down is not None and down > up else "up"


def sides_allowed(cfg, left: float, move: Optional[float], prices: dict) -> tuple[list, str]:
    """conditions mode: (the sides the conditions allow right now, why not when none).

    Each condition that is on is true for a set of sides: Time left (both, while `left` <= Time left at most),
    Move (the way the coin moved, while |move| >= Move at least; None = no live price or Price To Beat yet) and
    Price (each side whose best ask is within Price from-to). A side is allowed when every AND condition is true for
    it and, if any condition is OR, at least one OR condition is too (ANDs _ ORs = and); with ANDs _ ORs = or, a side
    is allowed when one OR condition is true for it, or every AND condition is. buy_cap (Price to) is checked after."""
    both = {"up", "down"}
    sets: dict[str, set] = {}
    why: dict[str, str] = {}
    conds = cfg.conditions()
    if "time" in conds:
        ok = left <= cfg.cond_time_sec + 1e-9
        sets["time"] = both if ok else set()
        why["time"] = f"{left:.0f} s left, waiting for {cfg.cond_time_sec} s"
    if "move" in conds:
        if move is None:
            sets["move"], why["move"] = set(), "no live price / Price To Beat yet"
        else:
            sets["move"] = {"up" if move > 0 else "down"} if abs(move) >= cfg.cond_move_usd - 1e-9 else set()
            why["move"] = f"moved ${abs(move):,.2f} {'up' if move >= 0 else 'down'}, needs ${cfg.cond_move_usd:,.2f}"
    if "price" in conds:
        lo, hi = price_range(cfg)
        sets["price"] = {s for s, p in prices.items() if p is not None and lo - 1e-9 <= p <= hi + 1e-9}
        show = " / ".join(f"{s.upper()} {p:.2f}" for s, p in sorted(prices.items()) if p is not None) or "no prices"
        dear = [(p, s) for s, p in prices.items() if p is not None and p > hi + 1e-9]
        why["price"] = f"{max(dear)[1].upper()} costs {max(dear)[0]:.2f}, above Price to {hi:.2f}" \
            if dear and not sets["price"] and lo >= 0.5 else f"no side at {price_range_text(cfg)} ({show})"
    allowed = set(both)
    ands = [c for c, op in conds.items() if op == "and"]
    for c in ands:
        allowed &= sets[c]
    ors = [c for c, op in conds.items() if op == "or"]
    if getattr(cfg, "cond_join", "and") == "or" and ands and ors:
        allowed = allowed | set().union(*(sets[c] for c in ors))   # ORs alone, or every AND together
        if allowed:
            return sorted(allowed), ""
        return [], ("; ".join(why[c] for c in ands if not sets[c]) or "the AND conditions point to different sides") \
            + " (all ANDs), or " + " or ".join(why[c] for c in ors)
    if ors:
        allowed &= set().union(*(sets[c] for c in ors))
    if allowed:
        return sorted(allowed), ""
    # say what is missing: the AND conditions that are false, else (all ANDs true) the ORs
    ands_false = [why[c] for c, op in conds.items() if op == "and" and not sets[c]]
    if ands_false:
        return [], "; ".join(ands_false)
    if ors and not any(sets[c] for c in ors):
        return [], " or ".join(why[c] for c in ors)
    if "price" in conds:          # the other conditions want a side that costs more than Price to
        want = set(both)
        for c, op in conds.items():
            if c != "price" and op == "and":
                want &= sets[c]
        other_ors = [c for c in ors if c != "price"]
        if other_ors:
            want &= set().union(*(sets[c] for c in other_ors))
        hi = price_range(cfg)[1]
        dear = sorted((prices[x], x) for x in want if prices.get(x) is not None and prices[x] > hi + 1e-9)
        if dear:
            return [], f"{dear[-1][1].upper()} costs {dear[-1][0]:.2f}, above Price to {hi:.2f}"
    return [], "the conditions point to different sides (" + ", ".join(
        f"{c} {'/'.join(x.upper() for x in sorted(sets[c])) or '-'}" for c in conds) + ")"


@dataclass
class Ladder:
    """Where the martingale stands. Saved in state-<network>.json."""
    step: int = 1                  # 1 = first bet of a streak
    streak_lost: float = 0.0       # USDC the streak still has to win back (fees included)
    pnl: float = 0.0               # realised profit since the last Reset
    wins: int = 0
    losses: int = 0
    last_side: str = ""            # side of the bot's last settled bet
    last_won: Optional[bool] = None
    rounds_bet: int = 0            # bets placed since Reset (for the alternate side mode)
    base: float = 0.0              # Steps to survive: this streak's Profit per win / Starting bet (0 = settings')
    wins_left: int = 0             # Recover over N wins: wins still needed to clear streak_lost (0 = N)

    def record(self, side: str, won: bool, profit: float, recover_wins: int = 1,
               base_profit: Optional[float] = None) -> None:
        """base_profit (recover sizing): the Profit per win the bet was sized for. A win then pays back only what
        it really earned over that, so a fill dearer than planned (slippage) leaves the shortfall owed instead of
        writing it off. None (multiply sizing): a win clears the streak, as before."""
        self.pnl += profit
        self.last_side, self.last_won = side, won
        if won:
            self.wins += 1
            self.step = 1
            left = self.wins_left or recover_wins
            if self.streak_lost <= 0:
                self.streak_lost, self.base, self.wins_left = 0.0, 0.0, 0
            elif base_profit is None:
                if left <= 1:
                    self.streak_lost, self.base, self.wins_left = 0.0, 0.0, 0
                else:                             # this win paid back 1/left of the debt; the rest is carried
                    self.streak_lost -= self.streak_lost / left
                    self.wins_left = left - 1
            else:
                self.streak_lost -= max(0.0, profit - base_profit)
                if self.streak_lost <= 0.005:
                    self.streak_lost, self.base, self.wins_left = 0.0, 0.0, 0
                else:                             # still owed (planned share or a short win): carry it
                    self.wins_left = max(1, left - 1)
        else:
            self.losses += 1
            self.step += 1
            self.streak_lost += -profit
            self.wins_left = 0                    # the bigger debt is spread over N wins again

    def restart(self) -> None:
        """Accept the streak's loss and start again at step 1 (pnl and counts stay)."""
        self.step, self.streak_lost, self.base, self.wins_left = 1, 0.0, 0.0, 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Ladder":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def recover_part(cfg, streak_lost: float, wins_left: int = 0) -> float:
    """What a win should pay back of the streak's debt: all of it, or 1/N with Recover over N wins."""
    return streak_lost / max(1, wins_left or int(getattr(cfg, "recover_wins", 1) or 1))


def shares_for(cfg, step: int, streak_lost: float, p: float, rate: float = DEFAULT_FEE_RATE,
               exponent: float = 1.0, wins_left: int = 0) -> float:
    """How many shares to buy at price p for this step of the ladder (at least MIN_SHARES)."""
    per_share = p + fee_per_share(p, rate, exponent)
    if cfg.sizing == "recover":
        # a win must leave base_profit after paying back (its part of) the streak: shares - shares * per_share = target
        shares = ceil2((cfg.base_profit_usd + recover_part(cfg, streak_lost, wins_left)) / (1 - per_share))
    else:
        shares = floor2(cfg.start_usd * cfg.multiplier ** (step - 1) / per_share)
    return max(MIN_SHARES, shares)


@dataclass
class Rung:
    step: int
    shares: float
    cost: float          # what this bet costs
    win_profit: float    # what a win at this step earns, after paying back (its part of) the streak
    lost_total: float    # the streak's total loss if this bet loses too
    fits: bool           # the budget can pay it
    left_after_win: float = 0.0   # Recover over N wins: debt still owed after a win here


def build_ladder(cfg, p: float, budget: float, rate: float = DEFAULT_FEE_RATE, max_rows: int = 30,
                 step: int = 1, lost: float = 0.0, wins_left: int = 0) -> list[Rung]:
    """The ladder at a constant price p: what each step costs and wins, and whether `budget` pays for it.

    step/lost: start from the middle of a streak (step 4 after losing $11.22) instead of a fresh ladder; `budget`
    then is the whole streak's budget (what's left now + what the streak already lost).
    """
    rows, first = [], step
    limit = cfg.max_steps or (first + max_rows - 1)
    for step in range(first, min(limit, first + max_rows - 1) + 1):
        shares = shares_for(cfg, step, lost, p, rate, wins_left=wins_left)
        cost = cost_of(shares, p, rate)
        fits = lost + cost <= budget + 1e-9
        part = recover_part(cfg, lost, wins_left) if cfg.sizing == "recover" else lost
        rows.append(Rung(step, shares, round(cost, 2), round(shares - cost - part, 2), round(lost + cost, 2), fits,
                         round(lost - part, 2)))
        if not fits:
            break
        lost += cost
        wins_left = 0                       # after a loss the debt is spread over N wins again
    return rows


def steps_that_fit(ladder: Sequence[Rung]) -> int:
    return sum(1 for r in ladder if r.fits)


@dataclass
class Sizing:
    """The sizing settings with the first bet replaced (what Steps to survive works out)."""
    sizing: str
    base_profit_usd: float
    start_usd: float
    multiplier: float
    max_steps: int
    recover_wins: int = 1


def with_base(cfg, base: float) -> Sizing:
    """cfg's sizing with the Profit per win (recover) or Starting bet (multiply) set to `base`."""
    recover = cfg.sizing == "recover"
    return Sizing(cfg.sizing, base if recover else cfg.base_profit_usd, cfg.start_usd if recover else base,
                  cfg.multiplier, cfg.max_steps, int(getattr(cfg, "recover_wins", 1) or 1))


def base_for_steps(cfg, steps: int, p: float, budget: float, rate: float = DEFAULT_FEE_RATE) -> Optional[float]:
    """The largest Profit per win (recover) or Starting bet (multiply), in cents, for which `budget` pays for
    `steps` bets in a row at price p. None when even the smallest bets (5 shares) don't fit."""
    def fits(base: float) -> bool:
        return steps_that_fit(build_ladder(with_base(cfg, base), p, budget, rate, max_rows=steps)) >= steps
    lo, hi = 0.01, max(0.01, budget)
    if not fits(lo):
        return None
    if fits(hi):
        return floor2(hi)
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if fits(mid) else (lo, mid)
    return floor2(lo)


def other(side: str) -> str:
    return "down" if side == "up" else "up"


def choose_side(cfg, ladder: Ladder, last_result: Optional[str], prices: dict,
                rng: Optional[random.Random] = None) -> str:
    """"up" or "down" for the next bet.

    last_result: how the market's last finished round ended ("up"/"down"), None if unknown.
    prices: best ask per side, {"up": 0.52, "down": 0.49}, used by the cheaper mode.
    """
    mode, first = cfg.side_mode, cfg.first_side
    rng = rng or random
    if mode == "up_only":
        return "up"
    if mode == "down_only":
        return "down"
    if mode == "alternate":
        return first if ladder.rounds_bet % 2 == 0 else other(first)
    if mode == "follow_last":
        return last_result or first
    if mode == "opposite_last":
        return other(last_result) if last_result else first
    if mode == "leader":
        up, down = prices.get("up"), prices.get("down")
        if up is not None and down is not None and up != down:
            return "up" if up > down else "down"
        return first
    if mode == "cheaper":
        up, down = prices.get("up"), prices.get("down")
        if up is not None and down is not None:
            return first if up == down else ("up" if up < down else "down")
        return "down" if down is not None else ("up" if up is not None else first)
    if mode in ("flip_on_loss", "flip_on_win"):
        if not ladder.last_side or ladder.last_won is None:
            return first
        flip = (not ladder.last_won) if mode == "flip_on_loss" else ladder.last_won
        return other(ladder.last_side) if flip else ladder.last_side
    if mode == "random":
        return rng.choice(["up", "down"])
    raise ValueError(f"unknown side mode {mode!r}")


@dataclass
class Fill:
    shares: float
    avg_price: float
    cost: float          # USDC paid, fee included
    fee: float
    levels: list = field(default_factory=list)


def walk_asks(asks: Sequence[tuple], shares: float, max_price: float, rate: float = DEFAULT_FEE_RATE,
              exponent: float = 1.0) -> Optional[Fill]:
    """Buy `shares` from the order book's asks [(price, size), ...], cheapest first, paying at most max_price.

    Returns None when the book can't fill the whole order within max_price (like a fill-or-kill market order),
    because a partly filled bet would break the ladder's maths.
    """
    need, paid, fee, used = shares, 0.0, 0.0, []
    for price, size in sorted(asks):
        if price > max_price + 1e-9 or need <= 1e-9:
            break
        take = min(size, need)
        paid += take * price
        fee += take * fee_per_share(price, rate, exponent)
        used.append((price, take))
        need -= take
    if need > 1e-9:
        return None
    return Fill(shares, paid / shares, round(paid + fee, 6), round(fee, 6), used)


def shares_for_book(cfg, step: int, streak_lost: float, asks: Sequence[tuple], max_price: float,
                    rate: float = DEFAULT_FEE_RATE, exponent: float = 1.0, wins_left: int = 0) -> Optional[float]:
    """Like shares_for, but priced on the real order book: a big bet climbs the asks (slippage), so a recover bet
    sized at the best price alone wins back less than the streak lost. Returns the fewest shares (to the cent)
    whose win, after the cost of walking the asks up to max_price (fees included), leaves the Profit per win plus
    the streak's loss; None when the book up to max_price is too thin for that. multiply sizing has no target,
    so it's sized at the best ask as before."""
    asks = sorted(a for a in asks if a[0] <= max_price + 1e-9)
    if not asks:
        return None
    if cfg.sizing != "recover":
        return shares_for(cfg, step, streak_lost, asks[0][0], rate, exponent)
    target = cfg.base_profit_usd + recover_part(cfg, streak_lost, wins_left)

    def net(n: float) -> Optional[float]:          # what a win of n shares earns over their cost
        f = walk_asks(asks, n, max_price, rate, exponent)
        return None if f is None else n - f.cost

    lo = shares_for(cfg, step, streak_lost, asks[0][0], rate, exponent, wins_left)   # if the best ask had them all
    if (net(lo) or -1) >= target - 1e-9:
        return lo
    hi = floor2(sum(size for _, size in asks))
    if hi <= lo or (net(hi) or -1) < target - 1e-9:
        return None
    for _ in range(60):                             # net() rises with n: bisect to the cent
        if hi - lo <= 0.01 + 1e-9:
            break
        mid = ceil2((lo + hi) / 2)
        if mid >= hi:
            break
        lo, hi = (mid, hi) if (net(mid) or -1) < target - 1e-9 else (lo, mid)
    return hi


def streak_sizing(cfg, step: int, base: float, budget: float, carried: bool = False):
    """(sizing, base) for a bet at `step` of a streak. Without Steps to survive: (cfg, 0). With it, a new streak
    (step 1, or no base yet) works out its first bet from `budget` at the Max price; later steps keep `base`.
    sizing is None when the budget can't pay for the steps."""
    if not cfg.survive_steps:
        return cfg, 0.0
    if (step == 1 and not carried) or not base:     # carried: a debt still being won back keeps its streak's base
        base = base_for_steps(cfg, cfg.survive_steps, buy_cap(cfg), budget)
        if base is None:
            return None, 0.0
    return with_base(cfg, base), base

