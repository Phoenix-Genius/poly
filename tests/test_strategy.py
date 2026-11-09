import random
import unittest

from poly_bot.config import BotConfig
from poly_bot.strategy import (Ladder, build_ladder, choose_side, cost_of, entry_window, fee_per_share, shares_for,
                               steps_that_fit, walk_asks)


class FeeTest(unittest.TestCase):
    def test_docs_example(self):
        # Polymarket's docs: 100 shares at $0.50 pay a $1.75 taker fee on crypto markets
        self.assertAlmostEqual(100 * fee_per_share(0.5), 1.75)
        self.assertAlmostEqual(cost_of(100, 0.5), 51.75)

    def test_fee_smaller_far_from_half(self):
        self.assertLess(fee_per_share(0.9), fee_per_share(0.5))


class SizingTest(unittest.TestCase):
    def test_recover_win_pays_streak_plus_profit(self):
        cfg = BotConfig(sizing="recover", base_profit_usd=2.5)
        for p in (0.3, 0.5, 0.6):
            lost = 37.0
            shares = shares_for(cfg, 4, lost, p)
            profit = shares - cost_of(shares, p)
            self.assertGreaterEqual(profit, lost + 2.5 - 1e-9)
            self.assertLess(profit, lost + 2.5 + 0.02)      # rounded up to the cent, no more

    def test_minimum_five_shares(self):
        cfg = BotConfig(sizing="recover", base_profit_usd=0.5)
        self.assertEqual(shares_for(cfg, 1, 0, 0.5), 5.0)

    def test_multiply(self):
        cfg = BotConfig(sizing="multiply", start_usd=5, multiplier=2)
        self.assertAlmostEqual(cost_of(shares_for(cfg, 1, 0, 0.5), 0.5), 5, delta=0.01)
        self.assertAlmostEqual(cost_of(shares_for(cfg, 3, 0, 0.5), 0.5), 20, delta=0.01)

    def test_ladder_budget(self):
        cfg = BotConfig(sizing="recover", base_profit_usd=2.5)
        rows = build_ladder(cfg, 0.5, 100)
        # 2.69 + 5.56 + 11.53 + 23.90 + 49.53 = 93.21 fits in 100; step 6 does not
        self.assertEqual(steps_that_fit(rows), 5)
        self.assertFalse(rows[-1].fits)
        for r in rows:
            self.assertGreaterEqual(r.win_profit, 2.5 - 0.01)

    def test_ladder_max_steps(self):
        cfg = BotConfig(sizing="recover", base_profit_usd=2.5, max_steps=3)
        self.assertEqual(len(build_ladder(cfg, 0.5, 1e9)), 3)

    def test_entry_window(self):
        self.assertEqual(entry_window("5m", 0), 100)
        self.assertEqual(entry_window("1h", 0), 1200)
        self.assertEqual(entry_window("5m", 45), 45)


class LadderTest(unittest.TestCase):
    def test_loss_then_win(self):
        lad = Ladder()
        lad.record("up", False, -2.7)
        lad.record("down", False, -5.6)
        self.assertEqual((lad.step, round(lad.streak_lost, 2)), (3, 8.3))
        lad.record("up", True, 10.8)
        self.assertEqual((lad.step, lad.streak_lost, lad.wins, lad.losses), (1, 0.0, 1, 2))
        self.assertAlmostEqual(lad.pnl, 2.5)


class SideTest(unittest.TestCase):
    def side(self, mode, ladder=None, last=None, prices=None, first="up"):
        return choose_side(BotConfig(side_mode=mode, first_side=first), ladder or Ladder(), last,
                           prices or {"up": 0.5, "down": 0.5}, random.Random(1))

    def test_fixed(self):
        self.assertEqual(self.side("up_only"), "up")
        self.assertEqual(self.side("down_only"), "down")

    def test_alternate(self):
        self.assertEqual(self.side("alternate", Ladder(rounds_bet=0)), "up")
        self.assertEqual(self.side("alternate", Ladder(rounds_bet=1)), "down")
        self.assertEqual(self.side("alternate", Ladder(rounds_bet=1), first="down"), "up")

    def test_last_result(self):
        self.assertEqual(self.side("follow_last", last="down"), "down")
        self.assertEqual(self.side("opposite_last", last="down"), "up")
        self.assertEqual(self.side("follow_last", last=None, first="down"), "down")

    def test_cheaper(self):
        self.assertEqual(self.side("cheaper", prices={"up": 0.55, "down": 0.46}), "down")
        self.assertEqual(self.side("cheaper", prices={"up": 0.44, "down": 0.57}), "up")
        self.assertEqual(self.side("cheaper", prices={"up": None, "down": 0.6}), "down")
        self.assertEqual(self.side("cheaper", prices={"up": 0.5, "down": 0.5}, first="down"), "down")

    def test_flips(self):
        lost_up = Ladder(last_side="up", last_won=False)
        won_up = Ladder(last_side="up", last_won=True)
        self.assertEqual(self.side("flip_on_loss", lost_up), "down")
        self.assertEqual(self.side("flip_on_loss", won_up), "up")
        self.assertEqual(self.side("flip_on_win", lost_up), "up")
        self.assertEqual(self.side("flip_on_win", won_up), "down")

    def test_random(self):
        self.assertIn(self.side("random"), ("up", "down"))


class WalkTest(unittest.TestCase):
    def test_walks_cheapest_first(self):
        f = walk_asks([(0.52, 10), (0.50, 4)], 8, 0.6)
        self.assertAlmostEqual(f.avg_price, (4 * 0.50 + 4 * 0.52) / 8)
        self.assertAlmostEqual(f.cost, 4 * 0.5 + 4 * 0.52 + 4 * fee_per_share(0.5) + 4 * fee_per_share(0.52), places=6)

    def test_all_or_nothing_at_max_price(self):
        self.assertIsNone(walk_asks([(0.50, 4), (0.65, 100)], 8, 0.6))
        self.assertIsNone(walk_asks([], 5, 0.6))


if __name__ == "__main__":
    unittest.main()


class MidStreakLadderTest(unittest.TestCase):
    def test_from_step_4(self):
        cfg = BotConfig(sizing="recover", base_profit_usd=2)
        rows = build_ladder(cfg, 0.5, budget=22.03 + 11.22, step=4, lost=11.22)
        self.assertEqual(rows[0].step, 4)
        self.assertTrue(rows[0].fits)                    # about $14 at 0.50, $22 left
        self.assertAlmostEqual(rows[0].win_profit, 2, delta=0.02)
        self.assertGreater(rows[0].cost, 13)
        self.assertLess(rows[0].cost, 15)
        self.assertAlmostEqual(rows[0].lost_total, 11.22 + rows[0].cost, places=2)
