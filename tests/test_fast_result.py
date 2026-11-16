"""Result = fast: the bot takes a round's result from its own 60 s TWAP and the market ~30 s after the end, then checks
it against Polymarket's official result (and corrects the ladder, balance and History if they differ)."""
import tempfile
import unittest
from pathlib import Path

from poly_bot.config import BotConfig
from poly_bot.engine import Bot
from poly_bot.paper import PaperClient
from poly_bot.trades import read_events, read_trades
from poly_bot.web import with_gaps
from tests.fakes import Clock, FakePM


class TwapFeed:
    available = True

    def __init__(self, beat: float, end: float):
        self.beat, self.end, self.last = beat, end, 10**12

    def watch(self, coin):
        pass

    def price(self, coin):
        return self.end

    def price_to_beat(self, coin, r, tf):
        return self.beat

    def settle_prices(self, coin, r, tf):
        return self.end, self.beat, True

    def twap(self, coin, at=None):
        return self.end

    def twap_to_beat(self, coin, r):
        return self.beat

    def last_second(self, coin):
        return self.last


class FastResultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = Clock(1_800_000_000.0)
        self.pm = FakePM(self.clock)
        self.feed = TwapFeed(64000.0, 64020.0)       # ended $20 up
        self.pm.coin_prices = self.feed
        self.pm.up_price = 0.9995

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **settings) -> Bot:
        cfg = BotConfig(self.dir, **{"side_mode": "up_only", "base_profit_usd": 2.5, **settings})
        return Bot(cfg, PaperClient(cfg.paper_file, 1000, self.pm), self.pm, clock=self.clock)

    def bet_and_end(self, bot: Bot) -> str:
        self.clock.t += 5
        bot.tick()
        self.assertIsNotNone(bot.bet, bot.message)
        slug = bot.bet["slug"]
        self.clock.t = bot.bet["end"] + 5
        return slug

    def test_fast_is_the_default_and_settles_before_the_official_result(self):
        bot = self.make()
        self.assertEqual(bot.cfg.result_wait, "fast")
        slug = self.bet_and_end(bot)
        bot.tick()
        self.assertIsNone(bot.bet, bot.message)                # paid out with no official result yet
        self.assertEqual(bot.ladder.wins, 1)
        self.assertIn(slug, bot.pending)
        row = read_trades(bot.cfg.trade_log)[-1]
        self.assertEqual((row["result"], row["result_by"]), ("up", "fast"))
        self.pm.results[slug] = "up"                           # the official result agrees: nothing changes
        bot._next_confirm = 0
        bot.tick()
        self.assertEqual(bot.pending, {})
        self.assertEqual(bot.ladder.wins, 1)

    def test_official_waits_like_before(self):
        bot = self.make(result_wait="official")
        slug = self.bet_and_end(bot)
        bot.tick()
        self.assertIsNotNone(bot.bet)
        self.assertEqual(bot.phase, "settling")
        self.pm.results[slug] = "up"
        bot._next_settle_check = 0
        bot.tick()
        self.assertIsNone(bot.bet)
        self.assertEqual(read_trades(bot.cfg.trade_log)[-1]["result_by"], "official")
        self.assertEqual(bot.pending, {})

    def test_a_close_round_waits_for_the_official_result(self):
        self.feed.end = 64003.0                                # $3 up: under the ~$5 margin
        bot = self.make()
        self.bet_and_end(bot)
        bot.tick()
        self.assertIsNotNone(bot.bet)
        self.assertIn("too close", bot.message)

    def test_waits_until_the_market_agrees(self):
        self.pm.up_price = 0.9
        bot = self.make()
        self.bet_and_end(bot)
        bot.tick()
        self.assertIsNotNone(bot.bet)
        self.assertIn("market to agree", bot.message)
        self.pm.up_price = 0.98
        self.clock.t += 3
        bot.tick()
        self.assertIsNone(bot.bet)

    def test_waits_while_the_feed_misses_the_end(self):
        self.feed.last = 1_800_000_000 + 300 - 10              # newest price 10 s before the end
        bot = self.make()
        self.bet_and_end(bot)
        bot.tick()
        self.assertIsNotNone(bot.bet)

    def test_a_wrong_fast_win_is_corrected(self):
        bot = self.make()
        slug = self.bet_and_end(bot)
        bot.tick()
        shares, before = read_trades(bot.cfg.trade_log)[-1], bot.client.balance()
        paid = float(shares["shares"])
        self.pm.results[slug] = "down"                         # Polymarket says otherwise
        bot._confirm_pending()
        self.assertEqual((bot.ladder.wins, bot.ladder.losses, bot.ladder.step), (0, 1, 2))
        self.assertAlmostEqual(bot.client.balance(), before - paid, places=4)
        self.assertAlmostEqual(bot.ladder.pnl, -float(shares["cost"]), places=3)
        self.assertGreater(bot.ladder.streak_lost, 0)
        row = read_trades(bot.cfg.trade_log)[-1]
        self.assertEqual((row["result"], row["result_by"], float(row["payout"])), ("down", "corrected", 0.0))
        self.assertEqual(read_events(bot.cfg.folder)[-1]["kind"],
                         "result_fixed")

    def test_a_wrong_fast_loss_is_corrected(self):
        self.feed.end, self.pm.up_price = 63980.0, 0.01        # looks like Down
        bot = self.make()
        slug = self.bet_and_end(bot)
        bot.tick()
        self.assertEqual((bot.ladder.losses, bot.ladder.step), (1, 2))
        self.pm.results[slug] = "up"
        bot._confirm_pending()
        self.assertEqual((bot.ladder.wins, bot.ladder.losses, bot.ladder.step), (1, 0, 1))
        self.assertEqual(bot.ladder.streak_lost, 0)
        self.assertAlmostEqual(bot.client.balance(), 1000 + bot.ladder.pnl, places=4)

    def test_pending_survives_a_restart(self):
        bot = self.make()
        slug = self.bet_and_end(bot)
        bot.tick()
        again = self.make()
        self.assertIn(slug, again.pending)


class LiveClaimWaitTest(unittest.TestCase):
    def test_a_fast_win_is_claimed_only_after_the_official_result(self):
        from poly_bot.live import LiveClient
        clock = Clock(1_800_000_000.0)
        pm = FakePM(clock)
        with tempfile.TemporaryDirectory() as d:
            redeemed = []

            class SDK:
                def redeem_positions(self, condition_id):
                    redeemed.append(condition_id)
                    return type("W", (), {"wait": lambda self: None})()

                def get_balance_allowance(self, **k):
                    return type("B", (), {"balance": "5"})()

            c = LiveClient(Path(d), pm, sdk_client=SDK())
            slug = "btc-updown-5m-1800000000"
            c.data["positions"][slug] = {"side": "up", "shares": 10.0, "cost": 7.0, "condition_id": "0xc",
                                         "end": 1_800_000_300, "token": "t"}
            self.assertEqual(c.settle(slug, "up", confirmed=False), 10.0)
            self.assertEqual(redeemed, [])
            self.assertEqual(c.unclaimed(), 10.0)
            c._claim(slug)                                     # no official result yet: not even tried
            self.assertEqual(redeemed, [])
            pm.results[slug] = "up"
            c._claim(slug)
            self.assertEqual(redeemed, ["0xc"])
            self.assertEqual(c.unclaimed(), 0)


class HistoryGapsTest(unittest.TestCase):
    def test_missed_rounds_and_time_left(self):
        rows = with_gaps([
            {"round": "btc-updown-5m-1794792300", "placed": "2026-11-16 10:26:58"},   # round 10:25-10:30 JST
            {"round": "btc-updown-5m-1794792600", "placed": "2026-11-16 10:33:04"},   # the next one: none missed
            {"round": "btc-updown-5m-1794793500", "placed": ""},                     # 2 rounds later
        ])
        self.assertEqual([r["missed"] for r in rows], ["", "", 2])
        self.assertEqual(rows[0]["bet_left"], 182)
        self.assertEqual(rows[1]["bet_left"], 116)
        self.assertEqual(rows[2]["bet_left"], "")


class SimFastTest(unittest.TestCase):
    def test_settle_after(self):
        from poly_bot.simulate import FAST_SETTLE_SEC, SETTLE_SEC, settle_after
        fast, slow = BotConfig(Path("/tmp"), result_wait="fast"), BotConfig(Path("/tmp"), result_wait="official")
        clear = {"start": 0, "end": 300, "coin": {"o": 64000, "a": [0, 30.0]}}
        close = {"start": 0, "end": 300, "coin": {"o": 64000, "a": [0, 2.0]}}
        self.assertEqual(settle_after(slow, clear), SETTLE_SEC)
        self.assertEqual(settle_after(fast, clear), FAST_SETTLE_SEC)
        self.assertEqual(settle_after(fast, close), SETTLE_SEC)
        self.assertEqual(settle_after(fast, {"start": 0, "end": 300, "h": [(0, 0.5), (295, 0.6)]}), SETTLE_SEC)
        self.assertEqual(settle_after(fast, {"start": 0, "end": 300, "h": [(0, 0.5), (295, 0.97)]}), FAST_SETTLE_SEC)


if __name__ == "__main__":
    unittest.main()
