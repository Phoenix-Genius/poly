"""The martingale maths for Polymarket Up/Down rounds. No I/O here, so all of it is unit-testable.

A bet buys `shares` of one side (Up or Down) at price p (0..1). Polymarket charges the taker a fee of
shares * rate * p * (1 - p) (rate 0.07 on crypto rounds), so the bet costs shares * p + fee. If the side wins, every
share pays $1; if it loses, the shares are worth $0. So:

    win:  profit = shares - cost          loss: profit = -cost

The ladder: after a loss the next bet is bigger so that one win pays back the streak; any win goes back to step 1.
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
    """Seconds after a round's start during which the bot may still bet (0 = auto: the first third)."""
    return float(setting) if setting > 0 else ROUND_SECONDS[timeframe] / 3


@dataclass
class Ladder:
    """Where the martingale stands. Saved in state-<network>.json."""
    step: int = 1                  # 1 = first bet of a streak
    streak_lost: float = 0.0       # USDC lost in the current losing streak (fees included)
    pnl: float = 0.0               # realised profit since the last Reset
    wins: int = 0
    losses: int = 0
    last_side: str = ""            # side of the bot's last settled bet
    last_won: Optional[bool] = None
    rounds_bet: int = 0            # bets placed since Reset (for the alternate side mode)

    def record(self, side: str, won: bool, profit: float) -> None:
        self.pnl += profit
        self.last_side, self.last_won = side, won
        if won:
            self.wins += 1
            self.step, self.streak_lost = 1, 0.0
        else:
            self.losses += 1
            self.step += 1
            self.streak_lost += -profit

    def restart(self) -> None:
        """Accept the streak's loss and start again at step 1 (pnl and counts stay)."""
        self.step, self.streak_lost = 1, 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Ladder":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def shares_for(cfg, step: int, streak_lost: float, p: float, rate: float = DEFAULT_FEE_RATE,
               exponent: float = 1.0) -> float:
    """How many shares to buy at price p for this step of the ladder (at least MIN_SHARES)."""
    per_share = p + fee_per_share(p, rate, exponent)
    if cfg.sizing == "recover":
        # a win must leave base_profit after paying back the streak: shares - shares * per_share = target
        shares = ceil2((cfg.base_profit_usd + streak_lost) / (1 - per_share))
    else:
        shares = floor2(cfg.start_usd * cfg.multiplier ** (step - 1) / per_share)
    return max(MIN_SHARES, shares)


@dataclass
class Rung:
    step: int
    shares: float
    cost: float          # what this bet costs
    win_profit: float    # what a win at this step earns, after paying back the streak
    lost_total: float    # the streak's total loss if this bet loses too
    fits: bool           # the budget can pay it


def build_ladder(cfg, p: float, budget: float, rate: float = DEFAULT_FEE_RATE, max_rows: int = 30,
                 step: int = 1, lost: float = 0.0) -> list[Rung]:
    """The ladder at a constant price p: what each step costs and wins, and whether `budget` pays for it.

    step/lost: start from the middle of a streak (step 4 after losing $11.22) instead of a fresh ladder; `budget`
    then is the whole streak's budget (what's left now + what the streak already lost).
    """
    rows, first = [], step
    limit = cfg.max_steps or (first + max_rows - 1)
    for step in range(first, min(limit, first + max_rows - 1) + 1):
        shares = shares_for(cfg, step, lost, p, rate)
        cost = cost_of(shares, p, rate)
        fits = lost + cost <= budget + 1e-9
        rows.append(Rung(step, shares, round(cost, 2), round(shares - cost - lost, 2), round(lost + cost, 2), fits))
        if not fits:
            break
        lost += cost
    return rows


def steps_that_fit(ladder: Sequence[Rung]) -> int:
    return sum(1 for r in ladder if r.fits)


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
