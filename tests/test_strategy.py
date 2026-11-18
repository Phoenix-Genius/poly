import random
import unittest

from poly_bot.config import BotConfig
from poly_bot.strategy import (Ladder, base_for_steps, build_ladder, choose_side, cost_of, entry_window,
                               fee_per_share, shares_for, steps_that_fit, streak_sizing, walk_asks, with_base)


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


class StepsToSurviveTest(unittest.TestCase):
    def check(self, sizing):
        cfg = BotConfig(".", sizing=sizing, target_steps=6, max_price=0.6)
        base = base_for_steps(cfg, 6, 0.6, 1000)
        self.assertIsNotNone(base)
        self.assertEqual(steps_that_fit(build_ladder(with_base(cfg, base), 0.6, 1000, max_rows=6)), 6)
        self.assertLess(steps_that_fit(build_ladder(with_base(cfg, base + 0.05), 0.6, 1000, max_rows=6)), 6)
        return base

    def test_recover_sets_profit_per_win(self):
        self.assertGreater(self.check("recover"), 1)

    def test_multiply_sets_starting_bet(self):
        self.assertGreater(self.check("multiply"), 10)

    def test_budget_too_small(self):
        cfg = BotConfig(".", target_steps=10, max_price=0.6)
        self.assertIsNone(base_for_steps(cfg, 10, 0.6, 20))

    def test_streak_keeps_its_base(self):
        cfg = BotConfig(".", target_steps=5, max_price=0.6)
        first, base = streak_sizing(cfg, 1, 0.0, 300)
        self.assertEqual(streak_sizing(cfg, 3, base, 50)[1], base)        # mid-streak: not recalculated
        self.assertLess(streak_sizing(cfg, 1, base, 150)[1], base)        # a new streak with less money
        self.assertIs(streak_sizing(BotConfig("."), 1, 0.0, 300)[0].__class__, BotConfig)   # off



class BookSizingTest(unittest.TestCase):
    """A big recover bet climbs a thin book: sized at the best ask alone, its win pays back less than the streak."""
    # the 2026-11-11 paper bet: step 9, the streak lost $3,843.78, best ask 0.46, filled at 0.508 on average
    ASKS = [(0.46, 2000), (0.48, 1500), (0.50, 1500), (0.52, 1500), (0.54, 2000), (0.55, 3000)]

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.cfg = BotConfig(Path(tempfile.mkdtemp()), sizing="recover", base_profit_usd=10)
        self.lost = 3843.78

    def test_best_ask_sizing_falls_short(self):
        from poly_bot.strategy import shares_for, walk_asks
        n = shares_for(self.cfg, 9, self.lost, 0.46)
        fill = walk_asks(self.ASKS, n, 0.55)
        self.assertLess(n - fill.cost - self.lost, 0)          # the old way: the win doesn't pay the streak back

    def test_book_sizing_pays_the_streak_back(self):
        from poly_bot.strategy import shares_for_book, walk_asks
        n = shares_for_book(self.cfg, 9, self.lost, self.ASKS, 0.55)
        fill = walk_asks(self.ASKS, n, 0.55)
        self.assertGreaterEqual(n - fill.cost - self.lost, 10 - 1e-6)     # streak back plus the Profit per win
        self.assertLess(walk_asks(self.ASKS, n - 0.02, 0.55).shares - walk_asks(self.ASKS, n - 0.02, 0.55).cost
                        - self.lost, 10)                                   # and not more shares than needed

    def test_thin_book_returns_none(self):
        from poly_bot.strategy import shares_for_book
        self.assertIsNone(shares_for_book(self.cfg, 9, self.lost, [(0.46, 500), (0.50, 500)], 0.55))
        self.assertIsNone(shares_for_book(self.cfg, 1, 0, [(0.60, 5000)], 0.55))    # nothing up to Max price

    def test_deep_book_same_as_before(self):
        from poly_bot.strategy import shares_for, shares_for_book
        self.assertEqual(shares_for_book(self.cfg, 3, 50, [(0.5, 1e6)], 0.55), shares_for(self.cfg, 3, 50, 0.5))


class RecoverOverWinsTest(unittest.TestCase):
    def cfg(self, **kw):
        import tempfile
        from pathlib import Path
        return BotConfig(Path(tempfile.mkdtemp()), **{"sizing": "recover", "base_profit_usd": 10, **kw})

    def test_record_carries_the_debt(self):
        lad = Ladder()
        lad.record("up", False, -10, 2)
        lad.record("down", False, -20, 2)
        self.assertEqual((lad.step, lad.streak_lost, lad.wins_left), (3, 30, 0))
        lad.record("up", True, 25, 2)                       # half of the 30 back (+ profit)
        self.assertEqual((lad.step, lad.streak_lost, lad.wins_left), (1, 15, 1))
        lad.record("down", False, -12, 2)                   # a loss: the 27 is spread over 2 wins again
        self.assertEqual((lad.step, lad.streak_lost, lad.wins_left), (2, 27, 0))
        lad.record("up", True, 23.5, 2)
        lad.record("down", True, 23.5, 2)
        self.assertEqual((lad.step, lad.streak_lost, lad.wins_left, lad.base), (1, 0, 0, 0))

    def test_last_win_forgives_a_small_leftover(self):
        lad = Ladder()
        lad.record("up", False, -10, 2, base_profit=2.5)
        lad.record("up", False, -20, 2, base_profit=2.5)                   # 30 owed, over 2 wins
        lad.record("up", True, 17.5, 2, base_profit=2.5)                   # pays back 15
        self.assertEqual((lad.streak_lost, lad.wins_left, lad.forgiven), (15, 1, 0))
        lad.record("up", True, 17.18, 2, base_profit=2.5)                  # a dearer fill: 14.68 back, 0.32 left
        self.assertEqual((lad.step, lad.streak_lost, lad.wins_left), (1, 0, 0))   # the 2nd win ends the cycle
        self.assertAlmostEqual(lad.forgiven, 0.32)
        lad.record("up", False, -10, 2, base_profit=2.5)
        lad.record("up", True, 7.5, 2, base_profit=2.5)                    # first of 2: a leftover is carried
        lad.record("up", True, 4.0, 2, base_profit=2.5)                    # 2nd, 3.5 short: still the end
        self.assertEqual((lad.streak_lost, lad.wins_left), (0, 0))         # (any leftover is forgiven)
        self.assertAlmostEqual(lad.forgiven, 3.5)
        lad.record("up", False, -10, 2, base_profit=2.5)
        lad.record("up", True, 4.0, 2, base_profit=2.5)                    # the 1st of 2 still carries its leftover
        self.assertAlmostEqual(lad.streak_lost, 8.5)
        self.assertEqual((lad.wins_left, lad.forgiven), (1, 0))

    def test_one_win_is_the_old_behaviour(self):
        lad = Ladder()
        lad.record("up", False, -10)
        lad.record("up", True, 12)
        self.assertEqual((lad.step, lad.streak_lost), (1, 0))

    def test_ladder_grows_slower(self):
        one = build_ladder(self.cfg(), 0.5, 1e9, max_rows=10)
        two = build_ladder(self.cfg(recover_wins=2), 0.5, 1e9, max_rows=10)
        three = build_ladder(self.cfg(recover_wins=3), 0.5, 1e9, max_rows=10)
        total = lambda rows: sum(r.cost for r in rows)
        self.assertGreater(total(one), 9 * total(two))       # ~$14.6k vs ~$1.4k for 10 losses
        self.assertGreater(total(two), 2 * total(three))
        for r in two:                                        # a win still earns the Profit per win
            self.assertAlmostEqual(r.win_profit, 10, delta=0.05)
        self.assertAlmostEqual(two[3].left_after_win, two[2].lost_total / 2, delta=0.01)
        self.assertEqual(one[3].left_after_win, 0)

    def test_steps_to_survive_fits_a_bigger_bet(self):
        self.assertGreater(base_for_steps(self.cfg(recover_wins=2), 8, 0.55, 5000),
                           3 * base_for_steps(self.cfg(), 8, 0.55, 5000))

    def test_book_sizing_uses_the_part(self):
        from poly_bot.strategy import shares_for, shares_for_book
        c = self.cfg(recover_wins=2)
        self.assertEqual(shares_for_book(c, 3, 100, [(0.5, 1e6)], 0.55), shares_for(c, 3, 100, 0.5))
        self.assertEqual(shares_for(c, 1, 100, 0.5, wins_left=1), shares_for(self.cfg(), 1, 100, 0.5))


class SimulateTest(unittest.TestCase):
    def test_fair_market_loses_about_the_fee(self):
        from poly_bot.simulate import simulate
        cfg = BotConfig(".", base_profit_usd=1, max_steps=3, on_ladder_end="reset")
        r = simulate(cfg, 0.5, 1000, bets=500, runs=400, seed=7)
        self.assertEqual(r["runs"], 400)
        self.assertAlmostEqual(r["break_even"], 0.5 + 0.07 * 0.25)
        self.assertLess(r["average"], 0)                     # fair coin + fees: loses on average
        self.assertLessEqual(max(t["step"] for t in r["log"] if not t.get("event")), 3)   # Max losses in a row
        self.assertGreater(r["ladder_ends_avg"], 0)
        self.assertEqual(sum(b["count"] for b in r["histogram"]), 400)
        self.assertTrue(all(not t.get("event") or t["event"] in ("ladder_end", "broke") for t in r["log"]))

    def test_sure_win_and_sure_loss(self):
        from poly_bot.simulate import simulate
        cfg = BotConfig(".", base_profit_usd=1)
        win = simulate(cfg, 0.5, 100, bets=50, runs=3, win_chance=1.0, seed=1)
        self.assertEqual(win["profit_chance"], 1.0)
        self.assertGreaterEqual(win["median"], 50 * 1 - 0.5)  # every bet a win of at least the Profit per win
        lose = simulate(cfg, 0.5, 100, bets=50, runs=3, win_chance=0.0, seed=1)
        self.assertEqual(lose["broke_chance"], 1.0)          # losing every bet uses up the budget
        self.assertGreater(lose["p5"], -100.01)

    def test_limits(self):
        from poly_bot.simulate import simulate
        cfg = BotConfig(".")
        with self.assertRaises(ValueError):
            simulate(cfg, 0.5, 100, bets=10_000, runs=10_000)
        with self.assertRaises(ValueError):
            simulate(cfg, 0.5, 0)


class PercentSizingTest(unittest.TestCase):
    """Bet sizing "percent": each bet costs Bet % of the budget left; 100% = all-in (owner's request 2026-11-18)."""

    def test_each_bet_is_the_share_of_what_is_left(self):
        from poly_bot.strategy import streak_sizing
        cfg = BotConfig(sizing="percent", bet_pct=50)
        s, base = streak_sizing(cfg, 1, 0.0, 200)
        self.assertEqual(base, 100.0)
        costs = [r.cost for r in build_ladder(s, 0.5, 200)[:3]]
        self.assertEqual(costs, [100.0, 50.0, 25.0])              # each loss leaves half, so the next bet halves
        self.assertLessEqual(cost_of(shares_for(s, 1, 0.0, 0.5), 0.5), 100.0)

    def test_all_in_bets_everything_and_one_loss_ends_the_ladder(self):
        from poly_bot.strategy import streak_sizing
        cfg = BotConfig(sizing="percent", bet_pct=100)
        self.assertEqual(cfg.survive_steps, 0)                     # Steps to survive doesn't apply
        s, _ = streak_sizing(cfg, 1, 0.0, 200)
        rows = build_ladder(s, 0.5, 200)
        self.assertEqual([r.fits for r in rows], [True, False])
        self.assertGreater(rows[0].cost, 199)

    def test_on_the_book_never_spends_more_than_the_bet(self):
        from poly_bot.strategy import shares_for_book, streak_sizing, walk_asks
        s, _ = streak_sizing(BotConfig(sizing="percent", bet_pct=100), 1, 0.0, 100)
        asks = [(0.50, 40), (0.52, 60), (0.55, 500)]
        n = shares_for_book(s, 1, 0.0, asks, 0.55)
        self.assertLessEqual(walk_asks(asks, n, 0.55).cost, 100 + 1e-9)
        self.assertGreater(walk_asks(asks, n + 0.05, 0.55).cost, 100)  # and as many as the money buys

    def test_win_clears_the_streak(self):
        lad = Ladder()
        lad.record("up", False, -50.0)
        lad.record("up", True, 30.0)
        self.assertEqual((lad.step, lad.streak_lost), (1, 0.0))

    def test_bad_percent_is_refused(self):
        for bad in (0, 101):
            with self.assertRaises(ValueError):
                BotConfig(sizing="percent", bet_pct=bad).validate()

    def test_simulation_runs(self):
        from poly_bot.simulate import simulate
        out = simulate(BotConfig(sizing="percent", bet_pct=100), 0.5, 100, bets=50, runs=20, seed=1)
        self.assertIn("first_bet", out)
        self.assertGreater(out["first_bet"]["cost"], 99)
