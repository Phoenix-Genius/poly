"""Config loading/saving. Each bot's settings live in profiles/<id>/config.json.

FIELDS is the single source of truth for every setting: its default, type, allowed values and help text.
The web UI builds its settings form from it, and README.md documents the same keys.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE"]
TIMEFRAMES = ["5m", "15m", "1h", "4h", "daily"]
SIDE_MODES = ["alternate", "follow_last", "opposite_last", "cheaper", "leader", "up_only", "down_only",
              "flip_on_loss", "flip_on_win", "random", "trend_random", "trend_follow"]
TREND_WINDOWS = ["month", "30d", "7d", "3d", "1d", "12h", "6h"]
COND_OPS = ["off", "and", "or"]

# key: (default, type, options-or-None, label, help)
FIELDS: dict[str, tuple] = {
    # --- basics -----------------------------------------------------------------------------------------
    "bot_name": ("", "text", None, "Bot name",
                 "Shown in the bot list and the trade log so you can tell bots apart. Empty = a random animal name."),
    "network": ("paper", "select", ["paper", "live"], "Network",
                "paper = simulated bets on the real Polymarket rounds: live order books and real results, "
                "virtual money, no wallet. live = REAL money: bets with the pUSD in your Polymarket account "
                "(needs your Polymarket keys)."),
    "paper_balance_usd": (1000.0, "number", None, "Paper balance",
                          "Virtual USDC the paper account starts with. Applies when you press Reset paper "
                          "(and the first time the bot runs)."),
    "coin": ("BTC", "select", COINS, "Coin",
             "Which coin's Up or Down rounds to bet on. Not every coin has every timeframe "
             "(SOL has no 1h or daily, DOGE no daily)."),
    "timeframe": ("5m", "select", TIMEFRAMES, "Round length",
                  "5m, 15m, 1h, 4h or daily rounds. Each round asks: is the price at the end >= the price at "
                  "the start? Up wins if yes, Down if no."),
    "budget_usd": (0.0, "number", None, "Budget (USDC)",
                   "How much the bot may lose in total. Profits are added to it and losses taken from it. When "
                   "the next bet costs more than what's left, the ladder ends. Empty (or 0) = the whole balance. "
                   "Live only (your live bots share one wallet): a paper bot's Paper balance is its budget."),
    "stop_profit_usd": (0.0, "number", None, "Until earn ($)",
                        "Stop once Profit since Reset reaches this, e.g. 20 (Stop/Start and app restarts don't change it; "
                        "Reset starts counting again). "
                        "Empty (or 0) = off: the bot runs until you stop it."),
    # --- sizing -----------------------------------------------------------------------------------------
    "sizing": ("recover", "select", ["recover", "multiply", "percent"], "Bet sizing",
               "recover = each bet is sized so one win pays back every loss of the streak plus the Profit per "
               "win, at the price actually paid. multiply = the first bet costs Starting bet, and every loss "
               "multiplies the next bet by the Multiplier. percent = each bet costs a share of the budget left "
               "(Bet % of budget); 100% = all-in."),
    "bet_pct": (100.0, "number", None, "Bet (% of budget)",
                "percent sizing: each bet costs this share of the budget left, fees included. 100 = all-in: "
                "the whole budget on every bet. A win makes the next bet bigger, a loss smaller; at 100% one "
                "loss ends the ladder. Polymarket's minimum is 5 shares."),
    "base_profit_usd": (2.5, "number", None, "Profit per win ($)",
                        "recover sizing: what each win earns after fees and after paying back the streak's "
                        "losses, e.g. 2.5. At a price of 0.50 the first bet then costs about $2.69, and each "
                        "loss roughly doubles the next bet. Polymarket's minimum is 5 shares (about $2.60 at 0.50)."),
    "start_usd": (3.0, "number", None, "Starting bet ($)",
                  "multiply sizing: the first bet's cost in USDC, fee included (5 shares minimum, so at 0.50 "
                  "at least about $2.60)."),
    "multiplier": (2.2, "number", None, "Multiplier after loss",
                   "multiply sizing: after a loss the next bet costs this many times more. 2 = $3 -> $6 -> $12. "
                   "At a price of 0.50 a win pays about 1.93x the bet, so 2.1 or more is needed to recover."),
    "recover_wins": (1, "int", None, "Recover over (wins)",
                     "recover sizing: how many wins pay back a losing streak. 1 = one win pays back everything "
                     "(bets about double after each loss). 2 = each win pays back half of what's still owed plus "
                     "the Profit per win (bets grow about 1.5x), 3 = a third (about 1.35x): far more losses in a "
                     "row fit in the budget, but a streak needs 2 or 3 wins to clear, and a loss in between adds "
                     "to the debt."),
    "target_steps": (0, "int", None, "Steps to survive",
                     "Work out the first bet so the budget pays for this many losses in a row, e.g. 7, even if "
                     "every bet costs the Max price. recover sizing sets the Profit per win, multiply sizing the "
                     "Starting bet; that setting is then not used. Recalculated from the budget left at each new "
                     "streak (step 1), so it grows after wins. 0 (or empty) = off. Side mode only: in conditions "
                     "mode a bet often costs 0.75-0.99, so each step costs ~4-20x the last."),
    "max_steps": (0, "int", None, "Max losses in a row",
                  "End the ladder after this many losses in a row. 0 = no fixed limit, the budget decides."),
    "on_ladder_end": ("reset", "select", ["stop", "reset"], "When the ladder ends",
                      "What to do after max losses in a row, or when the budget can't pay the next bet. "
                      "stop = halt the bot (you press Reset). reset = accept the loss and restart at step 1; "
                      "at step 1 it skips a round it can't pay and waits for one where the bet fits."),
    # --- side -------------------------------------------------------------------------------------------
    "entry_mode": ("side", "select", ["side", "conditions"], "Bet mode",
                   "side = pick a side by the Side mode and buy it near 50 cents early in the round (Min/Max price, "
                   "Entry window). conditions = bet when your Time left, Move and Price conditions are met, each "
                   "AND (must be true) or OR (one of the ORs is enough), e.g. 90 s or less left AND moved $10+ AND "
                   "the side costs 0.75-0.95; Move picks the way the coin moved, Price the side in range, and when "
                   "both sides pass it bets the leader (the dearer side). A higher price wins more often but pays "
                   "less: at 0.75 a win pays about 0.24 per share and a loss costs about 0.76, so it has to win "
                   "about 77% to break even, and each martingale step costs about 4x the last."),
    # --- conditions mode -----------------------------------------------------------------------------------------
    "cond_time": ("and", "select", COND_OPS, "Time left",
                  "conditions mode: off, AND (must be true) or OR (this or another OR condition). True while this many "
                  "seconds or fewer are left in the round (≤ s left). Doesn't pick a side."),
    "cond_time_sec": (90, "int", None, "≤ s left",
                      "conditions mode: the Time left condition is true from this many seconds before the end, e.g. 90, "
                      "until the bot stops betting 5 s before the end."),
    "cond_time_min_sec": (0, "int", None, "≥ s left",
                          "conditions mode: the Time left condition is true only while at least this many seconds are "
                          "left, so the bot stops entering then, e.g. ≤ 240 and ≥ 180 = only 60-120 s into a 5m round. "
                          "0 (or empty) = no limit: until 5 s before the end."),
    "cond_move": ("and", "select", COND_OPS, "Move",
                  "conditions mode: off, AND or OR. True for Up while the coin's live price (Chainlink, what the round "
                  "settles on) is the ≥ $ from beat amount above the round's Price To Beat, for Down while it is that far "
                  "below. Checked on the price right now, not the highest it reached."),
    "cond_move_usd": (10.0, "number", None, "≥ $ from beat",
                      "conditions mode: how many dollars from the Price To Beat the Move condition needs, e.g. 10 for BTC."),
    "cond_price": ("and", "select", COND_OPS, "Price",
                   "conditions mode: off, AND or OR. True for each side whose price (best ask) is between Price from and "
                   "Price to."),
    "cond_price_min": (0.0, "number", None, "Price from",
                       "conditions mode: the Price condition's lowest price per share, e.g. 0.75. Empty = 0.50: "
                       "the favourite only, never the side the market expects to lose."),
    "cond_price_max": (0.0, "number", None, "Price to",
                       "conditions mode: the Price condition's highest price per share, e.g. 0.95. Also the most the bot "
                       "ever pays per share (the order's limit, so a fill can't slip above it), even when the bet "
                       "came from an OR Move. Empty = 0.95. Price off = no limit (up to 0.99)."),
    "cond_join": ("and", "select", ["and", "or"], "ANDs _ ORs",
                  "conditions mode: how the OR conditions join the AND conditions. and = every AND condition AND at "
                  "least one OR, e.g. Time OR, Move OR, Price AND = (time or move) and price. or = all the AND "
                  "conditions together, OR any one OR condition alone, e.g. Time OR, Move AND, Price AND = time or "
                  "(move and price). Same either way when every condition is AND, or every one is OR."),
    "side_mode": ("alternate", "select", SIDE_MODES, "Side mode",
                  "side mode: which side to bet. alternate = Up, Down, Up, Down... follow_last = bet what the last finished round was "
                  "(momentum). opposite_last = bet against it. cheaper = the side with the lower price. "
                  "leader = the dearer side (the one the market expects to win). "
                  "up_only / down_only = one side. flip_on_loss = keep the side after a win, switch after a "
                  "loss. flip_on_win = the opposite. random = coin flip. trend_random = a coin weighted by how often "
                  "each side won in the Trend window (47% Up = Up 47% of the time). trend_follow = always the side "
                  "that won more often in the Trend window."),
    "trend_window": ("month", "select", TREND_WINDOWS, "Trend window",
                     "For trend_random and trend_follow: which past rounds to count. month = since the 1st of this "
                     "month (JST), 30d / 7d / 3d / 1d / 12h / 6h = that long back. With fewer than 20 rounds "
                     "counted, trend_random flips a fair coin and trend_follow bets the First side."),
    "first_side": ("up", "select", ["up", "down"], "First side",
                   "Side of the first bet (and the first after Reset) for alternate, flip_on_loss and "
                   "flip_on_win, and the fallback when the last result isn't known yet."),
    # --- entry ------------------------------------------------------------------------------------------
    "min_price": (0.45, "number", None, "Min price",
                  "Never bet on a side cheaper than this per share (0.30 = 30 cents, the market gives that side "
                  "only 30%). Stops long-shot bets late in a round, which lose most of the time. If the side "
                  "is cheaper, the bot waits; if the entry window passes, it skips the round. 0 = no minimum."),
    "max_price": (0.55, "number", None, "Max price",
                  "Never pay more than this per share (0.55 = 55 cents, the market gives that side 55%). "
                  "Higher prices win more often but pay less, so the next bets grow faster. If the side costs "
                  "more, the bot waits; if the entry window passes, it skips the round."),
    "entry_window_sec": (0, "int", None, "Entry window (s)",
                         "Bet only during this many seconds after a round starts, e.g. 60. Later in the round "
                         "the price already shows where it is heading. 0 = auto: the first third of the round "
                         "(5m: 100 s, 15m: 5 min, 1h: 20 min)."),
    "poll_interval_sec": (1.0, "number", None, "Check interval (s)",
                          "How often the bot checks the round. 1-5 is fine."),
    "result_wait": ("fast", "select", ["fast", "official"], "Result",
                    "When a round's bet counts as won or lost, so the bot can bet again. fast = about 10-30 s after "
                    "the round ends, once the bot's own 60 s TWAP (what Polymarket settles on) and the market (the "
                    "winner at 0.97+) agree and the coin ended clearly away from the Price To Beat; a close round "
                    "waits for the official result. Polymarket's official result is still checked when it comes, "
                    "and in the rare case it differs the ladder and History are corrected. official = wait for "
                    "Polymarket's result (2.5-3.5 min after the end), so after a bet the bot can only bet in the "
                    "last ~2 min of the next round."),
}


def upgrade(values: dict) -> dict:
    """Old settings -> today's, so old config files and presets keep working (2026-11-16: the hit, time, move and
    mixed Bet modes became one "conditions" mode). Each old mode becomes the conditions that bet exactly the same:
      hit:  Price AND from Hit price to Pay at most (empty = no limit)
      time: Time left AND <= Time left + Price AND from Leader at least to Pay at most (the leader when both pass)
      move: Move AND >= Move ($) + Price AND up to Pay at most (Price off when there was no limit)
    Returns a new dict; unknown keys are left for the caller to drop."""
    v = dict(values)
    mode = v.get("entry_mode")
    if mode == "mixed":
        v["entry_mode"] = "conditions"
    elif mode in ("hit", "time", "move"):
        cap = float(v.get("hit_max_price") or 0)
        off = {"cond_time": "off", "cond_move": "off", "cond_price": "off", "cond_price_min": 0.0, "cond_price_max": 0.0}
        if mode == "hit":
            off.update(cond_price="and", cond_price_min=float(v.get("hit_price") or 0.75), cond_price_max=cap)
        elif mode == "time":
            lead = float(v.get("time_min_price") or 0)
            off.update(cond_time="and", cond_time_sec=int(float(v.get("time_left_sec") or 120)))
            if lead or cap:
                off.update(cond_price="and", cond_price_min=lead, cond_price_max=cap)
        else:
            off.update(cond_move="and", cond_move_usd=float(v.get("move_usd") or 50))
            if cap:
                off.update(cond_price="and", cond_price_max=cap)
        v.update(off, entry_mode="conditions")
    for old in ("hit_price", "hit_max_price", "time_left_sec", "time_min_price", "move_usd"):
        v.pop(old, None)
    return v


def coerce(key: str, value: Any) -> Any:
    """A form/JSON value -> the setting's type. Empty means the default."""
    default, typ, options, label, _ = FIELDS[key]
    if value is None or value == "":
        return default
    try:
        if typ == "number":
            return float(value)
        if typ == "int":
            return int(float(value))
        if typ == "bool":
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    except (TypeError, ValueError):
        raise ValueError(f"{label}: {value!r} is not a number")
    return str(value).strip()


class BotConfig:
    """One bot's settings (FIELDS) plus where its files are."""

    def __init__(self, folder: Path | str = ".", **values):
        self.folder = Path(folder)
        values = upgrade(values)
        for key, (default, *_rest) in FIELDS.items():
            setattr(self, key, coerce(key, values.get(key, default)))

    # files in the bot's folder; the network is part of the name so paper and live never mix
    @property
    def state_file(self) -> Path:
        return self.folder / f"state-{self.network}.json"

    @property
    def trade_log(self) -> Path:
        return self.folder / f"trades-{self.network}.csv"

    @property
    def paper_file(self) -> Path:
        return self.folder / "paper-account.json"

    @property
    def display_name(self) -> str:
        return self.bot_name.strip() or f"{self.coin} {self.timeframe}"

    @property
    def conditions_mode(self) -> bool:
        return self.entry_mode == "conditions"

    def conditions(self) -> dict:
        """conditions mode: {"time"|"move"|"price": "and"|"or"} for the conditions that are on."""
        return {c: getattr(self, f"cond_{c}") for c in ("time", "move", "price") if getattr(self, f"cond_{c}") != "off"}

    @property
    def is_live(self) -> bool:
        return self.network == "live"

    @property
    def survive_steps(self) -> int:
        """Steps to survive in effect: side mode only. In hit, time and move mode a bet costs 0.75-0.99 a share, so
        each step costs ~4-20x the last and fitting several into the budget leaves no real first bet (the saved
        value is kept and counts again in side mode)."""
        return self.target_steps if self.entry_mode == "side" and self.sizing != "percent" else 0

    @property
    def budget(self) -> float:
        """The Budget in effect: live only. A paper bot has its own paper account, so its Paper balance is its
        budget (a Budget saved earlier is kept and counts again if the bot goes live)."""
        return self.budget_usd if self.is_live else 0.0

    @property
    def is_paper(self) -> bool:
        return self.network == "paper"

    def settings(self) -> dict:
        return {k: getattr(self, k) for k in FIELDS}

    def validate(self) -> None:
        for key, (_, typ, options, label, _) in FIELDS.items():
            if options and getattr(self, key) not in options:
                raise ValueError(f"{label}: must be one of {', '.join(options)}")
        if len(self.bot_name) > 40:
            raise ValueError("Bot name: 40 characters at most")
        if self.paper_balance_usd <= 0:
            raise ValueError("Paper balance must be more than 0")
        for key in ("budget_usd", "stop_profit_usd", "max_steps", "entry_window_sec"):
            if getattr(self, key) < 0:
                raise ValueError(f"{FIELDS[key][3]} can't be negative")
        if self.base_profit_usd <= 0:
            raise ValueError("Profit per win must be more than 0")
        if self.start_usd <= 0:
            raise ValueError("Starting bet must be more than 0")
        if self.multiplier < 1:
            raise ValueError("Multiplier must be at least 1")
        if not (0.02 <= self.max_price <= 0.98):
            raise ValueError("Max price must be between 0.02 and 0.98")
        if not (0 <= self.min_price < self.max_price):
            raise ValueError("Min price must be 0 or more and below the max price")
        if self.conditions_mode:
            if not self.conditions():
                raise ValueError("Conditions mode: turn on at least one condition (Time left, Move or Price)")
            if self.cond_time_sec < 10:
                raise ValueError("Time left: ≤ s left must be at least 10 seconds")
            if self.cond_time_min_sec < 0 or (self.cond_time_min_sec and self.cond_time_min_sec >= self.cond_time_sec):
                raise ValueError("Time left: ≥ s left must be below ≤ s left (or empty for no limit)")
            if self.cond_move_usd <= 0:
                raise ValueError("Move: ≥ $ from beat must be more than 0")
            if not (0 <= self.cond_price_min <= 0.99) or not (self.cond_price_max == 0 or 0.01 <= self.cond_price_max <= 0.99):
                raise ValueError("Price from and Price to must be between 0.01 and 0.99, or empty for no limit")
            if self.cond_price_max and self.cond_price_min > self.cond_price_max:
                raise ValueError("Price from is above Price to")
        if not (1 <= self.bet_pct <= 100):
            raise ValueError("Bet (% of budget) must be between 1 and 100")
        if not (1 <= self.recover_wins <= 5):
            raise ValueError("Recover over (wins) must be between 1 and 5")
        if not (0 <= self.target_steps <= 30):
            raise ValueError("Steps to survive must be between 1 and 30, or 0 for off")
        if self.survive_steps and self.max_steps and self.max_steps < self.survive_steps:
            raise ValueError(f"Max losses in a row ({self.max_steps}) is below Steps to survive "
                             f"({self.target_steps}): raise it or leave it empty")
        if not (0.5 <= self.poll_interval_sec <= 60):
            raise ValueError("Check interval must be between 0.5 and 60 seconds")


def load_config(path: Path | str) -> BotConfig:
    path = Path(path)
    values = upgrade(json.loads(path.read_text())) if path.exists() else {}
    cfg = BotConfig(path.parent, **{k: v for k, v in values.items() if k in FIELDS})
    cfg.validate()
    return cfg


def save_settings(path: Path | str, body: dict) -> BotConfig:
    """Validate and write the settings in `body` (unknown keys ignored, missing keys kept)."""
    path = Path(path)
    current = upgrade(json.loads(path.read_text())) if path.exists() else {}
    current.update(upgrade(body) if body.get("entry_mode") in ("hit", "time", "move", "mixed") else body)
    current = {k: v for k, v in current.items() if k in FIELDS}
    cfg = BotConfig(path.parent, **{k: v for k, v in current.items() if k in FIELDS})
    cfg.validate()
    write_json(path, cfg.settings())
    return cfg


def write_json(path: Path, data: Any) -> None:
    """Write through a temp file so a crash mid-write never leaves half a file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
