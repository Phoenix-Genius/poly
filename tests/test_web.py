import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from poly_bot.instance import bind_server
from poly_bot.web import App, make_handler
from tests.fakes import Clock, FakePM


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pm = FakePM(Clock())
        self.app = App(Path(self.tmp.name) / "profiles", self.pm)

    def tearDown(self):
        self.app.stop_all()
        for c in list(self.app.controllers.values()):
            if c.lock:
                c.lock.close()
        self.tmp.cleanup()

    def test_create_status_plan_delete(self):
        d = self.app.create({"coin": "ETH", "timeframe": "15m"})
        self.assertEqual(d["id"], "eth-15m")
        self.assertEqual(self.app.create({"coin": "ETH", "timeframe": "15m"})["id"], "eth-15m-2")
        ctl = self.app.get("eth-15m")
        s = ctl.status()
        self.assertEqual((s["coin"], s["timeframe"], s["balance"], s["step"]), ("ETH", "15m", 1000, 1))
        self.assertGreater(s["ladder"]["fits"], 5)
        plan = ctl.plan({"sizing": "multiply", "start_usd": 10, "multiplier": 2, "price": 0.4})
        self.assertAlmostEqual(plan["rows"][0]["cost"], 10, delta=0.02)
        self.assertEqual(ctl.cfg.sizing, "recover")             # plan doesn't save
        ctl.put_settings({"max_price": 0.55, "budget_usd": 200})
        self.assertEqual(json.loads(ctl.config_path.read_text())["max_price"], 0.55)
        with self.assertRaises(ValueError):
            ctl.put_settings({"max_price": 1.5})
        with self.assertRaises(ValueError):
            ctl.put_settings({"min_price": 0.6})                # must be below the max price
        s = ctl.put_settings({"paper_balance_usd": 5000})          # applies at once, no Reset paper needed
        self.assertEqual((s["balance"], s["start_balance"]), (5000, 5000))
        with self.assertRaises(ValueError):
            ctl.put_settings({"target_steps": 8, "max_steps": 5})   # max losses below steps to survive
        plan = ctl.plan({"target_steps": 4, "price": 0.55})   # max price 0.55
        self.assertEqual(plan["auto"]["steps"], 4)
        self.assertGreaterEqual(plan["fits"], 4)
        self.app.delete("eth-15m")
        self.assertNotIn("eth-15m", self.app.controllers)

    def test_keys_and_switch_to_live(self):
        ctl = self.app.get(self.app.create({"coin": "BTC", "timeframe": "5m"})["id"])
        s = ctl.put_settings({"network": "live"})                      # empty budget = the whole balance
        self.assertEqual(s["settings"]["budget_usd"], 0)
        s = ctl.put_settings({"network": "live", "budget_usd": 50})
        self.assertEqual(s["network"], "live")
        self.assertIsNone(s["balance"])                                # no keys yet: shown as "–", page still works
        self.assertFalse(s["keys"]["complete"])
        with self.assertRaises(ValueError):
            ctl.start()                                                # refuses without keys
        s = ctl.put_keys({"private_key": "ab" * 32, "wallet": "0x" + "12" * 20, "relayer_key": "k-secret",
                          "relayer_address": "0x" + "34" * 20})
        self.assertTrue(s["keys"]["complete"])
        self.assertNotIn("k-secret", json.dumps(s))
        self.assertNotIn("ab" * 32, json.dumps(s))
        s = ctl.put_settings({"network": "paper"})
        self.assertEqual(s["balance"], 1000)

    def test_missing_timeframe_refused(self):
        self.pm.missing.add(("SOL", "daily"))
        with self.assertRaises(ValueError):
            self.app.create({"coin": "SOL", "timeframe": "daily"})

    def test_blank_name_gets_an_animal(self):
        from poly_bot.web import ANIMALS
        self.app.create({"coin": "BTC", "timeframe": "5m"})
        self.app.create({"coin": "BTC", "timeframe": "5m", "name": "  "})
        names = [c.cfg.display_name for c in self.app.controllers.values()]
        self.assertTrue(all(n in ANIMALS for n in names), names)
        self.assertEqual(len(set(names)), 2)                           # not the same animal twice
        ctl = self.app.get("btc-5m")
        self.assertIn(ctl.put_settings({"bot_name": ""})["settings"]["bot_name"], ANIMALS)

    def test_no_bot_limit(self):
        for _ in range(7):
            self.app.create({"coin": "BTC", "timeframe": "5m"})
        self.assertEqual(len(self.app.controllers), 7)

    def test_reload_keeps_bots(self):
        self.app.create({"coin": "BTC", "timeframe": "5m", "name": "Fast one"})
        for c in self.app.controllers.values():
            c.lock.close()
        again = App(Path(self.tmp.name) / "profiles", self.pm)
        self.assertEqual(list(again.controllers), ["fast-one"])
        self.assertEqual(again.get("fast-one").cfg.display_name, "Fast one")
        again.get("fast-one").lock.close()

    def test_http_post_needs_header(self):
        server = bind_server("127.0.0.1", 18790, make_handler(self.app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/schema") as r:
                self.assertIn("fields", json.loads(r.read()))
            req = urllib.request.Request(base + "/api/bots", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 403)
            req = urllib.request.Request(base + "/api/bots", data=b'{"coin":"BTC","timeframe":"1h"}', method="POST",
                                         headers={"X-Requested-With": "bot-ui", "Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(json.loads(r.read())["id"], "btc-1h")
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn(b"Poly Martingale", r.read())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()


class ResumeAndHostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pm = FakePM(Clock())
        self.profiles = Path(self.tmp.name) / "profiles"

    def tearDown(self):
        self.tmp.cleanup()

    def close(self, app):
        app.stop_all()
        for c in app.controllers.values():
            c.lock.close()

    def test_running_bots_start_again_with_the_app(self):
        app = App(self.profiles, self.pm)
        a = app.create({"coin": "BTC", "timeframe": "5m"})["id"]
        b = app.create({"coin": "ETH", "timeframe": "5m"})["id"]
        app.get(a).start()
        app.get(b).start()
        app.get(b).stop()                      # stopped by the user: stays stopped
        self.close(app)                        # the app shuts down
        again = App(self.profiles, self.pm)
        again.resume()
        self.assertTrue(again.get(a).running)
        self.assertFalse(again.get(b).running)
        self.close(again)

    def test_public_host(self):
        server = bind_server("127.0.0.1", 18890, make_handler(App(self.profiles, self.pm), ("poly.example.com",)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            for host, code in (("poly.example.com", 200), ("evil.example.com", 403)):
                req = urllib.request.Request(base + "/api/bots", headers={"Host": host})
                try:
                    with urllib.request.urlopen(req) as r:
                        got = r.status
                except urllib.error.HTTPError as e:
                    got = e.code
                self.assertEqual(got, code, host)
        finally:
            server.shutdown()
            server.server_close()


class StreakBetsTest(unittest.TestCase):
    def test_played_steps_of_current_streak(self):
        from poly_bot.web import ladder_view, streak_bets
        from poly_bot.config import BotConfig
        t = lambda step, side, result, cost: {"step": str(step), "side": side, "result": result, "cost": str(cost),
                                              "shares": "5", "price": "0.5"}
        trades = [t(1, "up", "up", 2.5), t(1, "down", "up", 2.7), t(2, "up", "down", 2.6)]
        played = streak_bets(trades, 3)
        self.assertEqual([p["step"] for p in played], ["1", "2"])
        self.assertEqual(streak_bets(trades, 1), [])
        self.assertEqual(streak_bets(trades[:1], 2), [])                 # a win isn't part of a losing streak
        view = ladder_view(BotConfig(base_profit_usd=2), 0.5, 100, 3, 5.3, played)
        self.assertEqual([r["step"] for r in view["rows"]][:3], [1, 2, 3])
        self.assertTrue(view["rows"][0]["played"])
        self.assertAlmostEqual(view["rows"][1]["lost_total"], 5.3)
