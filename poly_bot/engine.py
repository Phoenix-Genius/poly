"""The bot: one loop per bot that bets on one coin's Up or Down rounds with a martingale ladder.

Each tick (every poll_interval_sec) does one of:
- **holding**: a bet is open. Wait until its round ends, then until Polymarket posts the result (1 to 5 minutes
  later), pay it out, and move the ladder: a win goes back to step 1, a loss goes one step up.
- **waiting**: no bet. When a round is running and still inside the entry window, choose the side, size the bet
  for the current step at the side's best ask, and buy it (never above the max price). One bet per round.

The ladder and the open bet are saved after every change (state-<network>.json), so a restart picks up where it was,
including a bet whose round finished while the bot was off.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .config import BotConfig, write_json
from .markets import MarketError, Polymarket
from .strategy import Ladder, choose_side, cost_of, entry_window, shares_for
from .trades import append_trade

SETTLE_CHECK_SEC = 10          # how often to ask whether an ended round has resolved
SLOW_RESULT_SEC = 15 * 60      # warn when a result takes longer than this


class Bot:
    def __init__(self, cfg: BotConfig, client, pm: Polymarket, log: Optional[logging.Logger] = None,
                 clock: Callable[[], float] = time.time, rng: Optional[random.Random] = None):
        self.cfg, self.client, self.pm, self.clock = cfg, client, pm, clock
        self.log = log or logging.getLogger("bot")
        self.rng = rng or random.Random()
        self.ladder = Ladder()
        self.bet: Optional[dict] = None
        self.done_round = ""           # the round already bet on (one bet per round)
        self.halted = ""               # why the bot stopped by itself: "ladder" (needs Reset) or "target"
        self.halt_message = ""
        self.start_pnl = 0.0           # ladder.pnl when Start was pressed (for Until earn)
        self.phase, self.message = "idle", ""
        self.live: dict = {}           # the open bet's live price, for the page
        self._next_settle_check = 0.0
        self._said = ""                # last waiting message logged (so it's logged once, not every tick)
        self._slow_warned = False
        self.load()

    # --- state -------------------------------------------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.cfg.state_file

    def load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            d = json.loads(self.state_path.read_text())
        except ValueError:
            self.log.warning("state file unreadable, starting a fresh ladder")
            return
        self.ladder = Ladder.from_dict(d.get("ladder") or {})
        self.bet = d.get("bet")
        self.done_round = d.get("done_round", "")
        self.halted = d.get("halted", "")
        self.halt_message = d.get("halt_message", "")
        self.start_pnl = float(d.get("start_pnl", 0.0))

    def save(self) -> None:
        write_json(self.state_path, {"ladder": self.ladder.to_dict(), "bet": self.bet, "done_round": self.done_round,
                                     "halted": self.halted, "halt_message": self.halt_message,
                                     "start_pnl": self.start_pnl})

    def reset(self) -> None:
        """A fresh ladder at step 1 with zero profit. Refused while a bet is open (its money is in the round)."""
        if self.bet:
            raise ValueError("A bet is open. Wait until its round is paid out, then Reset.")
        self.ladder, self.done_round, self.halted, self.halt_message, self.start_pnl = Ladder(), "", "", "", 0.0
        self.save()
        self.log.info("Reset: fresh ladder at step 1")

    def on_start(self) -> None:
        if self.halted == "ladder":
            raise ValueError(f"{self.halt_message} Press Reset to start a new ladder.")
        self.halted, self.halt_message = "", ""
        self.start_pnl = self.ladder.pnl
        self.save()

    # --- numbers -----------------------------------------------------------------------------------------
    def budget_left(self) -> float:
        """What the next bet may cost: the budget plus profit since Reset (capped by the balance)."""
        bal = self.client.balance()
        if self.cfg.budget_usd > 0:
            return max(0.0, min(bal, self.cfg.budget_usd + self.ladder.pnl))
        return bal

    def earned_since_start(self) -> float:
        return self.ladder.pnl - self.start_pnl

    # --- loop --------------------------------------------------------------------------------------------
    def run(self, stop: threading.Event) -> None:
        self.log.info("Started: %s %s rounds, %s sizing, side %s, step %d", self.cfg.coin, self.cfg.timeframe,
                      self.cfg.sizing, self.cfg.side_mode, self.ladder.step)
        while not stop.is_set() and not self.halted:
            try:
                self.tick()
            except MarketError as e:
                self._wait(f"Polymarket not reachable, retrying: {e}", key="net", level=logging.WARNING)
            except ValueError as e:      # a plain-words problem (e.g. a live order refused); shown and retried
                self._wait(f"{e} Retrying.", key=f"err:{e}", level=logging.WARNING)
            except Exception:  # noqa: BLE001 - keep the bot alive; the log has the details
                self.log.exception("Unexpected error; retrying")
            stop.wait(self.cfg.poll_interval_sec)
        self.phase = "halted" if self.halted else "idle"
        self.log.info("Stopped%s", f": {self.halt_message}" if self.halted else "")

    def tick(self) -> None:
        if self.halted:
            return
        if hasattr(self.client, "housekeeping"):
            self.client.housekeeping()        # live: retry claims of won rounds
        if self.bet:
            self._hold()
        else:
            self._enter()

    def _wait(self, message: str, key: str = "", level: int = logging.INFO) -> None:
        """Show `message` on the page; log it once per `key` (e.g. once per round), not every tick."""
        self.phase, self.message = "waiting", message
        key = key or message
        if key != self._said:
            self._said = key
            self.log.log(level, message)

    def _halt(self, kind: str, message: str) -> None:
        self.halted, self.halt_message = kind, message
        self.phase, self.message = "halted", message
        self.save()
        self.log.warning(message)

    def _ladder_end(self, why: str) -> None:
        if self.cfg.on_ladder_end == "reset" and self.ladder.step > 1:
            self.log.warning("%s Ladder end: accepting the streak's loss of $%.2f and starting again at step 1.",
                             why, self.ladder.streak_lost)
            self.ladder.restart()
            self.save()
            return
        self._halt("ladder", f"Ladder ended at step {self.ladder.step}: {why}")

    # --- holding -----------------------------------------------------------------------------------------
    def _hold(self) -> None:
        bet, now = self.bet, self.clock()
        if now < bet["end"]:
            self.phase = "holding"
            self.message = f"Bet {bet['side'].upper()} on {bet['title']}"
            if now >= self.live.get("next", 0):
                mid = self.pm.midpoint(bet["token"])
                self.live = {"mid": mid, "next": now + 5}
            return
        self.phase, self.message = "settling", "Round ended, waiting for Polymarket's result"
        if now < self._next_settle_check:
            return
        self._next_settle_check = now + SETTLE_CHECK_SEC
        r = self.pm.round(bet["slug"])
        if not r.result:
            if now - bet["end"] > SLOW_RESULT_SEC and not self._slow_warned:
                self._slow_warned = True
                self.log.warning("The result of %s is taking long (%d min); still waiting", bet["slug"],
                                 (now - bet["end"]) // 60)
            return
        self._settle(r.result)

    def _settle(self, result: str) -> None:
        bet = self.bet
        payout = self.client.settle(bet["slug"], result)
        won = result == bet["side"]
        profit = round(payout - bet["cost"], 6)
        self.ladder.record(bet["side"], won, profit)
        self.bet, self.live, self._slow_warned = None, {}, False
        self.save()
        append_trade(self.cfg.trade_log, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.clock() + 9 * 3600)),
            "round": bet["slug"], "title": bet["title"], "side": bet["side"], "step": bet["step"],
            "shares": bet["shares"], "price": round(bet["price"], 4), "cost": round(bet["cost"], 4),
            "fee": round(bet["fee"], 4), "result": result, "payout": round(payout, 4), "pnl": round(profit, 4),
            "total_pnl": round(self.ladder.pnl, 4), "balance": round(self.client.balance(), 4),
            "placed": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(bet["placed"] + 9 * 3600)) if bet.get("placed") else "",
            "held_sec": int(self.clock() - bet["placed"]) if bet.get("placed") else "",
            "best_price": bet.get("best_price", ""),
            "slippage": round(max(0.0, bet["price"] - bet["best_price"]) * bet["shares"], 4)
            if bet.get("best_price") else ""})
        self.log.info("%s %s: bet %s at %.3f, round went %s, %s$%.2f (total %s$%.2f)",
                      "WIN " if won else "LOSS", bet["title"], bet["side"].upper(), bet["price"], result.upper(),
                      "+" if profit >= 0 else "-", abs(profit), "+" if self.ladder.pnl >= 0 else "-",
                      abs(self.ladder.pnl))
        if self.cfg.stop_profit_usd > 0 and self.earned_since_start() >= self.cfg.stop_profit_usd - 1e-9:
            self._halt("target", f"Earned ${self.earned_since_start():.2f} since Start, the Until earn target.")
            return
        if not won and self.cfg.max_steps and self.ladder.step > self.cfg.max_steps:
            self._ladder_end(f"{self.cfg.max_steps} losses in a row (Max losses in a row).")

    # --- entering ----------------------------------------------------------------------------------------
    def _enter(self) -> None:
        cfg, now = self.cfg, self.clock()
        cur, nxt = self.pm.current_round(cfg.coin, cfg.timeframe)
        later = f"next round starts in {fmt_secs(nxt.start - now)}" if nxt else "waiting for the next round"
        self.live = {"next_start": nxt.start if nxt else None}
        if not cur or cur.slug == self.done_round:
            return self._wait(f"Bet placed for this round; {later}" if cur else f"No round running; {later}",
                              key=f"done:{cur.slug}" if cur else "none")
        elapsed, window = now - cur.start, entry_window(cfg.timeframe, cfg.entry_window_sec)
        if elapsed > window:
            return self._wait(f"Too late for the current round ({fmt_secs(elapsed)} in, entry window "
                              f"{fmt_secs(window)}); {later}", key=f"late:{cur.slug}")
        if not cur.accepting:
            return self._wait(f"{cur.title} isn't taking orders; {later}", key=f"closed:{cur.slug}")
        last = None
        if cfg.side_mode in ("follow_last", "opposite_last"):
            found = self.pm.last_result(cfg.coin, cfg.timeframe)
            last = found[1] if found else None
        prices = self.pm.best_asks(cur)
        side = choose_side(cfg, self.ladder, last, prices, self.rng)
        price = prices.get(side)
        if price is None or price > cfg.max_price + 1e-9:
            return self._wait(f"{side.upper()} costs {price if price is not None else 'n/a'}, above the max price "
                              f"{cfg.max_price:.2f}; waiting (window ends in {fmt_secs(window - elapsed)})",
                              key=f"price:{cur.slug}:{side}")
        if price < cfg.min_price - 1e-9:
            return self._wait(f"{side.upper()} costs {price}, below the min price {cfg.min_price:.2f}; waiting "
                              f"(window ends in {fmt_secs(window - elapsed)})", key=f"cheap:{cur.slug}:{side}")
        shares = max(shares_for(cfg, self.ladder.step, self.ladder.streak_lost, price, cur.fee_rate,
                                cur.fee_exponent), cur.min_size)
        est = cost_of(shares, price, cur.fee_rate, cur.fee_exponent)
        if est > self.budget_left() + 1e-9:
            return self._ladder_end(f"the step {self.ladder.step} bet costs ${est:.2f} but only "
                                    f"${self.budget_left():.2f} of the budget is left.")
        fill = self.client.buy(cur, side, shares, cfg.max_price)
        if fill is None:
            return self._wait(f"Not enough {side.upper()} shares for sale up to {cfg.max_price:.2f}; retrying",
                              key=f"thin:{cur.slug}")
        self.bet = {"slug": cur.slug, "title": cur.title, "side": side, "token": cur.token(side), "step": self.ladder.step,
                    "shares": fill.shares, "price": fill.avg_price, "cost": fill.cost, "fee": fill.fee,
                    "start": cur.start, "end": cur.end, "placed": now, "url": cur.url, "best_price": price,
                    "last_result": last}
        self.ladder.rounds_bet += 1
        self.done_round = cur.slug
        self._said = ""
        self._next_settle_check = 0.0
        self.save()
        self.phase, self.message = "holding", f"Bet {side.upper()} on {cur.title}"
        self.log.info("BET  step %d: %s %.2f shares at %.3f = $%.2f (fee $%.2f) on %s; a win pays $%.2f",
                      self.ladder.step, side.upper(), fill.shares, fill.avg_price, fill.cost, fill.fee, cur.title,
                      fill.shares)


def fmt_secs(s: float) -> str:
    s = max(0, int(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"
