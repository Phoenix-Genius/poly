"""conditions Bet mode: AND/OR conditions on time left, the coin's move and the side's price."""
import tempfile
import unittest
from pathlib import Path

from poly_bot.config import BotConfig
from poly_bot.engine import Bot
from poly_bot.paper import PaperClient
from poly_bot.strategy import buy_cap, conditions_text, entry_span, sides_allowed, plan_price

from tests.fakes import Clock, FakePM


def cfg(**kw):
    return BotConfig(".", **{"entry_mode": "conditions", "cond_time": "off", "cond_move": "off", "cond_price": "off", **kw})


class UpgradeTest(unittest.TestCase):
    def test_old_modes_become_the_same_conditions(self):
        hit = BotConfig(".", entry_mode="hit", hit_price=0.8, hit_max_price=0.0)
        self.assertEqual((hit.entry_mode, hit.conditions(), hit.cond_price_min, hit.cond_price_max),
                         ("conditions", {"price": "and"}, 0.8, 0.0))
        time = BotConfig(".", entry_mode="time", time_left_sec=60, time_min_price=0.0, hit_max_price=0.0)
        self.assertEqual((time.conditions(), time.cond_time_sec), ({"time": "and"}, 60))
        lead = BotConfig(".", entry_mode="time", time_left_sec=120, time_min_price=0.6, hit_max_price=0.95)
        self.assertEqual((lead.conditions(), lead.cond_price_min, lead.cond_price_max),
                         ({"time": "and", "price": "and"}, 0.6, 0.95))
        move = BotConfig(".", entry_mode="move", move_usd=50, hit_max_price=0.0)
        self.assertEqual((move.conditions(), move.cond_move_usd), ({"move": "and"}, 50))
        self.assertEqual(BotConfig(".", entry_mode="mixed", cond_time="and").entry_mode, "conditions")
        for c in (hit, time, lead, move):
            c.validate()

    def test_old_config_file_is_upgraded_on_load(self):
        import json
        from poly_bot.config import load_config, save_settings
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text(json.dumps({"entry_mode": "hit", "hit_price": 0.75, "hit_max_price": 0.9}))
        c = load_config(d / "config.json")
        self.assertEqual((c.entry_mode, c.cond_price_min, c.cond_price_max), ("conditions", 0.75, 0.9))
        save_settings(d / "config.json", {"bot_name": "x"})
        saved = json.loads((d / "config.json").read_text())
        self.assertEqual((saved["entry_mode"], saved["cond_price_min"]), ("conditions", 0.75))
        self.assertNotIn("hit_price", saved)


class SidesAllowedTest(unittest.TestCase):
    def test_all_and_the_owners_example(self):
        c = cfg(cond_time="and", cond_time_sec=90, cond_move="and", cond_move_usd=10,
                cond_price="and", cond_price_min=0.75, cond_price_max=0.95)
        p = {"up": 0.85, "down": 0.16}
        self.assertEqual(sides_allowed(c, 89, 12.5, p)[0], ["up"])
        sides, why = sides_allowed(c, 120, 12.5, p)                 # too early
        self.assertEqual(sides, [])
        self.assertIn("120 s left", why)
        self.assertEqual(sides_allowed(c, 60, 8.0, p)[0], [])       # moved only $8
        self.assertEqual(sides_allowed(c, 60, -15.0, p)[0], [])     # moved down, but only Up is in the price range
        self.assertEqual(sides_allowed(c, 60, 12.5, {"up": 0.97, "down": 0.04})[0], [])   # Up above Price to
        self.assertEqual(sides_allowed(c, 60, None, p)[0], [])      # no live price yet

    def test_or_is_enough_with_one(self):
        c = cfg(cond_move="or", cond_move_usd=50, cond_price="or", cond_price_min=0.85, cond_price_max=0.99)
        self.assertEqual(sides_allowed(c, 200, 60.0, {"up": 0.7, "down": 0.31})[0], ["up"])      # move only
        self.assertEqual(sides_allowed(c, 200, 5.0, {"up": 0.12, "down": 0.9})[0], ["down"])     # price only
        self.assertEqual(sides_allowed(c, 200, 60.0, {"up": 0.1, "down": 0.9})[0], ["down", "up"])  # each one side
        sides, why = sides_allowed(c, 200, 5.0, {"up": 0.5, "down": 0.51})
        self.assertEqual(sides, [])
        self.assertIn(" or ", why)

    def test_and_with_ors(self):
        c = cfg(cond_time="and", cond_time_sec=90, cond_move="or", cond_move_usd=30,
                cond_price="or", cond_price_min=0.9, cond_price_max=0.99)
        self.assertEqual(sides_allowed(c, 100, 40.0, {"up": 0.6, "down": 0.41})[0], [])          # time not yet
        self.assertEqual(sides_allowed(c, 80, 40.0, {"up": 0.6, "down": 0.41})[0], ["up"])
        self.assertEqual(sides_allowed(c, 80, 1.0, {"up": 0.05, "down": 0.95})[0], ["down"])
        self.assertEqual(conditions_text(c), "time <= 90s AND (move >= $30 OR price 0.90-0.99)")

    def test_time_alone_allows_both(self):
        c = cfg(cond_time="and", cond_time_sec=60)
        self.assertEqual(sides_allowed(c, 50, None, {"up": 0.6, "down": 0.41})[0], ["down", "up"])

    def test_window_cap_and_plan_price(self):
        c = cfg(cond_time="and", cond_time_sec=90, cond_price="and", cond_price_max=0.95, hit_max_price=0.9)
        self.assertEqual(entry_span(c, 300), (210, 295))
        self.assertEqual(entry_span(cfg(cond_time="or", cond_price="or"), 300), (0.0, 295))
        self.assertEqual(buy_cap(c), 0.95)                       # Price to is the limit; Pay at most isn't used
        self.assertEqual(plan_price(c), 0.95)
        self.assertEqual(buy_cap(cfg(cond_move="and", cond_price="or", cond_price_max=0.9)), 0.9)   # also for OR
        self.assertEqual(buy_cap(cfg(cond_move="and")), 0.99)    # no Price condition: no limit
        self.assertEqual(buy_cap(cfg(cond_price="and", cond_price_min=0.8)), 0.95)   # Price to empty = 0.95

    def test_empty_price_from_is_050_and_to_095(self):
        top = cfg(cond_price="and", cond_price_min=0.8)
        self.assertEqual(sides_allowed(top, 100, None, {"up": 0.94, "down": 0.07})[0], ["up"])
        self.assertEqual(sides_allowed(top, 100, None, {"up": 0.98, "down": 0.03})[0], [])
        self.assertEqual(conditions_text(top), "price 0.80-0.95")
        fav = cfg(cond_price="and", cond_price_max=0.95)
        self.assertEqual(sides_allowed(fav, 60, None, {"up": 0.75, "down": 0.26})[0], ["up"])
        sides, why = sides_allowed(fav, 60, None, {"up": 0.97, "down": 0.04})   # never the 0.04 long shot
        self.assertEqual(sides, [])
        self.assertIn("UP costs 0.97, above Price to 0.95", why)
        self.assertEqual(conditions_text(fav), "price 0.50-0.95")
        self.assertEqual(sides_allowed(cfg(cond_price="and"), 60, None, {"up": 0.4, "down": 0.61})[0], ["down"])
        self.assertEqual(conditions_text(cfg(cond_price="and")), "price 0.50-0.95")
        cfg(cond_price="and").validate()

    def test_validation(self):
        with self.assertRaises(ValueError):
            cfg().validate()                                      # no condition on
        with self.assertRaises(ValueError):
            cfg(cond_price="and", cond_price_min=0.9, cond_price_max=0.8).validate()
        with self.assertRaises(ValueError):
            cfg(cond_price="and", cond_price_max=1.5).validate()
        cfg(cond_time="and").validate()


class ConditionsEngineTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.clock = Clock(1_800_000_000)
        self.pm = FakePM(self.clock)

    def feed(self, now, beat):
        class Feed:
            available = True

            def watch(self, coin):
                pass

            def price(self, coin):
                return self.now

            def price_to_beat(self, coin, r, tf):
                return self.beat

            def settle_prices(self, coin, r, tf):     # (now, Price To Beat, exact): the TWAPs in the real feed
                return self.now, self.beat, True

            def twap(self, coin, at=None):
                return None

            def close_price(self, coin, start, end, tf):
                return self.end
        f = Feed()
        f.end = None
        f.now, f.beat = now, beat
        self.pm.coin_prices = f
        return f

    def make(self, **kw):
        c = BotConfig(self.dir, **{"entry_mode": "conditions", "base_profit_usd": 2.5, "cond_time": "and", "cond_time_sec": 90,
                                   "cond_move": "and", "cond_move_usd": 10, "cond_price": "and",
                                   "cond_price_min": 0.75, "cond_price_max": 0.95, **kw})
        return Bot(c, PaperClient(c.paper_file, 1000, self.pm), self.pm, clock=self.clock)

    def test_waits_for_every_and_then_bets(self):
        f = self.feed(64712.0, 64700.0)                 # $12 up
        self.pm.prices.update(up=0.8, down=0.21)
        bot = self.make()
        self.clock.t += 100                             # 200 s left
        bot.tick()
        self.assertIsNone(bot.bet)
        self.assertIn("1m 30s are left", bot.message)
        self.clock.t += 120                             # 80 s left
        f.now = 64705.0                                 # only $5 up now
        bot.tick()
        self.assertIsNone(bot.bet)
        self.assertIn("needs $10.00", bot.message)
        f.now = 64715.0
        bot.tick()
        self.assertEqual(bot.bet["side"], "up")
        self.assertAlmostEqual(bot.bet["price_to_beat"], 64700.0)

    def test_history_gets_the_coins_moves(self):
        from poly_bot.trades import read_trades
        f = self.feed(64715.0, 64700.0)
        self.pm.prices.update(up=0.8, down=0.21)
        bot = self.make()
        self.clock.t += 220
        bot.tick()
        self.assertEqual(bot.bet["coin_at_bet"], 64715.0)
        f.end = 64690.5
        self.clock.t = bot.bet["end"] + 1
        bot.tick()
        self.pm.results[bot.bet["slug"]] = "down"
        bot._next_settle_check = 0
        bot.tick()
        row = read_trades(bot.cfg.trade_log)[-1]
        self.assertEqual((float(row["beat"]), float(row["coin_at_bet"]), float(row["coin_end"])), (64700.0, 64715.0, 64690.5))

    def test_the_leader_when_both_pass(self):
        self.pm.prices.update(up=0.41, down=0.6)
        bot = self.make(cond_move="off", cond_price="off", side_mode="up_only")   # Side mode isn't used
        self.clock.t += 220
        bot.tick()
        self.assertEqual(bot.bet["side"], "down")

    def test_price_to_is_the_limit_for_an_or_move(self):
        self.feed(64750.0, 64700.0)                     # $50 up: Move (OR) passes for Up
        self.pm.prices.update(up=0.97, down=0.04)
        bot = self.make(cond_time="off", cond_move="or", cond_price="or", cond_price_max=0.95, hit_max_price=0.99)
        self.clock.t += 220
        bot.tick()
        self.assertIsNone(bot.bet)
        self.assertIn("above Price to 0.95", bot.message)
        self.pm.prices.update(up=0.94, down=0.07)
        bot.tick()
        self.assertEqual(bot.bet["side"], "up")


class ConditionsSimTest(unittest.TestCase):
    def test_entry_on_the_coins_position_then(self):
        from poly_bot.simulate import entry_for
        c = cfg(cond_time="and", cond_time_sec=90, cond_move="and", cond_move_usd=10)
        path = [0.0] * 300
        for i in range(150, 300):
            path[i] = 25.0                               # $25 up from 150 s in
        for i in range(200, 240):
            path[i] = 5.0                                # back to $5 for a while
        rnd = {"start": 0, "end": 300, "result": "up", "h": [[0, 0.5], [240, 0.8], [300, 0.95]],
               "coin": {"o": 64700.0, "k": [], "c": path, "s": 1}}
        t, side, price = entry_for(c, rnd)
        self.assertEqual((t, side), (240, "up"))         # 210 s is in the window but only $5 up until 240 s
        self.assertAlmostEqual(price, 0.81)              # Up at the next recorded point + 1 cent
        self.assertIsNone(entry_for(c, {**rnd, "coin": None}))

    def test_twap_path(self):
        from poly_bot.history import twap_path
        candles = [(t, 0, 0, 0, 100.0 + (10.0 if t >= 30 else 0.0)) for t in range(-70, 300)]   # +10 at 30 s
        a = twap_path(candles, 0, 300, 1)
        self.assertEqual(a[0], 0)
        self.assertEqual(a[32], 0)                       # the TWAP ignores the last 3 s
        self.assertAlmostEqual(a[62], 10 * 30 / 60)      # half-way through the jump's minute: +5
        self.assertAlmostEqual(a[100], 10)

    def test_default_gap(self):
        from poly_bot.simulate import DEFAULT_GAP, default_gap
        self.assertEqual(default_gap(cfg(cond_move="and", cond_price="and")), DEFAULT_GAP["hit"])
        self.assertEqual(default_gap(cfg(cond_move="and", cond_price="or")), DEFAULT_GAP["move"])
        self.assertEqual(default_gap(cfg(cond_time="and")), DEFAULT_GAP["time"])

    def test_price_only_needs_no_coin(self):
        from poly_bot.simulate import entry_for, needs_coin
        c = cfg(cond_price="and", cond_price_min=0.75, cond_price_max=0.95)
        self.assertFalse(needs_coin(c))
        rnd = {"start": 0, "end": 300, "result": "down", "h": [[0, 0.5], [60, 0.4], [120, 0.2], [300, 0.1]]}
        t, side, price = entry_for(c, rnd)
        self.assertEqual(side, "down")
        self.assertTrue(0.75 <= price <= 0.81)


if __name__ == "__main__":
    unittest.main()
