"""Local web app: any number of bots side by side, each with its settings, Start/Stop, live status, history and log.

Every bot is a folder, profiles/<id>/, with its own config.json, state, trade log, paper account and bot.log.

Standard library only. Listens on 127.0.0.1 by default. POST requests must carry an X-Requested-With header
and a localhost Host header, so other websites open in your browser can't drive the bots.
"""
from __future__ import annotations

import collections
import json
import logging
import random
import re
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

from . import __version__
from .clock import use_jst_in_logs
from .config import COINS, FIELDS, TIMEFRAMES, BotConfig, load_config, save_settings, write_json
from .engine import Bot, fmt_secs
from .instance import PROFILES_DIR, ROOT, AlreadyRunning, acquire_lock, bot_ids, free_bot_id, slug
from .markets import MarketError, Polymarket
from .live import LiveClient, keys_view, read_keys, save_keys
from .paper import PaperClient
from .history import MIN_ROUNDS, CoinPaths, History
from .simulate import simulate
from .strategy import build_ladder, buy_cap, plan_price, recover_part, steps_that_fit, streak_sizing
from .trades import append_event, read_events, read_trades

log = logging.getLogger("poly")
STATIC = Path(__file__).parent / "static"
FORMAT = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
PM = Polymarket()                     # shared by all bots: one cache of series ids
HISTORY = History(PM)                 # past rounds for the real simulation, shared too
COIN_PATHS = CoinPaths()              # the coin's own price inside those rounds (move mode)
RUNNING_MARK = ".running"             # in a bot's folder while it should be running
STARTED = str(time.time())            # changes on every app start; an open page reloads its settings form then


class Lines(logging.Handler):
    """The newest log lines of one bot, for the page."""

    def __init__(self, size: int = 300):
        super().__init__()
        self.lines: collections.deque = collections.deque(maxlen=size)
        self.setFormatter(FORMAT)

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def bot_logger(bot_id: str, folder: Path) -> tuple[logging.Logger, Lines]:
    logger = logging.getLogger(f"poly.bot.{bot_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    lines = Lines()
    to_file = logging.FileHandler(folder / "bot.log", encoding="utf-8")
    to_file.setFormatter(FORMAT)
    to_term = logging.StreamHandler(sys.stderr)
    to_term.setFormatter(logging.Formatter(f"%(asctime)s [{bot_id}] %(message)s", "%H:%M:%S"))
    for h in (lines, to_file, to_term):
        logger.addHandler(h)
    if (folder / "bot.log").exists():   # show the last lines from before this start, too
        try:
            for line in (folder / "bot.log").read_text(encoding="utf-8", errors="replace").splitlines()[-100:]:
                lines.lines.append(line)
        except OSError:
            pass
    return logger, lines


ANIMALS = ["Fox", "Wolf", "Bear", "Tiger", "Lion", "Panda", "Koala", "Otter", "Badger", "Falcon", "Eagle", "Hawk",
           "Owl", "Raven", "Shark", "Dolphin", "Orca", "Whale", "Octopus", "Turtle", "Gecko", "Cobra", "Viper",
           "Lynx", "Puma", "Jaguar", "Cheetah", "Leopard", "Zebra", "Giraffe", "Rhino", "Hippo", "Bison", "Moose",
           "Elk", "Yak", "Camel", "Llama", "Alpaca", "Kangaroo", "Wombat", "Hedgehog", "Squirrel", "Beaver",
           "Raccoon", "Meerkat", "Mongoose", "Ferret", "Weasel", "Penguin", "Pelican", "Flamingo", "Parrot",
           "Toucan", "Heron", "Swan", "Crane", "Bat", "Mole", "Hare", "Rabbit", "Goat", "Ram", "Stallion", "Mustang",
           "Gorilla", "Lemur", "Sloth", "Armadillo", "Coyote", "Jackal", "Hyena", "Walrus", "Seal", "Manta",
           "Barracuda", "Marlin", "Piranha", "Salmon", "Crab", "Lobster", "Scorpion", "Mantis", "Hornet", "Beetle"]


class Controller:
    """One bot: its folder, settings, Bot object and thread."""

    name_for_blank = staticmethod(lambda: random.choice(ANIMALS))   # the App swaps in one that avoids taken names

    def __init__(self, bot_id: str, folder: Path, pm: Polymarket = PM, log_key: str = ""):
        self.id, self.folder, self.pm = bot_id, folder, pm
        self.log_key = log_key or bot_id        # unique across users in hosted mode ("u7/btc-5m")
        self.config_path = folder / "config.json"
        self.lock = None
        self.locked_elsewhere = False
        try:
            self.lock = acquire_lock(folder)
        except AlreadyRunning:
            self.locked_elsewhere = True
        self.log, self.lines = bot_logger(self.log_key, folder)
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.last_check: dict = {}             # live: the last Check connection result
        self.bot = self._make_bot(load_config(self.config_path))

    def _make_bot(self, cfg: BotConfig) -> Bot:
        if cfg.is_live:
            client = LiveClient(self.folder, self.pm, logger=self.log)
        else:
            client = PaperClient(cfg.paper_file, cfg.paper_balance_usd, self.pm)
        return Bot(cfg, client, self.pm, self.log)

    def wallet(self) -> Optional[float]:
        """The whole account's balance, or None when it can't be read (live without keys, network down)."""
        try:
            return self.bot.client.balance()
        except Exception:  # noqa: BLE001 - the page shows "–" and the Keys panel says why
            return None

    def balance(self) -> Optional[float]:
        """The bot's own balance: Budget + profit since Reset - the open bet when a Budget is set (each bot plays
        with its budget), else the account's. None when the account can't be read."""
        w = self.wallet()
        return None if w is None else self.bot.own_balance(w)

    @property
    def cfg(self) -> BotConfig:
        return self.bot.cfg

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def _check_owned(self) -> None:
        if self.locked_elsewhere:
            raise ValueError("This bot is open in another window of the app. Close it there first.")

    # --- actions -----------------------------------------------------------------------------------------
    def start(self) -> dict:
        self._check_owned()
        if self.running:
            return self.status()
        self.pm.series_id(self.cfg.coin, self.cfg.timeframe)      # fails early for a coin without that timeframe
        if self.cfg.is_live:
            self.last_check = self.bot.client.check()            # keys, location, approvals: refuse now, not later
            self.log.info("LIVE trading with wallet %s, balance $%.2f, budget $%.2f", self.last_check["wallet"],
                          self.last_check["balance"], self.cfg.budget_usd)
        self.bot.on_start()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.bot.run, args=(self.stop_event,), daemon=True, name=self.id)
        self.thread.start()
        (self.folder / RUNNING_MARK).touch()        # start it again when the app restarts
        return self.status()

    @property
    def should_resume(self) -> bool:
        """Was this bot running when the app last stopped? (Not if it halted by itself since.)"""
        return (self.folder / RUNNING_MARK).exists() and not self.bot.halted and not self.locked_elsewhere

    def stop(self, keep_mark: bool = False) -> dict:
        """Stop the bot. keep_mark=True when the whole app is shutting down: it starts again with the app."""
        if not keep_mark:
            (self.folder / RUNNING_MARK).unlink(missing_ok=True)
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)
        if self.bot.bet:
            self.log.info("Stopped with a bet open on %s; it's paid out when the bot runs again", self.bot.bet["title"])
        return self.status()

    def stop_after_win(self) -> dict:
        """Stop at the next winning bet (the bot halts itself as "after_win")."""
        self._check_owned()
        if not self.running:
            raise ValueError("The bot isn't running.")
        self.bot.stop_after_win = True
        self.bot.save()
        self.log.info("Stop after a win: the bot stops at its next winning bet")
        return self.status()

    def cancel_after_win(self) -> dict:
        self._check_owned()
        if self.bot.stop_after_win:
            self.bot.stop_after_win = False
            self.bot.save()
            self.log.info("No longer stopping after a win")
        return self.status()

    def reset(self) -> dict:
        self._check_owned()
        if self.running:
            raise ValueError("Stop the bot first, then Reset.")
        lad = self.bot.ladder
        before = f"was step {lad.step}, profit since Reset {'+' if lad.pnl >= 0 else '-'}${abs(lad.pnl):,.2f}"
        self.bot.reset()
        append_event(self.folder, "reset", f"Reset: fresh ladder at step 1, profit counted from 0 ({before})")
        return self.status()

    def reset_ladder(self) -> dict:
        """Back to step 1 only: Profit since Reset and the win/loss counts stay (the streak's loss is accepted)."""
        self._check_owned()
        if self.running:
            raise ValueError("Stop the bot first, then Reset.")
        lad = self.bot.ladder
        before = f"was step {lad.step}" + (f", streak lost ${lad.streak_lost:,.2f}" if lad.streak_lost else "")
        self.bot.reset(keep_counts=True)
        append_event(self.folder, "reset", f"Reset only ladder: back to step 1, profit and wins/losses kept ({before})")
        return self.status()

    def paper_reset(self) -> dict:
        """A fresh paper account (Paper balance), no open bet, and a fresh ladder. The trade log is kept."""
        self._check_owned()
        if self.running:
            raise ValueError("Stop the bot first, then Reset paper.")
        self.bot.client.reset(self.cfg.paper_balance_usd)
        self.bot.bet = None
        self.bot.reset()
        self.log.info("Paper account reset to $%.2f", self.cfg.paper_balance_usd)
        append_event(self.folder, "paper_reset", f"Reset paper: balance ${self.cfg.paper_balance_usd:,.2f}, "
                                                 f"fresh ladder at step 1")
        return self.status()

    def put_settings(self, body: dict) -> dict:
        self._check_owned()
        new_network = str(body.get("network") or self.cfg.network)
        if new_network != self.cfg.network and (self.running or self.bot.bet):
            raise ValueError("Stop the bot and let its open bet pay out before switching between paper and live.")
        if "bot_name" in body and not str(body["bot_name"] or "").strip():
            body = {**body, "bot_name": self.name_for_blank()}
        old = self.cfg.settings()
        cfg = save_settings(self.config_path, body)
        changed = settings_diff(old, cfg.settings())
        if changed:
            append_event(self.folder, "settings", "Settings: " + " · ".join(changed))
        if (cfg.coin, cfg.timeframe) != (self.cfg.coin, self.cfg.timeframe):
            self.pm.series_id(cfg.coin, cfg.timeframe)            # tell now, not at Start
        if cfg.network != self.cfg.network:
            self.bot = self._make_bot(cfg)                        # own client, ladder and files per network
            self.last_check = {}
            self.log.info("Switched to %s", "LIVE trading (real money)" if cfg.is_live else "paper trading")
        else:
            if cfg.is_paper and cfg.paper_balance_usd != self.cfg.paper_balance_usd:
                bal = self.bot.client.set_start(cfg.paper_balance_usd)
                self.log.info("Paper balance set to $%.2f: balance now $%.2f (profit kept)", cfg.paper_balance_usd, bal)
            self.bot.cfg = cfg   # the loop reads cfg every tick, so a running bot uses the new settings at once
        self.log.info("Settings saved")
        return self.status()

    def put_keys(self, body: dict) -> dict:
        self._check_owned()
        if self.running:
            raise ValueError("Stop the bot first, then change its keys.")
        view = save_keys(self.folder, body)
        if self.cfg.is_live:
            self.bot = self._make_bot(self.cfg)                   # sign in again with the new keys
        self.last_check = {}
        self.log.info("Polymarket keys saved")
        return self.status()

    def check(self) -> dict:
        """Sign in with the saved keys and report the wallet, balance and approvals (works for a paper bot too)."""
        self._check_owned()
        client = self.bot.client if self.cfg.is_live else LiveClient(self.folder, self.pm, logger=self.log)
        self.last_check = client.check()
        self.log.info("Polymarket connection OK: wallet %s (%s), balance $%.2f", self.last_check["wallet"],
                      self.last_check["wallet_type"], self.last_check["balance"])
        return self.status()

    def plan(self, body: dict) -> dict:
        """The ladder for the settings in `body` (unsaved form values) at the price in body['price']."""
        values = {**self.cfg.settings(), **{k: v for k, v in body.items() if k in FIELDS}}
        cfg = BotConfig(self.folder, **values)
        cfg.validate()
        price = float(body.get("price") or plan_price(cfg))
        if not 0.02 <= price <= 0.98:
            raise ValueError("Price must be between 0.02 and 0.98")
        lad = self.bot.ladder
        if body.get("from_now"):      # the current streak with these settings; else a fresh ladder from step 1
            return self._ladder(cfg, price, lad.step, lad.base if cfg.target_steps == self.cfg.target_steps else 0)
        return self._ladder(cfg, price, 1, 0.0, fresh=True)

    def simulate(self, body: dict) -> dict:
        """These settings (the form's, saved or not) over many bets in a fair market. See simulate.py."""
        values = {**self.cfg.settings(), **{k: v for k, v in (body.get("settings") or {}).items() if k in FIELDS}}
        cfg = BotConfig(self.folder, **values)
        cfg.validate()
        try:
            bankroll = float(body.get("bankroll") or 0)
            bets, runs = int(body.get("bets") or 1000), int(body.get("runs") or 1000)
            chance = body.get("win_chance")
            chance = None if chance in (None, "") else float(chance) / 100
        except (TypeError, ValueError):
            raise ValueError("Bets, runs, budget and win chance must be numbers")
        if bankroll <= 0:
            bankroll = self._budget(cfg)
        rounds = None
        if body.get("market", "real") == "real":           # replay past rounds (history.py)
            history = HISTORY if self.pm is PM else History(self.pm)
            rounds, progress = history.rounds(cfg.coin, cfg.timeframe)
            if len(rounds) < MIN_ROUNDS:
                if progress and progress.get("error") and not (progress["total"] or progress["done"]):
                    raise ValueError(f"Couldn't load past rounds from Polymarket: {progress['error']}")
                return {"loading": progress or {"done": 0, "total": 0}, "have": len(rounds)}
            if cfg.move_mode:                                 # needs the coin's own price inside each round
                paths, coin_progress = COIN_PATHS.paths(cfg.coin, cfg.timeframe, rounds)
                with_path = [{**r, "coin": paths[r["slug"]]} for r in rounds if r["slug"] in paths]
                if coin_progress or len(with_path) < MIN_ROUNDS:
                    if not coin_progress and len(with_path) < MIN_ROUNDS:
                        raise ValueError(f"Couldn't load {cfg.coin}'s past prices (Binance {cfg.coin}USDT) for "
                                         f"the move mode.")
                    return {"loading": {**coin_progress, "what": f"{cfg.coin} prices"}, "have": len(with_path)}
                rounds = with_path
            chance = None
        out = simulate(cfg, plan_price(cfg), bankroll, bets, runs, chance, rounds=rounds)
        out["pace_per_day"] = bet_pace(read_trades(self.cfg.trade_log))
        self.log.info("Simulated %d runs x %d bets from $%.2f at %.2f: average %+.2f, %.0f%% end in profit", runs,
                      bets, bankroll, out["price"], out["average"], out["profit_chance"] * 100)
        return out

    def _ladder(self, cfg: BotConfig, price: float, step: int, base: float, fresh: bool = False) -> dict:
        """ladder_view for the current streak (or a fresh one at step 1), with Steps to survive worked out."""
        lad = self.bot.ladder
        lost, wins_left = (0.0, 0) if fresh else (lad.streak_lost, lad.wins_left)
        budget = self._budget(cfg) + lad.streak_lost
        sizing, base = streak_sizing(cfg, step, base, budget - lost, lost > 0)
        played = streak_bets(read_trades(self.cfg.trade_log), step) if step > 1 else None
        bet = None if fresh else self.bot.bet
        if bet and bet.get("step") == step:
            # the open bet as it was really placed; the steps after it priced at its real price (or the settings'
            # price when that is dearer), so the table agrees with Current bet
            price = max(price, float(bet["price"]))
            view = ladder_view(sizing or cfg, price, budget, step + 1, lost + bet["cost"], played, 0)
            part = recover_part(cfg, lost, wins_left) if cfg.sizing == "recover" else lost
            row = {"step": step, "shares": bet["shares"], "cost": round(bet["cost"], 2),
                   "win_profit": round(bet["shares"] - bet["cost"] - part, 2),
                   "lost_total": round(lost + bet["cost"], 2), "fits": True, "open": True,
                   "price": float(bet["price"]), "side": bet["side"], "left_after_win": round(lost - part, 2)}
            n_played = len(played or [])
            view["rows"] = view["rows"][:n_played] + [row] + view["rows"][n_played:]
            view.update(step=step, lost=round(lost, 2), left=round(budget - lost, 2), fits=view["fits"] + 1,
                        open_price=float(bet["price"]))
        else:
            view = ladder_view(sizing or cfg, price, budget, step, lost, played, wins_left)
        view["recover_wins"] = cfg.recover_wins if cfg.sizing == "recover" else 1
        view["wins_left"] = (wins_left or view["recover_wins"]) if lost > 0 else 0
        view["auto"] = None if not cfg.target_steps else {
            "steps": cfg.target_steps, "base": base if sizing else None, "price": buy_cap(cfg),
            "label": "Profit per win" if cfg.sizing == "recover" else "Starting bet"}
        return view

    def _budget(self, cfg: BotConfig) -> float:
        bal = (self.wallet() or 0.0) + (self.bot.bet["cost"] if self.bot.bet else 0.0)
        if cfg.budget_usd > 0:
            return max(0.0, min(bal, cfg.budget_usd + self.bot.ladder.pnl))
        return bal

    # --- views -------------------------------------------------------------------------------------------
    def summary(self, full: bool = False) -> dict:
        """The bot list's card. full=False: only what status() repeats anyway (no balance or ladder work)."""
        b = self.bot
        out = {"id": self.id, "name": self.cfg.display_name, "coin": self.cfg.coin, "timeframe": self.cfg.timeframe,
               "network": self.cfg.network, "running": self.running,
               "halted": "" if b.halted == "after_win" else b.halted,     # a stop you asked for isn't a halt
               "stop_after_win": b.stop_after_win and self.running, "step": b.ladder.step,
               "pnl": round(b.ladder.pnl, 2), "bet_side": b.bet["side"] if b.bet else "",
               "locked": self.locked_elsewhere, "wins": b.ladder.wins, "losses": b.ladder.losses,
               "budget": self.cfg.budget_usd > 0}
        if full:
            phase, message = self._phase()
            bal = self.balance()
            try:
                lad = self._ladder(self.cfg, plan_price(self.cfg), b.ladder.step, b.ladder.base)
                nxt = next((r for r in lad["rows"] if r["step"] == b.ladder.step and not r.get("played")), None)
                fits, next_cost = lad["fits"] + b.ladder.step - 1, nxt["cost"] if nxt else None   # steps the budget reaches, lost ones included
            except Exception:                 # a card must never fail the whole list
                fits, next_cost = None, None
            out.update(phase=phase, message=message, balance=None if bal is None else round(bal, 2), fits=fits,
                       next_cost=next_cost, bet_cost=round(b.bet["cost"], 2) if b.bet else None)
        return out

    def _phase(self) -> tuple[str, str]:
        b = self.bot
        if self.running:
            return b.phase, b.message
        if b.halted == "after_win":
            return "idle", b.halt_message
        if b.halted:
            return "halted", b.halt_message
        return "idle", "Stopped. A bet is open; it's paid out when you Start." if b.bet else "Stopped"

    def status(self) -> dict:
        b, now = self.bot, time.time()
        bet = None
        if b.bet:
            bet = {k: b.bet.get(k) for k in ("slug", "title", "side", "step", "shares", "price", "cost", "fee",
                                              "start", "end", "url")}
            bet["mid"] = b.live.get("mid")
            bet["volume"] = b.live.get("volume")
            bet["price_to_beat"] = b.bet.get("price_to_beat")
            bet["coin_price"] = b.live.get("coin_price")
            bet["ends_in"] = max(0, b.bet["end"] - now)
            bet["win_profit"] = round(b.bet["shares"] - b.bet["cost"], 2)
        phase, message = self._phase()
        trades = read_trades(self.cfg.trade_log)
        next_start = b.live.get("next_start") if self.running and not b.bet else None
        return {
            **self.summary(),
            "phase": phase, "message": message, "halt_message": b.halt_message,
            "settings": self.cfg.settings(),
            "balance": None if (bal := self.balance()) is None else round(bal, 2),
            "wallet": None if (w := self.wallet()) is None else round(w, 2),
            "keys": keys_view(read_keys(self.folder)), "live_check": self.last_check,
            "unclaimed": round(b.client.unclaimed(), 2) if hasattr(b.client, "unclaimed") else 0.0,
            "start_balance": round(float(b.client.data.get("start_balance", 0)), 2),
            "budget_left": round(b.budget_left(), 2) if bal is not None else None,
            "until_earn_left": round(b.until_earn_left(), 2),
            "streak_lost": round(b.ladder.streak_lost, 2), "wins": b.ladder.wins, "losses": b.ladder.losses,
            "wins_left": (b.ladder.wins_left or self.cfg.recover_wins) if b.ladder.streak_lost > 0 else 0,
            "bet": bet,
            "next_in": fmt_secs(next_start - now) if next_start else "",
            "ladder": self._ladder(self.cfg, plan_price(self.cfg), b.ladder.step, b.ladder.base),
            "trades": list(reversed(trades[-100:])),
            # Resets and settings changes since the oldest bet shown (all of them while every bet is shown)
            "events": [e for e in read_events(self.folder, 300)
                       if len(trades) <= 100 or e.get("time", "") >= trades[-100]["time"]],
            "trade_count": len(trades),
            "log": list(self.lines.lines)[-150:],
        }


def streak_bets(trades: list[dict], step: int) -> list[dict]:
    """The settled bets of the current losing streak (steps 1 .. step-1), oldest first, from the trade log."""
    need = step - 1
    if need <= 0 or len(trades) < need:
        return []
    last = trades[-need:]
    try:
        if [int(t["step"]) for t in last] != list(range(1, step)) or any(t["result"] == t["side"] for t in last):
            return []
    except (KeyError, ValueError):
        return []
    return last


def ladder_view(cfg: BotConfig, price: float, budget: float, step: int = 1, lost: float = 0.0,
                played: Optional[list] = None, wins_left: int = 0) -> dict:
    """The whole ladder: the streak's steps already played (`played`, real bets from the trade log, all lost), then
    from `step` on what the next bets would cost at `price` after the streak lost `lost`."""
    rows = build_ladder(cfg, price, budget, step=step, lost=lost, wins_left=wins_left)
    done, total = [], 0.0
    for t in played or []:
        total += float(t["cost"])
        done.append({"step": int(t["step"]), "shares": float(t["shares"]), "cost": round(float(t["cost"]), 2),
                     "win_profit": None, "lost_total": round(total, 2), "fits": True, "played": True,
                     "price": float(t["price"]), "side": t["side"]})
    return {"price": price, "budget": round(budget, 2), "left": round(budget - lost, 2), "step": step,
            "lost": round(lost, 2), "fits": steps_that_fit(rows), "rows": done + [r.__dict__ for r in rows]}


class App:
    """All bots in profiles/."""

    max_bots = 0                      # 0 = no limit (MAX_BOTS can set one per user in the hosted app)

    def __init__(self, profiles: Path = PROFILES_DIR, pm: Polymarket = PM, prefix: str = ""):
        self.profiles, self.pm, self.prefix = profiles, pm, prefix
        self.controllers: dict[str, Controller] = {}
        self._order_lock = threading.Lock()
        self.profiles.mkdir(parents=True, exist_ok=True)
        for bot_id in bot_ids(self.profiles):
            try:
                self.controllers[bot_id] = Controller(bot_id, self.profiles / bot_id, pm, prefix + bot_id)
                self.controllers[bot_id].name_for_blank = self.animal_name
            except (ValueError, OSError) as e:
                log.warning("Bot %s can't be loaded: %s", bot_id, e)

    def get(self, bot_id: str) -> Controller:
        if bot_id not in self.controllers:
            raise LookupError(f"no bot {bot_id!r}")
        return self.controllers[bot_id]

    def list(self) -> dict:
        """Every bot: pinned ones first, each group in the order dragged into (see order())."""
        order, pins = self.order()
        rank = {i: n for n, i in enumerate(order)}
        bots = [{**c.summary(full=True), "pinned": c.id in pins} for c in self.controllers.values()]
        bots.sort(key=lambda b: (not b["pinned"], rank.get(b["id"], len(rank))))
        return {"bots": bots, "version": __version__, "started": STARTED}

    # --- list order and pins (like the hyperliquid bot): profiles/.order.json, shared by every browser ---------
    def _read_order(self) -> tuple[list, list]:
        try:
            data = json.loads((self.profiles / ORDER_FILE).read_text())
            return list(data.get("order") or []), list(data.get("pinned") or [])
        except (OSError, ValueError, AttributeError):
            return [], []

    def _write_order(self, order: list, pinned: list) -> None:
        write_json(self.profiles / ORDER_FILE, {"order": order, "pinned": pinned})

    def order(self) -> tuple[list, list]:
        """(every bot id in list order, the pinned ones). Bots not placed yet join at the end, oldest first."""
        order, pinned = self._read_order()
        known = [i for i in order if i in self.controllers]
        new = sorted((i for i in self.controllers if i not in known),
                     key=lambda i: ((self.controllers[i].folder / "config.json").stat().st_ctime
                                    if (self.controllers[i].folder / "config.json").exists() else 0, i))
        pins = [i for i in pinned if i in self.controllers]
        if new or known != order or pins != pinned:
            self._write_order(known + new, pins)
        return known + new, pins

    def set_order(self, body: dict) -> dict:
        """{"order": [ids], "pinned": [ids]} from a drag and drop; unknown ids are dropped, missing ones kept."""
        with self._order_lock:
            old, old_pins = self.order()
            order = [i for i in dict.fromkeys(body.get("order") or []) if i in self.controllers]
            order += [i for i in old if i not in order]
            pins = body.get("pinned", old_pins)
            pins = [i for i in dict.fromkeys(pins or []) if i in self.controllers]
            self._write_order(order, pins)
        return self.list()

    def pin(self, bot_id: str, body: dict) -> dict:
        self.get(bot_id)
        with self._order_lock:
            order, pins = self.order()
            pins = [i for i in pins if i != bot_id] + ([bot_id] if body.get("pinned", True) else [])
            self._write_order(order, pins)
        return self.list()

    def animal_name(self) -> str:
        """A random animal no other bot of this user is called (a blank Bot name gets one)."""
        taken = {c.cfg.display_name.lower() for c in self.controllers.values()}
        free = [a for a in ANIMALS if a.lower() not in taken]
        return random.choice(free) if free else f"{random.choice(ANIMALS)} {random.randint(2, 999)}"

    def create(self, body: dict) -> dict:
        if self.max_bots and len(self.controllers) >= self.max_bots:
            raise ValueError(f"You can have up to {self.max_bots} bots. Delete one to make another.")
        coin = str(body.get("coin") or "BTC").upper()
        tf = str(body.get("timeframe") or "5m")
        if coin not in COINS or tf not in TIMEFRAMES:
            raise ValueError("Pick a coin and a round length")
        self.pm.series_id(coin, tf)          # refuse a coin without that timeframe
        name = str(body.get("name") or "").strip()[:40] or self.animal_name()
        bot_id = free_bot_id(slug(str(body.get("name") or "").strip() or f"{coin}-{tf}"), self.profiles,
                             self.controllers)
        folder = self.profiles / bot_id
        folder.mkdir(parents=True)
        cfg = BotConfig(folder, coin=coin, timeframe=tf, bot_name=name)
        write_json(folder / "config.json", cfg.settings())
        self.controllers[bot_id] = Controller(bot_id, folder, self.pm, self.prefix + bot_id)
        self.controllers[bot_id].name_for_blank = self.animal_name
        log.info("New bot %s%s (%s %s)", self.prefix, bot_id, coin, tf)
        return {"id": bot_id, **self.list()}

    def delete(self, bot_id: str) -> dict:
        ctl = self.get(bot_id)
        if ctl.running:
            raise ValueError("Stop the bot first, then delete it.")
        if ctl.bot.bet:
            raise ValueError("This bot has a bet open. Start it and let the round pay out first.")
        if ctl.lock:
            ctl.lock.close()
        for h in list(ctl.log.handlers):
            ctl.log.removeHandler(h)
            h.close()
        del self.controllers[bot_id]
        shutil.rmtree(ctl.folder)
        log.info("Deleted bot %s%s", self.prefix, bot_id)
        return self.list()

    def stop_all(self) -> None:
        """App shutdown: stop every bot but remember which were running, so they start again with the app."""
        for c in self.controllers.values():
            if c.running:
                c.stop(keep_mark=True)

    def resume(self) -> None:
        for c in self.controllers.values():
            if c.should_resume and not c.running:
                try:
                    c.start()
                    log.info("Started bot %s again (it was running when the app stopped)", c.id)
                except Exception as e:  # noqa: BLE001 - one bot's problem mustn't keep the app down
                    c.log.warning("Couldn't start again after the app restarted: %s", e)


def schema() -> dict:
    return {"fields": [{"key": k, "default": d, "type": t, "options": o, "label": l, "help": h}
                       for k, (d, t, o, l, h) in FIELDS.items()],
            "coins": COINS, "timeframes": TIMEFRAMES, "version": __version__, "started": STARTED}


def bet_pace(rows: list[dict]) -> Optional[float]:
    """Bets per day in this bot's history (None with fewer than 10 bets), to turn simulated bets into days."""
    from datetime import datetime
    times = []
    for r in rows:
        try:
            times.append(datetime.fromisoformat(r["time"]).timestamp())
        except (KeyError, TypeError, ValueError):
            pass
    times.sort()
    if len(times) < 10 or times[-1] - times[0] < 3600:
        return None
    return (len(times) - 1) / ((times[-1] - times[0]) / 86400)


def settings_diff(old: dict, new: dict) -> list[str]:
    """["Hit price 0.75 → 0.7", ...] for every setting that changed (labels as on the form; 0 for "off" fields)."""
    out = []
    for k, (_, typ, _, label, _) in FIELDS.items():
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        show = lambda v: "empty" if v in ("", None) else (f"{v:g}" if isinstance(v, float) else str(v))  # noqa: E731
        out.append(f"{label} {show(a)} → {show(b)}")
    return out


ORDER_FILE = ".order.json"     # in a user's profiles/: the bot list's order and pins

BOT_PATH = re.compile(r"^/api/bots/([A-Za-z0-9_-]{1,30})/([a-z_]+)$")
BOT_POST = {"start": Controller.start, "stop": Controller.stop, "reset": Controller.reset,
            "reset_ladder": Controller.reset_ladder,
            "paper_reset": Controller.paper_reset, "check": Controller.check,
            "stop_after_win": Controller.stop_after_win, "cancel_after_win": Controller.cancel_after_win}
BOT_POST_BODY = {"settings": Controller.put_settings, "plan": Controller.plan, "keys": Controller.put_keys,
                 "simulate": Controller.simulate}


def make_handler(app: Optional[App], public_hosts: tuple = ()):
    """The HTTP handler. The hosted app (hosted.py) subclasses it: _app() is then the signed-in user's bots."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence per-request logging
            pass

        def _app(self) -> Optional[App]:
            return app

        def _page(self) -> bytes:
            return (STATIC / "index.html").read_bytes().replace(b"{{version}}", __version__.encode())

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost", self.server.server_address[0]) or host in public_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            path = self.path.partition("?")[0]
            if path in ("/", "/index.html"):
                return self._send(200, self._page(), "text/html; charset=utf-8")
            if path == "/api/schema":
                return self._call(schema)
            app = self._app()
            if app is None:
                return self._json(401, {"error": "Sign in first."})
            if path == "/api/bots":
                return self._call(app.list)
            m = BOT_PATH.match(path)
            if m and m.group(2) == "status":
                return self._call(lambda: app.get(m.group(1)).status())
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Requested-With") != "bot-ui":
                return self._json(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "bad JSON"})
            app = self._app()
            if app is None:
                return self._json(401, {"error": "Sign in first."})
            if self.path == "/api/bots":
                return self._call(app.create, body)
            if self.path == "/api/order":
                return self._call(app.set_order, body)
            m = BOT_PATH.match(self.path)
            if not m:
                return self._json(404, {"error": "not found"})
            bot_id, action = m.groups()
            if action == "delete":
                return self._call(app.delete, bot_id)
            if action == "pin":
                return self._call(app.pin, bot_id, body)
            if action in ("start", "check"):
                refusal = self._live_refusal(app, bot_id)
                if refusal:
                    return self._json(403, {"error": refusal})
            if action in BOT_POST:
                return self._call(lambda: BOT_POST[action](app.get(bot_id)))
            if action in BOT_POST_BODY:
                return self._call(lambda: BOT_POST_BODY[action](app.get(bot_id), body))
            self._json(404, {"error": "not found"})

        def _live_refusal(self, app: App, bot_id: str) -> str:
            """Why this visitor may not start/check a live bot ("" = fine). Only the hosted app checks locations."""
            return ""

        def _call(self, fn, *args):
            try:
                self._json(200, fn(*args))
            except (BrokenPipeError, ConnectionResetError):
                pass                      # the page went away mid-answer (closed tab, reload)
            except LookupError as e:
                self._json(404, {"error": str(e)})
            except (ValueError, MarketError) as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                log.exception("API error")
                self._json(500, {"error": str(e)})

    return Handler


def setup_logging() -> None:
    use_jst_in_logs()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    to_file = logging.FileHandler(ROOT / "app.log", encoding="utf-8")
    to_file.setFormatter(FORMAT)
    to_term = logging.StreamHandler(sys.stderr)
    to_term.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    root.addHandler(to_file)
    root.addHandler(to_term)
    for noisy in ("httpx", "httpcore", "websockets"):      # the Polymarket SDK logs every HTTP request at INFO
        logging.getLogger(noisy).setLevel(logging.WARNING)


def serve(host: str, port: int, open_browser: bool, public_hosts: tuple = ()) -> None:
    """Run the app. public_hosts: domain names it may also answer to, e.g. ("poly.zodypay.com",). Only use that
    behind a reverse proxy that adds HTTPS and a password (see README "Open it from anywhere")."""
    from .instance import bind_server
    app = App()
    server = bind_server(host, port, make_handler(app, tuple(h.lower() for h in public_hosts)))
    port = server.server_address[1]
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    (ROOT / ".app-url").write_text(url)
    log.info("Web app running at %s with %d bot(s)  (Ctrl+C to quit)", url, len(app.controllers))
    if public_hosts:
        log.info("Also answering to %s (must be behind a password-protected HTTPS proxy)", ", ".join(public_hosts))
    app.resume()
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_all()
        server.server_close()
