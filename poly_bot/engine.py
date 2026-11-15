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
from .strategy import (Ladder, buy_cap, choose_side, conditions_text, cost_of, entry_span, leader_of, sides_allowed,
                       shares_for_book, streak_sizing, walk_asks)
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
        self.halted = ""               # why the bot stopped by itself: "ladder" (needs Reset), "target", "after_win"
        self.stop_after_win = False    # Stop after a win: stop at the next winning bet
        self.halt_message = ""
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
        self.stop_after_win = bool(d.get("stop_after_win"))

    def save(self) -> None:
        write_json(self.state_path, {"ladder": self.ladder.to_dict(), "bet": self.bet, "done_round": self.done_round,
                                     "halted": self.halted, "halt_message": self.halt_message,
                                     "stop_after_win": self.stop_after_win})

    def reset(self, keep_counts: bool = False) -> None:
        """A fresh ladder at step 1 with zero profit; keep_counts ("Reset only ladder") keeps Profit since Reset and
        the win/loss counts, accepting the streak's loss. An open bet whose round has a result is settled first (a
        stopped bot doesn't settle by itself); one still running (or without a result yet) refuses the Reset."""
        if self.bet:
            self.settle_ended()
        if self.bet:
            raise ValueError("A bet is open. Wait until its round is paid out, then Reset.")
        if keep_counts:
            self.ladder.restart()
        else:
            self.ladder = Ladder()
        self.done_round, self.halted, self.halt_message = "", "", ""
        self.stop_after_win = False
        self.save()
        self.log.info("Reset: fresh ladder at step 1%s", " (profit and counts kept)" if keep_counts else "")

    def on_start(self) -> None:
        if self.halted == "ladder" and not self.waits_at_step1():
            raise ValueError(f"{self.halt_message} Press Reset to start a new ladder.")
        if self.target_reached():
            raise ValueError(f"Already earned ${self.ladder.pnl:.2f} since Reset, the Until earn target of "
                             f"${self.cfg.stop_profit_usd:.2f}. Press Reset to start counting again, or raise "
                             f"Until earn.")
        self.halted, self.halt_message, self.stop_after_win = "", "", False
        self.save()

    def waits_at_step1(self) -> bool:
        """When the ladder ends = reset and the bot is at step 1: a bet it can't pay is skipped (it waits for a round
        where the bet fits) instead of stopping, as there is no streak to give up."""
        return self.cfg.on_ladder_end == "reset" and self.ladder.step == 1

    # --- numbers -----------------------------------------------------------------------------------------
    def budget_left(self) -> float:
        """What the next bet may cost: the budget plus profit since Reset (capped by the balance)."""
        bal = self.client.balance()
        if self.cfg.budget > 0:
            return max(0.0, min(bal, self.cfg.budget + self.ladder.pnl))
        return bal

    def bet_value(self) -> float:
        """What the open bet is worth now: its shares at the side's price now (5 s old at most while holding; its
        buy price until the first check)."""
        if not self.bet:
            return 0.0
        mid = self.live.get("mid")
        return self.bet["shares"] * float(mid if mid is not None else self.bet["price"])

    def own_balance(self, wallet: Optional[float] = None) -> Optional[float]:
        """The bot's balance as shown: with a Budget, its own money (Budget + profit since Reset, with the open bet
        at what it's worth now, so it moves with the price), so several bots on one account each show only theirs;
        without one, the account's cash plus this bot's open bet at what it's worth now (the cash already paid for it)."""
        if self.cfg.budget > 0:
            open_now = self.bet_value() - self.bet["cost"] if self.bet else 0.0
            return max(0.0, self.cfg.budget + self.ladder.pnl + open_now)
        return (self.client.balance() if wallet is None else wallet) + self.bet_value()

    def sizing(self):
        """The sizing for the next bet (see strategy.streak_sizing); remembers a new streak's Steps-to-survive bet."""
        cfg, lad = self.cfg, self.ladder
        sizing, base = streak_sizing(cfg, lad.step, lad.base, self.budget_left(), lad.streak_lost > 0)
        if sizing is not None and cfg.survive_steps and base != lad.base:
            lad.base = base
            self.log.info("Steps to survive %d: %s $%.2f for this streak (budget left $%.2f, at price %.2f)",
                          cfg.survive_steps, "Profit per win" if cfg.sizing == "recover" else "Starting bet",
                          base, self.budget_left(), buy_cap(cfg))
        return sizing

    def target_reached(self) -> bool:
        """Until earn counts the profit since Reset (ladder.pnl), so a restart or Stop/Start doesn't move it."""
        return self.cfg.stop_profit_usd > 0 and self.ladder.pnl >= self.cfg.stop_profit_usd - 1e-9

    def until_earn_left(self) -> float:
        return max(0.0, self.cfg.stop_profit_usd - self.ladder.pnl) if self.cfg.stop_profit_usd > 0 else 0.0

    # --- loop --------------------------------------------------------------------------------------------
    def run(self, stop: threading.Event) -> None:
        self.log.info("Started: %s %s rounds, %s sizing, %s, step %d", self.cfg.coin, self.cfg.timeframe,
                      self.cfg.sizing, f"conditions: {conditions_text(self.cfg)} (pays at most {buy_cap(self.cfg):.2f})"
                      if self.cfg.conditions_mode else f"side {self.cfg.side_mode}", self.ladder.step)
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

    def _ladder_end(self, why: str, key: str = "") -> None:
        """The next bet can't be paid: stop, or (When the ladder ends = reset) accept the loss and go back to step 1.
        Either way it's logged in History. At step 1 with reset there's nothing to give up: wait for a round where
        the bet fits (the price, so the cost, changes from round to round)."""
        if self.waits_at_step1():
            return self._wait(f"Can't pay this round: {why} Waiting for a round where the step 1 bet fits "
                              f"(When the ladder ends = reset).", key=key or f"short:{why}", level=logging.WARNING)
        if self.cfg.on_ladder_end == "reset" and self.ladder.step > 1:
            self.log.warning("%s Ladder end: accepting the streak's loss of $%.2f and starting again at step 1.",
                             why, self.ladder.streak_lost)
            self._event("ladder_end", f"Ladder ended at step {self.ladder.step}: {why} Reset by itself: the streak's "
                                      f"${self.ladder.streak_lost:,.2f} loss is accepted, back to step 1.")
            self.ladder.restart()
            self.save()
            return
        if self.halted != "ladder":
            self._event("ladder_end", f"Ladder ended at step {self.ladder.step}: {why} The bot stopped "
                                      f"(streak lost ${self.ladder.streak_lost:,.2f}); press Reset.")
        self._halt("ladder", f"Ladder ended at step {self.ladder.step}: {why}")

    def _event(self, kind: str, text: str) -> None:
        from .trades import append_event
        try:
            append_event(self.cfg.folder, kind, text)
        except OSError as e:
            self.log.warning("Couldn't log %s in History: %s", kind, e)

    # --- holding -----------------------------------------------------------------------------------------
    def _hold(self) -> None:
        bet, now = self.bet, self.clock()
        if now < bet["end"]:
            self.phase = "holding"
            self.message = f"Bet {bet['side'].upper()} on {bet['title']}"
            if now >= self.live.get("next", 0):
                mid = self.pm.midpoint(bet["token"])
                self.live = {**self.live, "mid": mid, "next": now + 5}
            pf = getattr(self.pm, "coin_prices", None)
            if pf is not None and pf.available and bet.get("price_to_beat"):   # the coin vs the beat (60 s TWAP)
                twap = pf.twap(self.cfg.coin) if hasattr(pf, "twap") else None
                self.live["coin_price"] = twap if twap is not None else pf.price(self.cfg.coin)
            feed = getattr(self.pm, "volumes", None)
            v = feed.volume(bet["slug"]) if feed else None
            if v:                                          # real time, from Polymarket's trade stream
                self.live["volume"] = v
            elif now >= self.live.get("next_volume", 0):   # no stream: Gamma's number (lags), every 15 s
                self.live["next_volume"] = now + 15
                try:
                    self.live["volume"] = {"usd": self.pm.round(bet["slug"]).volume, "partial": True, "lagging": True}
                except Exception:  # noqa: BLE001 - only shown on the page
                    pass
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

    def _coin_end(self, bet: dict) -> Optional[float]:
        """The coin's Chainlink price at the bet's round end, for History (None if unknown)."""
        feed = getattr(self.pm, "coin_prices", None)
        if feed is None or not getattr(feed, "available", False) or not bet.get("price_to_beat"):
            return None
        try:
            twap = feed.twap(self.cfg.coin, bet["end"]) if hasattr(feed, "twap") else None
            return twap if twap is not None else feed.close_price(self.cfg.coin, bet["start"], bet["end"],
                                                                  self.cfg.timeframe)
        except Exception:  # noqa: BLE001 - only for the page
            return None

    def settle_ended(self) -> bool:
        """Settle the open bet now if its round has ended and Polymarket has its result (used while stopped)."""
        if not self.bet or self.clock() < self.bet["end"]:
            return False
        r = self.pm.round(self.bet["slug"])
        if not r.result:
            return False
        self._settle(r.result)
        return True

    def _settle(self, result: str) -> None:
        bet = self.bet
        payout = self.client.settle(bet["slug"], result)
        won = result == bet["side"]
        profit = round(payout - bet["cost"], 6)
        recover = self.cfg.sizing == "recover"
        base = bet.get("base_profit", self.cfg.base_profit_usd) if recover else None
        self.ladder.record(bet["side"], won, profit, self.cfg.recover_wins if recover else 1, base)
        self.bet, self.live, self._slow_warned = None, {}, False
        self.save()
        append_trade(self.cfg.trade_log, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.clock() + 9 * 3600)),
            "round": bet["slug"], "title": bet["title"], "side": bet["side"], "step": bet["step"],
            "shares": bet["shares"], "price": round(bet["price"], 4), "cost": round(bet["cost"], 4),
            "fee": round(bet["fee"], 4), "result": result, "payout": round(payout, 4), "pnl": round(profit, 4),
            "total_pnl": round(self.ladder.pnl, 4), "balance": round(self.own_balance(), 4),
            "placed": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(bet["placed"] + 9 * 3600)) if bet.get("placed") else "",
            "held_sec": int(self.clock() - bet["placed"]) if bet.get("placed") else "",
            "best_price": bet.get("best_price", ""),
            "slippage": round(max(0.0, bet["price"] - bet["best_price"]) * bet["shares"], 4)
            if bet.get("best_price") else "",
            "beat": bet.get("price_to_beat") or "", "coin_at_bet": bet.get("coin_at_bet") or "",
            "coin_end": self._coin_end(bet) or ""})
        self.log.info("%s %s: bet %s at %.3f, round went %s, %s$%.2f (total %s$%.2f)",
                      "WIN " if won else "LOSS", bet["title"], bet["side"].upper(), bet["price"], result.upper(),
                      "+" if profit >= 0 else "-", abs(profit), "+" if self.ladder.pnl >= 0 else "-",
                      abs(self.ladder.pnl))
        if won and self.ladder.streak_lost > 0:
            self.log.info("Still to win back from the streak: $%.2f over %d more win(s) (Recover over %d wins)",
                          self.ladder.streak_lost, self.ladder.wins_left, self.cfg.recover_wins)
        if self.target_reached():
            self._halt("target", f"Earned ${self.ladder.pnl:.2f} since Reset, the Until earn target. Press Reset "
                                 f"to start counting again, or raise Until earn.")
            return
        if won and self.stop_after_win:
            self.stop_after_win = False
            self._halt("after_win", "Stopped after a win, as asked (Stop after a win).")
            return
        if not won and self.cfg.max_steps and self.ladder.step > self.cfg.max_steps:
            self._ladder_end(f"{self.cfg.max_steps} losses in a row (Max losses in a row).")

    # --- entering ----------------------------------------------------------------------------------------
    def _enter(self) -> None:
        cfg, now = self.cfg, self.clock()
        cur, nxt = self.pm.current_round(cfg.coin, cfg.timeframe)
        later = f"next round starts in {fmt_secs(nxt.start - now)}" if nxt else "waiting for the next round"
        self.live = {"next_start": nxt.start if nxt else None}
        coins = getattr(self.pm, "coin_prices", None)
        if coins is not None and getattr(coins, "available", False):
            coins.watch(cfg.coin)      # the coin's prices from before each round starts: its TWAP Price To Beat
        feed = getattr(self.pm, "volumes", None)
        if feed:                       # count both rounds' trades from before they start, for the page
            feed.watch(cur)
            feed.watch(nxt)
        if not cur or cur.slug == self.done_round:
            return self._wait(f"Bet placed for this round; {later}" if cur else f"No round running; {later}",
                              key=f"done:{cur.slug}" if cur else "none")
        elapsed = now - cur.start
        opens, window = entry_span(cfg, cur.end - cur.start)
        if elapsed < opens:
            return self._wait(f"Waiting until {fmt_secs(cur.end - cur.start - opens)} are left in {cur.title} "
                              f"(in {fmt_secs(opens - elapsed)})", key=f"early:{cur.slug}")
        if elapsed > window:
            return self._wait(f"Too late for the current round ({fmt_secs(elapsed)} in, entry window "
                              f"{fmt_secs(window)}); {later}", key=f"late:{cur.slug}")
        if not cur.accepting:
            return self._wait(f"{cur.title} isn't taking orders; {later}", key=f"closed:{cur.slug}")
        last, cap = None, buy_cap(cfg)
        prices = self.pm.best_asks(cur)
        if cfg.conditions_mode:        # the owner's own AND/OR conditions
            return self._enter_conditions(cur, prices, cap, now, window - elapsed)
        if cfg.side_mode in ("follow_last", "opposite_last"):
            found = self.pm.last_result(cfg.coin, cfg.timeframe)
            last = found[1] if found else None
        side = choose_side(cfg, self.ladder, last, prices, self.rng)
        price = prices.get(side)
        if price is None or price > cfg.max_price + 1e-9:
            return self._wait(f"{side.upper()} costs {price if price is not None else 'n/a'}, above the max price "
                              f"{cfg.max_price:.2f}; waiting (window ends in {fmt_secs(window - elapsed)})",
                              key=f"price:{cur.slug}:{side}")
        if price < cfg.min_price - 1e-9:
            return self._wait(f"{side.upper()} costs {price}, below the min price {cfg.min_price:.2f}; waiting "
                              f"(window ends in {fmt_secs(window - elapsed)})", key=f"cheap:{cur.slug}:{side}")
        return self._buy(cur, side, price, cap, last, now, window - elapsed)

    def _enter_conditions(self, cur, prices: dict, cap: float, now: float, window_left: float) -> None:
        """Bet the side the conditions allow (strategy.sides_allowed); the leader when both are allowed."""
        cfg, conds = self.cfg, self.cfg.conditions()
        move = beat = None
        if "move" in conds:
            feed = getattr(self.pm, "coin_prices", None)
            if feed is None or not feed.available:
                return self._wait("The Move condition needs the live price feed (the websockets package); it isn't "
                                  "available.", key="nofeed", level=logging.WARNING)
            feed.watch(cfg.coin)
            price_now, beat, exact = feed.settle_prices(cfg.coin, cur, cfg.timeframe)   # 60 s TWAPs, like settling
            self.live.update(coin_price=price_now, price_to_beat=beat, twap=exact)
            if price_now is not None and beat is not None:
                move = price_now - beat
        sides, why = sides_allowed(cfg, cur.end - now, move, prices)
        if not sides:
            return self._wait(f"Waiting: {why} (checking until 5 s before the end, in {fmt_secs(window_left)})",
                              key=f"cond:{cur.slug}")
        side = leader_of(sides, prices)
        price = prices.get(side)
        if price is None or price > cap + 1e-9:
            return self._wait(f"The conditions are met for {side.upper()}, but it costs "
                              f"{price if price is not None else 'n/a'}, above Price to {cap:.2f} (the most it "
                              f"pays); waiting",
                              key=f"condcap:{cur.slug}")
        self.log.info("Conditions met (%s): betting %s at %.2f%s", ", ".join(f"{c} {op.upper()}" for c, op in conds.items()),
                      side.upper(), price, f", {cfg.coin} ${move:+,.2f} from the Price To Beat" if move is not None else "")
        self._buy(cur, side, price, cap, None, now, window_left)
        if beat is not None and self.bet and self.bet["slug"] == cur.slug:
            self.bet["price_to_beat"] = beat          # shown with the live price while the bet runs
            self.save()

    def _buy(self, cur, side: str, price: float, cap: float, last, now: float, window_left: float) -> None:
        """Size the bet for this step on the order book (paying at most `cap` per share) and place it."""
        cfg = self.cfg
        sizing = self.sizing()
        if sizing is None:
            if self.waits_at_step1():
                return self._wait(f"${self.budget_left():.2f} left can't pay for {cfg.survive_steps} bets in a row "
                                  f"(Steps to survive), even at 5 shares each; waiting (When the ladder ends = "
                                  f"reset). Lower Steps to survive or add money.", key=f"short:{cur.slug}",
                                  level=logging.WARNING)
            return self._halt("budget", f"${self.budget_left():.2f} left can't pay for {cfg.survive_steps} bets in "
                                        f"a row (Steps to survive), even at 5 shares each: lower Steps to survive "
                                        f"or add money, then Start again.")
        # sized on the order book, not the best ask alone: a big bet climbs the asks, and a win sized at the best
        # price would then win back less than the streak lost
        asks = self.pm.book(cur.token(side))["asks"]
        shares = shares_for_book(sizing, self.ladder.step, self.ladder.streak_lost, asks, cap,
                                 cur.fee_rate, cur.fee_exponent, self.ladder.wins_left)
        if shares is None:
            return self._wait(f"Not enough {side.upper()} shares for sale up to {cap:.2f} for a win to "
                              f"pay back the streak; retrying (window ends in {fmt_secs(window_left)})",
                              key=f"thin:{cur.slug}")
        shares = max(shares, cur.min_size)
        plan = walk_asks(asks, shares, cap, cur.fee_rate, cur.fee_exponent)
        est = plan.cost if plan else cost_of(shares, price, cur.fee_rate, cur.fee_exponent)
        if est > self.budget_left() + 1e-9:
            return self._ladder_end(f"the step {self.ladder.step} bet costs ${est:.2f} but only "
                                    f"${self.budget_left():.2f} of the budget is left.", key=f"short:{cur.slug}")
        fill = self.client.buy(cur, side, shares, cap)
        if fill is None:
            return self._wait(f"Not enough {side.upper()} shares for sale up to {cap:.2f}; retrying",
                              key=f"thin:{cur.slug}")
        self.bet = {"slug": cur.slug, "title": cur.title, "side": side, "token": cur.token(side), "step": self.ladder.step,
                    "shares": fill.shares, "price": fill.avg_price, "cost": fill.cost, "fee": fill.fee,
                    "start": cur.start, "end": cur.end, "placed": now, "url": cur.url, "best_price": price,
                    "last_result": last, "base_profit": sizing.base_profit_usd}
        feed = getattr(self.pm, "coin_prices", None)
        if feed is not None and getattr(feed, "available", False):   # for History's moves (after the order: no delay)
            try:
                feed.watch(cfg.coin)
                coin_now, beat, _ = feed.settle_prices(cfg.coin, cur, cfg.timeframe)
                self.bet.update(coin_at_bet=coin_now, price_to_beat=beat)
            except Exception as e:  # noqa: BLE001 - only for the page: never stops a placed bet being saved
                self.log.debug("coin price at bet: %s", e)
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
