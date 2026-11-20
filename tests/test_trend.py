import random
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from poly_bot.clock import JST
from poly_bot.config import BotConfig
from poly_bot.simulate import RealFeed
from poly_bot.strategy import Ladder, choose_side
from poly_bot.trend import Results, prefix_ups, share, window_start


def event(start, result):
    won = {"up": '["1", "0"]', "down": '["0", "1"]'}[result]
    return {"slug": f"btc-updown-5m-{start}", "markets": [{
        "outcomes": '["Up", "Down"]', "clobTokenIds": '["1", "2"]', "closed": True, "outcomePrices": won,
        "eventStartTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start)),
        "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start + 300))}]}


class TrendSide(unittest.TestCase):
    def side(self, mode, up_share, seed=1, first="up"):
        return choose_side(BotConfig(side_mode=mode, first_side=first), Ladder(), None, {}, random.Random(seed),
                           up_share)

    def test_follow_takes_the_leading_side(self):
        self.assertEqual(self.side("trend_follow", 0.47), "down")
        self.assertEqual(self.side("trend_follow", 0.53), "up")
        self.assertEqual(self.side("trend_follow", None, first="down"), "down")   # not known yet: First side
        self.assertEqual(self.side("trend_follow", 0.5, first="down"), "down")

    def test_random_is_weighted_by_the_share(self):
        rng = random.Random(7)
        cfg = BotConfig(side_mode="trend_random")
        ups = sum(choose_side(cfg, Ladder(), None, {}, rng, 0.3) == "up" for _ in range(10000))
        self.assertAlmostEqual(ups / 10000, 0.3, delta=0.02)
        ups = sum(choose_side(cfg, Ladder(), None, {}, rng, None) == "up" for _ in range(10000))
        self.assertAlmostEqual(ups / 10000, 0.5, delta=0.02)                       # not known yet: fair coin

    def test_window_and_share(self):
        now = datetime(2026, 11, 20, 19, 0, tzinfo=JST).timestamp()
        self.assertEqual(window_start("month", now), datetime(2026, 11, 1, tzinfo=JST).timestamp())
        self.assertEqual(window_start("1d", now), now - 86400)
        with self.assertRaises(ValueError):
            window_start("2y", now)
        starts = list(range(0, 3000, 100))                                  # 30 rounds: 10 up, then 20 down
        ups = prefix_ups(["u"] * 10 + ["d"] * 20)
        self.assertEqual(share(starts, ups, 0, 3000), (10 / 30, 30))
        self.assertEqual(share(starts, ups, 1000, 3000), (0.0, 20))
        self.assertEqual(share(starts, ups, 2000, 3000), (None, 10))        # under 20 rounds: not known

    def test_config_checks_the_window(self):
        BotConfig(side_mode="trend_follow", trend_window="7d").validate()
        with self.assertRaises(ValueError):
            BotConfig(trend_window="2y").validate()


class ResultsStore(unittest.TestCase):
    def test_fetches_pages_back_and_saves(self):
        now = 1_800_000_000
        rounds = [(now - 300 * (i + 1), "up" if i % 4 == 0 else "down") for i in range(120)]   # newest first
        asked = []

        def fetch(url, params):
            asked.append(params)
            before = time.mktime(time.strptime(params["end_date_max"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            page = [r for r in rounds if r[0] + 300 <= before][:params["limit"]]
            return [event(s, res) for s, res in page]

        with tempfile.TemporaryDirectory() as d:
            store = Results(lambda c, tf: "sid", Path(d), clock=lambda: now, fetch=fetch)
            self.assertEqual(store.up_share("BTC", "5m", "1d"), (None, 0))     # first look: the fetch starts
            for t in __import__("threading").enumerate():
                if t.name.startswith("trend-"):
                    t.join(5)
            up, n = store.up_share("BTC", "5m", "1d")
            self.assertEqual(n, 120)
            self.assertAlmostEqual(up, 0.25)
            self.assertGreaterEqual(len(asked), 3)                           # 50 per page
            again = Results(lambda c, tf: "sid", Path(d), clock=lambda: now, fetch=lambda *a: [])
            self.assertEqual(again.up_share("BTC", "5m", "1d")[1], 120)       # kept on disk


class TrendSimulation(unittest.TestCase):
    def test_follow_bets_the_side_that_led_before_each_round(self):
        rounds = []
        for i in range(200):                                                # Up 3 in 4 rounds
            res = "down" if i % 4 == 3 else "up"
            rounds.append({"slug": f"r{i}", "start": 1_800_000_000 + 300 * i, "end": 1_800_000_300 + 300 * i,
                           "result": res, "h": [[-60, 0.5], [10, 0.5], [200, 0.5]]})
        cfg = BotConfig(side_mode="trend_follow", trend_window="1d", first_side="down", min_price=0.01,
                        max_price=0.99)
        feed = RealFeed(cfg, rounds, random.Random(3))
        lad = Ladder()
        sides = [feed.next(lad)[2] for _ in range(60)]
        late = [s for s in sides if s["round"] >= rounds[30]["start"]]
        self.assertTrue(late and all(s["side"] == "up" for s in late))


if __name__ == "__main__":
    unittest.main()
