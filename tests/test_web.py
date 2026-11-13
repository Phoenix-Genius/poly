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

    def test_reset_and_settings_logged_for_history(self):
        self.app.create({"coin": "BTC", "timeframe": "5m"})
        ctl = self.app.get("btc-5m")
        ctl.put_settings({"entry_mode": "hit", "hit_price": 0.7})
        ctl.put_settings({"hit_price": 0.7})                      # nothing changed: nothing logged
        ctl.reset()
        ev = ctl.status()["events"]
        self.assertEqual([e["kind"] for e in ev], ["settings", "reset"])
        self.assertIn("Bet mode side → hit", ev[0]["text"])
        self.assertIn("Hit price 0.75 → 0.7", ev[0]["text"])
        self.assertIn("was step 1", ev[1]["text"])

    def test_budget_bot_shows_its_own_balance(self):
        self.app.create({"coin": "BTC", "timeframe": "5m"})
        ctl = self.app.get("btc-5m")
        s = ctl.put_settings({"budget_usd": 200})
        self.assertEqual((s["balance"], s["wallet"]), (200, 1000))       # its budget, not the whole account
        ctl.bot.ladder.pnl = 12.5
        ctl.bot.bet = {"slug": "x", "title": "t", "side": "up", "step": 1, "shares": 10, "price": 0.8, "cost": 8.1,
                       "fee": 0.1, "start": 1.8e9, "end": 1.8e9 + 300, "url": "u", "token": "x"}
        self.assertAlmostEqual(ctl.status()["balance"], 200 + 12.5 - 8.1, places=2)
        ctl.bot.bet = None
        self.assertEqual(ctl.put_settings({"budget_usd": 0})["balance"], 1000)   # no budget: the account

    def test_reset_only_ladder_keeps_profit_and_counts(self):
        self.app.create({"coin": "BTC", "timeframe": "5m"})
        ctl = self.app.get("btc-5m")
        lad = ctl.bot.ladder
        lad.step, lad.streak_lost, lad.pnl, lad.wins, lad.losses = 3, 55.55, -30.81, 10, 6
        ctl.bot.halted, ctl.bot.halt_message = "ladder", "Ladder ended at step 3"
        s = ctl.reset_ladder()
        self.assertEqual((s["step"], s["streak_lost"], s["pnl"], s["wins"], s["losses"]), (1, 0, -30.81, 10, 6))
        self.assertEqual(ctl.bot.halted, "")                      # Start works again
        self.assertIn("Reset only ladder", s["events"][-1]["text"])
        s = ctl.reset()
        self.assertEqual((s["pnl"], s["wins"], s["losses"]), (0, 0, 0))

    def test_ladder_shows_the_open_bet_as_placed(self):
        self.app.create({"coin": "BTC", "timeframe": "5m"})
        ctl = self.app.get("btc-5m")
        ctl.put_settings({"entry_mode": "hit", "hit_price": 0.75, "base_profit_usd": 2})
        lad = ctl.bot.ladder
        lad.step, lad.streak_lost = 2, 15.83
        ctl.bot.bet = {"slug": "btc-updown-5m-1800000000", "title": "t", "side": "down", "step": 2,
                       "shares": 111.34, "price": 0.83, "cost": 93.51, "fee": 1.1, "start": 1.8e9,
                       "end": 1.8e9 + 300, "url": "u", "token": "x"}
        view = ctl.status()["ladder"]
        row = next(r for r in view["rows"] if r.get("open"))
        self.assertEqual((row["step"], row["shares"], row["cost"], row["price"]), (2, 111.34, 93.51, 0.83))
        self.assertAlmostEqual(row["win_profit"], 111.34 - 93.51 - 15.83, places=2)
        self.assertEqual(view["price"], 0.83)                    # the next steps at the real price
        nxt = next(r for r in view["rows"] if r["step"] == 3)
        self.assertAlmostEqual(nxt["lost_total"] - nxt["cost"], 15.83 + 93.51, places=1)
        ctl.bot.bet = None

    def test_order_and_pins(self):
        for coin in ("BTC", "ETH", "SOL"):
            self.app.create({"coin": coin, "timeframe": "5m"})
        ids = lambda: [b["id"] for b in self.app.list()["bots"]]   # noqa: E731
        self.assertEqual(ids(), ["btc-5m", "eth-5m", "sol-5m"])     # oldest first
        self.app.set_order({"order": ["sol-5m", "btc-5m", "nope"]})
        self.assertEqual(ids(), ["sol-5m", "btc-5m", "eth-5m"])     # unknown dropped, missing kept at the end
        d = self.app.pin("eth-5m", {"pinned": True})
        self.assertEqual([(b["id"], b["pinned"]) for b in d["bots"]][0], ("eth-5m", True))   # pinned first
        self.app.pin("eth-5m", {"pinned": False})
        self.assertEqual(ids(), ["sol-5m", "btc-5m", "eth-5m"])
        self.app.delete("btc-5m")
        self.assertEqual(ids(), ["sol-5m", "eth-5m"])
        saved = json.loads((Path(self.tmp.name) / "profiles" / ".order.json").read_text())
        self.assertEqual(saved["order"], ["sol-5m", "eth-5m"])

    def test_stop_after_win_endpoints(self):
        self.app.create({"coin": "BTC", "timeframe": "5m"})
        ctl = self.app.get("btc-5m")
        with self.assertRaises(ValueError):
            ctl.stop_after_win()                                  # not running
        ctl.start()
        self.assertTrue(ctl.stop_after_win()["stop_after_win"])
        self.assertFalse(ctl.cancel_after_win()["stop_after_win"])
        ctl.stop_after_win()
        ctl.stop()
        ctl.bot.halted, ctl.bot.halt_message = "after_win", "Stopped after a win"
        s = ctl.status()
        self.assertEqual((s["halted"], s["phase"]), ("", "idle"))  # an asked-for stop isn't shown as a halt

    def test_simulate_uses_form_settings_and_budget(self):
        self.app.create({"coin": "BTC", "timeframe": "5m"})
        ctl = self.app.get("btc-5m")
        r = ctl.simulate({"bets": 200, "runs": 50, "market": "fair", "settings": {"entry_mode": "hit", "hit_price": 0.75}})
        self.assertEqual((r["bets"], r["runs"], r["price"], r["bankroll"]), (200, 50, 0.75, 1000))
        self.assertAlmostEqual(r["p_win"], 0.75)                 # fair market by default
        r = ctl.simulate({"bets": 100, "runs": 10, "bankroll": 300, "win_chance": "80", "market": "fair"})
        self.assertEqual((r["bankroll"], r["p_win"], r["price"]), (300, 0.8, ctl.cfg.max_price))
        self.assertEqual(ctl.cfg.entry_mode, "side")             # simulate doesn't save

    def test_simulate_real_replays_past_rounds(self):
        import random
        from unittest import mock
        rng = random.Random(3)
        rounds = []
        for i in range(300):                                     # rounds whose Up price drifts to its result
            res = rng.choice(["up", "down"])
            end_p = 0.9 if res == "up" else 0.1
            rounds.append({"slug": f"r{i}", "start": 1000.0 + 300 * i, "end": 1300.0 + 300 * i, "result": res,
                           "h": [[t, round(0.5 + (end_p - 0.5) * t / 300, 3)] for t in range(-30, 301, 60)]})

        class FakeHistory:
            def __init__(self, pm):
                pass

            def rounds(self, coin, tf):
                return rounds, None

        self.app.create({"coin": "BTC", "timeframe": "5m"})
        ctl = self.app.get("btc-5m")
        with mock.patch("poly_bot.web.History", FakeHistory):
            r = ctl.simulate({"bets": 50, "runs": 20, "settings": {"entry_mode": "time", "time_left_sec": 120}})
        self.assertEqual(r["mode"], "real")
        self.assertEqual(r["p_win"], 1.0)                        # the leader 2 min before the end always won here
        self.assertGreater(r["price"], 0.7)                      # bought at the leader's real price, not 0.75 flat
        self.assertEqual(r["history"]["rounds"], 300)
        self.assertTrue(all("round" in t for t in r["log"] if not t.get("event")))

        class Loading(FakeHistory):
            def rounds(self, coin, tf):
                return [], {"done": 10, "total": 2000, "error": ""}
        with mock.patch("poly_bot.web.History", Loading):
            self.assertEqual(ctl.simulate({})["loading"]["total"], 2000)

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
        self.assertEqual((s["wallet"], s["start_balance"]), (5000, 5000))
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
        self.assertEqual((s["wallet"], s["balance"]), (1000, 50))          # the account, and the bot's budget

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
