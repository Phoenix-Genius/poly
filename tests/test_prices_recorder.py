import tempfile
import unittest
from pathlib import Path

from poly_bot.prices import PriceFeed, Recorder

DAY = 1794700800          # a UTC midnight


class RecorderTest(unittest.TestCase):
    def test_writes_each_second_once_in_order_and_resumes(self):
        d = Path(tempfile.mkdtemp())
        r = Recorder(d)
        r.add("btc/usd", [(DAY + 5, 64700.1), (DAY + 6, 64701.25)])
        r.add("btc/usd", [(DAY + 4, 1.0), (DAY + 6, 2.0), (DAY + 7, 64702.123456)])   # a reconnect's backfill
        f = d / "btc" / "2026-11-15.csv"
        self.assertEqual(f.read_text().splitlines(),
                         ["unix_sec,price", f"{DAY + 5},64700.1", f"{DAY + 6},64701.25", f"{DAY + 7},64702.123456"])
        Recorder(d).add("btc/usd", [(DAY + 7, 9.0), (DAY + 8, 64703.0)])              # after a restart
        self.assertEqual(f.read_text().splitlines()[-2:], [f"{DAY + 7},64702.123456", f"{DAY + 8},64703.0"])

    def test_new_file_each_utc_day(self):
        d = Path(tempfile.mkdtemp())
        Recorder(d).add("eth/usd", [(DAY - 1, 3000.5), (DAY, 3001.0)])
        self.assertTrue((d / "eth" / "2026-11-14.csv.gz").exists())          # the finished day is gzipped
        self.assertFalse((d / "eth" / "2026-11-14.csv").exists())
        self.assertTrue((d / "eth" / "2026-11-15.csv").exists())

    def test_keeps_only_its_coins(self):
        d = Path(tempfile.mkdtemp())
        r = Recorder(d, coins=("btc",))
        r._handle('{"payload":{"symbol":"doge/usd","timestamp":%d000,"value":0.2}}' % DAY)
        r._handle('{"payload":{"symbol":"btc/usd","timestamp":%d000,"value":64000.5}}' % DAY)
        self.assertEqual([p.name for p in d.iterdir()], ["btc"])


class PriceFeedTest(unittest.TestCase):
    def test_keeps_watched_coins_from_the_all_coins_stream(self):
        f = PriceFeed(clock=lambda: DAY + 1)
        f._symbols = {"btc/usd", "eth/usd"}           # watched (without starting the socket)
        for sym, v in (("btc/usd", 64000.5), ("eth/usd", 1800.25), ("doge/usd", 0.2)):
            f._handle('{"payload":{"symbol":"%s","timestamp":%d000,"value":%s}}' % (sym, DAY, v))
        self.assertEqual(f.price("BTC"), 64000.5)
        self.assertEqual(f.price("ETH"), 1800.25)
        self.assertNotIn("doge/usd", f._series)


class TwapTest(unittest.TestCase):
    def feed(self, now):
        f = PriceFeed(clock=lambda: now)
        f._symbols = {"btc/usd"}
        return f

    def test_twap_is_the_mean_from_62_to_3_seconds_before(self):
        f = self.feed(DAY + 100)
        f._series["btc/usd"] = {DAY + i: 64000.0 + i for i in range(-80, 101)}
        self.assertAlmostEqual(f.twap("BTC", DAY), 64000.0 + (-62 - 3) / 2)     # seconds -62 .. -3
        self.assertAlmostEqual(f.twap("BTC"), 64000.0 + (38 + 97) / 2)          # now = 100 s: 38 .. 97

    def test_a_gap_means_no_twap_and_the_spot_fallback(self):
        from types import SimpleNamespace
        f = self.feed(DAY + 100)
        f._series["btc/usd"] = {DAY + i: 64000.0 + i for i in range(-20, 101)}  # nothing before -20 s
        f._last["btc/usd"] = (DAY + 100, 64100.0)
        self.assertIsNone(f.twap("BTC", DAY))
        r = SimpleNamespace(slug="btc-updown-5m-x", start=DAY, end=DAY + 300)
        f._opens[r.slug] = 64000.0                                                # the spot Price To Beat
        now, beat, exact = f.settle_prices("BTC", r, "5m")
        self.assertEqual((now, beat, exact), (64100.0, 64000.0, False))


if __name__ == "__main__":
    unittest.main()
