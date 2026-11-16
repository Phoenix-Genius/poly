import unittest

from poly_bot.rounds import build, up_at


class RoundsViewTest(unittest.TestCase):
    def test_up_price_is_the_last_point_before(self):
        h = [[-75, 0.5], [13, 0.47], [70, 0.69], [199, 0.91], [504, 0.0005]]
        self.assertEqual((up_at(h, 0), up_at(h, 60), up_at(h, 210), up_at(h, -100)), (0.5, 0.47, 0.91, None))

    def test_row_newest_first_with_moves_and_official_prices(self):
        rounds = [{"slug": "a", "start": 0, "result": "up", "h": [[10, 0.6]]},
                  {"slug": "b", "start": 300, "result": "down", "h": [[200, 0.2]]}]
        paths = {"b": {"a": [float(i) for i in range(300)], "s": 1, "k": [[0, 0, 0], [5, 9.5, -3.25]]}}
        rows = build(rounds, paths, "5m", {300: [100.0, 90.0]})
        self.assertEqual([r["slug"] for r in rows], ["b", "a"])
        b = rows[0]
        self.assertEqual(b["move"][4], 210.0)                    # 90 s left = 210 s in
        self.assertEqual((b["up"][4], b["high"], b["low"], b["beat"], b["final"]), (0.2, 9.5, -3.25, 100.0, 90.0))
        self.assertEqual((rows[1]["move"][0], rows[1]["beat"]), (None, None))


if __name__ == "__main__":
    unittest.main()
