import unittest

from poly_bot.web import bot_results


class BotResultsTest(unittest.TestCase):
    def test_totals_and_longest_losing_run(self):
        rows = [{"side": "up", "result": "up", "pnl": "2.5", "cost": "10", "fee": "0.1", "time": "a", "placed": "p", "balance": "102.5"},
                {"side": "up", "result": "down", "pnl": "-10", "cost": "10", "fee": "0.1", "time": "b"},
                {"side": "down", "result": "up", "pnl": "-20", "cost": "20", "fee": "0.2", "time": "c"},
                {"side": "down", "result": "down", "pnl": "5", "cost": "30", "fee": "0.3", "time": "d", "balance": "77.5"}]
        r = bot_results(rows)
        self.assertEqual((r["bets"], r["wins"], r["losses"], r["max_loss_run"]), (4, 2, 2, 2))
        self.assertEqual((r["net"], r["staked"], r["fees"], r["best"], r["worst"]), (-22.5, 70.0, 0.7, 5.0, -20.0))
        self.assertEqual((r["win_rate"], r["first"], r["last"], r["balance"]), (50.0, "p", "d", "77.5"))

    def test_no_bets(self):
        r = bot_results([])
        self.assertEqual((r["bets"], r["win_rate"], r["net"], r["first"]), (0, None, 0.0, ""))


if __name__ == "__main__":
    unittest.main()
