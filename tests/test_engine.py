import json
import tempfile
import threading
import unittest
from pathlib import Path

from poly_bot.config import BotConfig
from poly_bot.engine import Bot
from poly_bot.paper import PaperClient
from poly_bot.trades import read_trades
from tests.fakes import Clock, FakePM


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = Clock(1_800_000_000.0)          # a round boundary (divisible by 300)
        self.pm = FakePM(self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **settings) -> Bot:
        cfg = BotConfig(self.dir, **{"side_mode": "up_only", "base_profit_usd": 2.5, **settings})
        client = PaperClient(cfg.paper_file, 1000, self.pm)
        return Bot(cfg, client, self.pm, clock=self.clock)

    def play(self, bot: Bot, result: str) -> None:
        """Bet in the round now running, let it end with `result`, settle, and move to the next round's start."""
        self.clock.t += 5
        bot.tick()
        self.assertIsNotNone(bot.bet, bot.message)
        slug = bot.bet["slug"]
        self.clock.t = bot.bet["end"] + 1
        bot.tick()                                   # ended, no result yet
        self.assertEqual(bot.phase, "settling")
        self.pm.results[slug] = result
        bot._next_settle_check = 0
        bot.tick()
        self.assertIsNone(bot.bet)
        self.clock.t = bot_round_end(slug)           # the next round's start

    def test_loss_loss_win_recovers(self):
        bot = self.make()
        self.play(bot, "down")
        self.play(bot, "down")
        self.assertEqual(bot.ladder.step, 3)
        lost = bot.ladder.streak_lost
        self.play(bot, "up")
        self.assertEqual(bot.ladder.step, 1)
        self.assertGreaterEqual(bot.ladder.pnl, 2.5 - 0.05)    # one win paid back both losses plus the profit
        self.assertLess(bot.ladder.pnl, 2.5 + 0.1)
        self.assertAlmostEqual(bot.client.balance(), 1000 + bot.ladder.pnl, places=4)
        rows = read_trades(bot.cfg.trade_log)
        self.assertEqual([r["result"] for r in rows], ["down", "down", "up"])
        self.assertEqual([r["step"] for r in rows], ["1", "2", "3"])
        self.assertGreater(lost, 8)

    def test_one_bet_per_round(self):
        bot = self.make()
        self.clock.t += 5
        bot.tick()
        slug = bot.bet["slug"]
        self.pm.results[slug] = "up"
        self.clock.t = bot.bet["end"] + 1
        bot.tick()
        self.assertIsNone(bot.bet)
        bot.tick()                         # same round slug is done; the clock is in the next round (fresh)
        self.assertIsNotNone(bot.bet)
        self.assertNotEqual(bot.bet["slug"], slug)

    def test_too_late_waits_for_next_round(self):
        bot = self.make(entry_window_sec=60)
        self.clock.t += 120
        bot.tick()
        self.assertIsNone(bot.bet)
        self.assertIn("Too late", bot.message)

    def test_price_above_max_waits(self):
        self.pm.prices["up"] = 0.7
        bot = self.make(max_price=0.6)
        self.clock.t += 5
        bot.tick()
        self.assertIsNone(bot.bet)
        self.assertIn("above the max price", bot.message)
        self.pm.prices["up"] = 0.55
        bot.tick()
        self.assertIsNotNone(bot.bet)

    def test_price_below_min_waits(self):
        self.pm.prices["up"] = 0.2
        bot = self.make(min_price=0.3)
        self.clock.t += 5
        bot.tick()
        self.assertIsNone(bot.bet)
        self.assertIn("below the min price", bot.message)
        self.pm.prices["up"] = 0.45
        bot.tick()
        self.assertIsNotNone(bot.bet)

    def test_steps_to_survive(self):
        bot = self.make(target_steps=5, budget_usd=300, max_price=0.6)
        self.play(bot, "down")                          # step 1 lost
        base = bot.ladder.base
        self.assertGreater(base, 0)
        self.play(bot, "down")                          # step 2 lost: same base
        self.assertEqual(bot.ladder.base, base)
        self.play(bot, "up")                            # a win: next streak works it out again
        self.assertEqual((bot.ladder.step, bot.ladder.base), (1, 0.0))
        bets = read_trades(bot.cfg.trade_log)
        self.assertAlmostEqual(float(bets[2]["shares"]) - float(bets[2]["cost"]) - float(bets[0]["cost"])
                               - float(bets[1]["cost"]), base, delta=0.05)   # the win earned the worked-out profit

    def test_steps_to_survive_too_few_funds(self):
        bot = self.make(target_steps=12, budget_usd=20, max_price=0.6)
        self.clock.t += 5
        bot.tick()
        self.assertIsNone(bot.bet)
        self.assertEqual(bot.halted, "budget")
        self.assertIn("Steps to survive", bot.halt_message)

    def test_state_survives_restart(self):
        bot = self.make()
        self.play(bot, "down")
        self.clock.t += 5
        bot.tick()
        slug = bot.bet["slug"]
        again = self.make()                # a new process: same folder
        self.assertEqual(again.ladder.step, 2)
        self.assertEqual(again.bet["slug"], slug)
        self.pm.results[slug] = "up"
        self.clock.t = again.bet["end"] + 1
        again.tick()
        self.assertEqual(again.ladder.step, 1)
        self.assertGreater(again.ladder.pnl, 0)

    def test_budget_ends_ladder(self):
        bot = self.make(budget_usd=10)     # 2.69 + 5.56 fit, the 11.53 step 3 doesn't
        self.play(bot, "down")
        self.play(bot, "down")
        self.clock.t += 5
        bot.tick()
        self.assertEqual(bot.halted, "ladder")
        self.assertIsNone(bot.bet)
        with self.assertRaises(ValueError):
            bot.on_start()
        bot.reset()
        bot.on_start()
        self.assertEqual(bot.ladder.step, 1)

    def test_ladder_end_reset_mode(self):
        bot = self.make(max_steps=2, on_ladder_end="reset")
        self.play(bot, "down")
        self.play(bot, "down")
        self.assertEqual(bot.ladder.step, 1)
        self.assertFalse(bot.halted)
        self.assertLess(bot.ladder.pnl, 0)

    def test_until_earn_stops(self):
        bot = self.make(stop_profit_usd=4)
        bot.on_start()
        self.play(bot, "up")
        self.assertFalse(bot.halted)
        self.play(bot, "up")
        self.assertEqual(bot.halted, "target")
        bot.on_start()                     # Start again is allowed after the target
        self.assertEqual(bot.earned_since_start(), 0)

    def test_run_loop_stops(self):
        bot = self.make(poll_interval_sec=0.5)
        stop = threading.Event()
        t = threading.Thread(target=bot.run, args=(stop,))
        t.start()
        stop.set()
        t.join(5)
        self.assertFalse(t.is_alive())

    def test_follow_last(self):
        bot = self.make(side_mode="follow_last")
        self.pm.results["btc-updown-5m-1799999700"] = "down"     # the round before
        self.clock.t += 5
        bot.tick()
        self.assertEqual(bot.bet["side"], "down")

    def test_paper_balance_too_small(self):
        bot = self.make()
        bot.client.reset(1.0)
        self.clock.t += 5
        bot.tick()
        self.assertEqual(bot.halted, "ladder")

    def test_state_file_is_json(self):
        bot = self.make()
        self.play(bot, "down")
        d = json.loads(bot.cfg.state_file.read_text())
        self.assertEqual(d["ladder"]["step"], 2)


def bot_round_end(slug: str) -> float:
    return float(slug.rsplit("-", 1)[1]) + 300


if __name__ == "__main__":
    unittest.main()


class TradeLogDetailsTest(unittest.TestCase):
    def test_new_columns_and_upgrade(self):
        import csv
        from poly_bot.trades import COLUMNS, append_trade
        with tempfile.TemporaryDirectory() as d:
            clock = Clock(1_800_000_000.0)
            pm = FakePM(clock)
            pm.depth = 3                          # thin book: 3 shares at 0.50, the rest at 0.51
            cfg = BotConfig(Path(d), side_mode="up_only", base_profit_usd=2.5)
            old = cfg.trade_log                    # an older log without the new columns
            old.write_text("time,round,title,side,step,shares,price,cost,fee,result,payout,pnl,total_pnl,balance\n"
                           "2026-11-09 22:53:33,r,T,up,1,5,0.5,2.6,0.1,up,5,2.4,2.4,1002.4\n")
            bot = Bot(cfg, PaperClient(cfg.paper_file, 1000, pm), pm, clock=clock)
            clock.t += 5
            bot.tick()
            self.assertEqual(bot.bet["best_price"], 0.5)
            pm.results[bot.bet["slug"]] = "up"
            clock.t = bot.bet["end"] + 70
            bot.tick()
            with open(old, newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(list(rows[0].keys()), COLUMNS)
            self.assertEqual(rows[0]["held_sec"], "")                # old row kept, new columns empty
            new = rows[1]
            self.assertEqual(int(new["held_sec"]), 365)              # 5 s in, round end + 70 s
            self.assertGreater(float(new["slippage"]), 0)            # walked from 0.50 into 0.51
            self.assertEqual(float(new["best_price"]), 0.5)
