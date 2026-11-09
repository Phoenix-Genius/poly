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
