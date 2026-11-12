import json
import unittest
from unittest import mock

from poly_bot import markets
from poly_bot.markets import Polymarket


def event(closed=False, prices=("0.99", "0.01")):
    return [{"slug": "btc-updown-5m-100", "title": "R", "markets": [{
        "outcomes": json.dumps(["Up", "Down"]), "clobTokenIds": json.dumps(["111", "222"]),
        "outcomePrices": json.dumps(list(prices)), "closed": closed, "conditionId": "0xc",
        "eventStartTime": "1970-01-01T00:01:40Z", "endDate": "1970-01-01T00:06:40Z"}]}]


class RoundResultTest(unittest.TestCase):
    def answer(self, gamma, clob):
        def get_json(url, params=None, **kw):
            return gamma if "gamma" in url else clob
        return mock.patch.object(markets, "get_json", side_effect=get_json)

    def test_clob_winner_before_gamma(self):
        pm = Polymarket(clock=lambda: 500)
        with self.answer(event(), {"closed": True, "tokens": [{"token_id": "111", "winner": False},
                                                             {"token_id": "222", "winner": True}]}):
            self.assertEqual(pm.round("btc-updown-5m-100").result, "down")

    def test_clob_not_closed_yet(self):
        pm = Polymarket(clock=lambda: 500)
        with self.answer(event(), {"closed": False, "tokens": []}):
            self.assertIsNone(pm.round("btc-updown-5m-100").result)

    def test_gamma_result_wins(self):
        pm = Polymarket(clock=lambda: 500)
        with self.answer(event(closed=True, prices=("1", "0")), {}):
            self.assertEqual(pm.round("btc-updown-5m-100").result, "up")

    def test_no_clob_before_round_end(self):
        pm = Polymarket(clock=lambda: 200)
        with self.answer(event(), {"closed": True, "tokens": [{"token_id": "111", "winner": True}]}):
            self.assertIsNone(pm.round("btc-updown-5m-100").result)


if __name__ == "__main__":
    unittest.main()


class VolumeFeedTest(unittest.TestCase):
    def test_counts_trades_per_round(self):
        import json
        from poly_bot.volume import VolumeFeed
        f = VolumeFeed(clock=lambda: 1000.0)
        f._rounds["r1"] = {"usd": 0.0, "shares": 0.0, "trades": 0, "since": 990.0, "start": 1000.0, "end": 1300.0,
                           "partial": False}
        f._token_slug.update({"up1": "r1", "down1": "r1"})
        f._handle(json.dumps([{"event_type": "last_trade_price", "asset_id": "up1", "price": "0.6", "size": "10"},
                              {"event_type": "last_trade_price", "asset_id": "down1", "price": "0.4", "size": "5"},
                              {"event_type": "book", "asset_id": "up1"},
                              {"event_type": "last_trade_price", "asset_id": "other", "price": "0.5", "size": "99"}]))
        f._handle("PONG")
        v = f.volume("r1")
        self.assertEqual((v["trades"], v["shares"]), (2, 15.0))
        self.assertAlmostEqual(v["usd"], 8.0)
        self.assertIsNone(f.volume("nope"))
