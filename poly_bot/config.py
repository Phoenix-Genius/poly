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
SIDE_MODES = ["alternate", "follow_last", "opposite_last", "cheaper", "up_only", "down_only",
              "flip_on_loss", "flip_on_win", "random"]

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
                   "the next bet costs more than what's left, the ladder ends. Empty (or 0) = the whole balance "
                   "(paper or live)."),
    "stop_profit_usd": (0.0, "number", None, "Until earn ($)",
                        "Stop once Profit since Reset reaches this, e.g. 20 (Stop/Start and app restarts don't change it; "
                        "Reset starts counting again). "
                        "Empty (or 0) = off: the bot runs until you stop it."),
    # --- sizing -----------------------------------------------------------------------------------------
    "sizing": ("recover", "select", ["recover", "multiply"], "Bet sizing",
               "recover = each bet is sized so one win pays back every loss of the streak plus the Profit per "
               "win, at the price actually paid. multiply = the first bet costs Starting bet, and every loss "
               "multiplies the next bet by the Multiplier."),
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
    "target_steps": (0, "int", None, "Steps to survive",
                     "Work out the first bet so the budget pays for this many losses in a row, e.g. 7, even if "
                     "every bet costs the Max price. recover sizing sets the Profit per win, multiply sizing the "
                     "Starting bet; that setting is then not used. Recalculated from the budget left at each new "
                     "streak (step 1), so it grows after wins. 0 (or empty) = off."),
    "max_steps": (0, "int", None, "Max losses in a row",
                  "End the ladder after this many losses in a row. 0 = no fixed limit, the budget decides."),
    "on_ladder_end": ("stop", "select", ["stop", "reset"], "When the ladder ends",
                      "What to do after max losses in a row, or when the budget can't pay the next bet. "
                      "stop = halt the bot (you press Reset). reset = accept the loss and restart at step 1."),
    # --- side -------------------------------------------------------------------------------------------
    "side_mode": ("alternate", "select", SIDE_MODES, "Side mode",
                  "alternate = Up, Down, Up, Down... follow_last = bet what the last finished round was "
                  "(momentum). opposite_last = bet against it. cheaper = the side with the lower price. "
                  "up_only / down_only = one side. flip_on_loss = keep the side after a win, switch after a "
                  "loss. flip_on_win = the opposite. random = coin flip."),
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
    "poll_interval_sec": (2.0, "number", None, "Check interval (s)",
                          "How often the bot checks the round. 1-5 is fine."),
}


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
    def is_live(self) -> bool:
        return self.network == "live"

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
        if not (0 <= self.target_steps <= 30):
            raise ValueError("Steps to survive must be between 1 and 30, or 0 for off")
        if self.target_steps and self.max_steps and self.max_steps < self.target_steps:
            raise ValueError(f"Max losses in a row ({self.max_steps}) is below Steps to survive "
                             f"({self.target_steps}): raise it or leave it empty")
        if not (0.5 <= self.poll_interval_sec <= 60):
            raise ValueError("Check interval must be between 0.5 and 60 seconds")


def load_config(path: Path | str) -> BotConfig:
    path = Path(path)
    values = json.loads(path.read_text()) if path.exists() else {}
    cfg = BotConfig(path.parent, **{k: v for k, v in values.items() if k in FIELDS})
    cfg.validate()
    return cfg


def save_settings(path: Path | str, body: dict) -> BotConfig:
    """Validate and write the settings in `body` (unknown keys ignored, missing keys kept)."""
    path = Path(path)
    current = json.loads(path.read_text()) if path.exists() else {}
    current.update({k: v for k, v in body.items() if k in FIELDS})
    cfg = BotConfig(path.parent, **{k: v for k, v in current.items() if k in FIELDS})
    cfg.validate()
    write_json(path, cfg.settings())
    return cfg


def write_json(path: Path, data: Any) -> None:
    """Write through a temp file so a crash mid-write never leaves half a file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
