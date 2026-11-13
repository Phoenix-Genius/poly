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


def entry_window(timeframe: str, setting: int, hit: bool = False) -> float:
    """Seconds after a round's start during which the bot may still bet (0 = auto: the first third, or the whole
    round in hit mode)."""
    if setting > 0:
        return float(setting)
    return ROUND_SECONDS[timeframe] - 5 if hit else ROUND_SECONDS[timeframe] / 3


def mode(cfg) -> str:
    return getattr(cfg, "entry_mode", "side")


def entry_span(cfg, length: float) -> tuple[float, float]:
    """(open, close): seconds after a round's start between which the bot may bet. time mode: from Time left
    before the end to 5 s before it; else from the start through the Entry window."""
    if mode(cfg) == "time":
        return max(0.0, length - cfg.time_left_sec), length - 5
    return 0.0, entry_window(cfg.timeframe, cfg.entry_window_sec, mode(cfg) in ("hit", "move"))


def buy_cap(cfg) -> float:
    """The most the bot pays per share: Max price, or Pay at most in hit, time and move mode (0 = no limit: 0.99)."""
    if mode(cfg) in ("hit", "time", "move"):
        return cfg.hit_max_price or 0.99
    return cfg.max_price


def plan_price(cfg) -> float:
    """The price the ladder table assumes, the dearest the bot's settings let it pay: the Max price, or Pay at most
    in hit and time mode. With no limit: the Hit price (hit), or Leader at least / 0.75 (time)."""
    if mode(cfg) == "hit":
        return cfg.hit_max_price or cfg.hit_price
    if mode(cfg) == "time":
        return cfg.hit_max_price or cfg.time_min_price or 0.75
    if mode(cfg) == "move":
        return cfg.hit_max_price or 0.75
    return cfg.max_price


def leader_side(cfg, prices: dict) -> tuple[Optional[str], str]:
    """time mode: (side, "") for the leading side (the dearer best ask) when its price is within Leader at least and
    Pay at most, else (None, why)."""
    up, down = prices.get("up"), prices.get("down")
    show = " / ".join(f"{s.upper()} {p:.2f}" for s, p in sorted(prices.items()) if p is not None) or "no prices"
    if up is None or down is None or abs(up - down) < 1e-9:
        return None, f"no clear leader ({show})"
    side = "up" if up > down else "down"
    p = prices[side]
    if p < cfg.time_min_price - 1e-9:
        return None, f"{side.upper()} leads at {p:.2f}, below Leader at least {cfg.time_min_price:.2f}"
    if p > buy_cap(cfg) + 1e-9:
        return None, f"{side.upper()} leads at {p:.2f}, above Pay at most {buy_cap(cfg):.2f}"
    return side, ""


def hit_side(cfg, prices: dict) -> tuple[Optional[str], str]:
    """hit mode: (side, "") for the side whose best ask is between the Hit price and Pay at most, else (None, why).
    Both sides can't sensibly be at 0.75+ at once; if they are, the dearer one is the market's favourite."""
    cap = buy_cap(cfg)
    ok = [(p, s) for s, p in prices.items() if p is not None and cfg.hit_price - 1e-9 <= p <= cap + 1e-9]
    if ok:
        return max(ok)[1], ""
    over = [(p, s) for s, p in prices.items() if p is not None and p > cap + 1e-9]
    if over:
        p, s = max(over)
        return None, f"{s.upper()} is at {p:.2f}, above Pay at most {cap:.2f}"
    show = " / ".join(f"{s.upper()} {p:.2f}" for s, p in sorted(prices.items()) if p is not None) or "no prices"
    return None, f"no side at {cfg.hit_price:.2f} yet ({show})"


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

    def record(self, side: str, won: bool, profit: float, recover_wins: int = 1) -> None:
        self.pnl += profit
        self.last_side, self.last_won = side, won
        if won:
            self.wins += 1
            self.step = 1
            left = self.wins_left or recover_wins
            if left <= 1 or self.streak_lost <= 0:
                self.streak_lost, self.base, self.wins_left = 0.0, 0.0, 0
            else:                                 # this win paid back 1/left of the debt; the rest is carried
                self.streak_lost -= self.streak_lost / left
                self.wins_left = left - 1
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
    if not cfg.target_steps:
        return cfg, 0.0
    if (step == 1 and not carried) or not base:     # carried: a debt still being won back keeps its streak's base
        base = base_for_steps(cfg, cfg.target_steps, buy_cap(cfg), budget)
        if base is None:
            return None, 0.0
    return with_base(cfg, base), base

