import gzip
import http.cookiejar
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from poly_bot import geo, hosted
from poly_bot.geo import CountryDB
from poly_bot.hosted import Hosted, Store, make_hosted_handler
from poly_bot.instance import bind_server
from tests.fakes import Clock, FakePM


def tiny_db(folder: Path) -> CountryDB:
    path = folder / "db.csv.gz"
    with gzip.open(path, "wt") as f:
        f.write("1.0.0.0,1.0.0.255,JP\n3.0.0.0,3.255.255.255,US\n5.0.0.0,5.0.0.255,FI\n")
    return CountryDB(path)


class GeoTest(unittest.TestCase):
    def test_rules(self):
        with tempfile.TemporaryDirectory() as d:
            db = tiny_db(Path(d))
            self.assertEqual(db.country("1.0.0.7"), "JP")
            self.assertTrue(geo.live_allowed("1.0.0.7", db)[0])        # Japan: allowed (owner's choice)
            self.assertFalse(geo.live_allowed("3.1.2.3", db)[0])       # United States: blocked
            self.assertIn("United States", geo.live_allowed("3.1.2.3", db)[1])
            self.assertFalse(geo.live_allowed("9.9.9.9", db)[0])       # unknown: blocked
            self.assertFalse(geo.live_allowed("127.0.0.1", db)[0])


class StoreTest(unittest.TestCase):
    def test_codes(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / "users.db")
            g = s.new_guest("1.2.3.4")
            self.assertIsNone(g["email"])
            code = s.new_code("a@b.com", "1.2.3.4", now=1000)
            with self.assertRaises(ValueError):
                s.new_code("a@b.com", "1.2.3.4", now=1010)              # too soon
            with self.assertRaises(ValueError):
                s.check_code("a@b.com", "000000" if code != "000000" else "111111", now=1020)
            user = s.check_code("a@b.com", code, now=1030)
            self.assertEqual(user["email"], "a@b.com")
            with self.assertRaises(ValueError):
                s.check_code("a@b.com", code, now=1040)                 # a code works once
            token = s.new_session(user["id"])
            self.assertEqual(s.user_for(token)["id"], user["id"])
            s.set_disabled(user["id"], True)
            self.assertIsNone(s.user_for(token))


class HostedHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"COOKIE_INSECURE": "1", "ADMIN_EMAILS": "boss@x.com",
                                                "RESEND_API_KEY": "", "MAX_BOTS": "2"})
        self.env.start()
        self.geo = mock.patch.object(geo, "DB", tiny_db(d))
        self.geo.start()
        self.codes = {}
        self.mail = mock.patch.object(hosted, "send_code_email", side_effect=lambda to, c: self.codes.__setitem__(to, c))
        self.mail.start()
        self.h = Hosted(d / "hosted", pm=FakePM(Clock()))
        self.server = bind_server("127.0.0.1", 18990, make_hosted_handler(self.h))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.h.stop_all()
        for app in self.h.apps.values():
            for c in app.controllers.values():
                c.lock.close()
        for p in (self.mail, self.geo, self.env):
            p.stop()
        self.tmp.cleanup()

    def client(self, ip="1.0.0.7"):
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def call(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(self.base + path, data=data, method="GET" if body is None else "POST",
                                         headers={"X-Requested-With": "bot-ui", "Content-Type": "application/json",
                                                  "X-Forwarded-For": ip})
            try:
                with opener.open(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        return call

    def sign_in(self, call, email, approve=True):
        """Sign in with the emailed code; approve=True also approves the account as the admin would."""
        self.assertEqual(call("/api/auth/code", {"email": email})[0], 200)
        status, body = call("/api/auth/verify", {"email": email, "code": self.codes[email]})
        self.assertEqual(status, 200, body)
        if approve:
            self.h.store.set_approved(self.h.store.user_by_email(email)["id"])

    def test_email_sign_in_is_required(self):
        visitor = self.client()
        status, body = visitor("/api/me")
        self.assertEqual((status, body.get("signin")), (401, True))            # no guest account any more
        self.assertEqual(visitor("/api/bots")[0], 401)
        self.assertEqual(visitor("/api/bots", {"coin": "BTC", "timeframe": "5m"})[0], 401)
        self.sign_in(visitor, "new@x.com")
        me = visitor("/api/me")[1]
        self.assertEqual((me["guest"], me["email"]), (False, "new@x.com"))
        self.assertEqual(visitor("/api/bots", {"coin": "BTC", "timeframe": "5m"})[1]["id"], "btc-5m")

    def test_new_accounts_wait_for_the_admin(self):
        with mock.patch.object(hosted, "send_approval_request") as notify:
            joe = self.client()
            self.sign_in(joe, "joe@x.com", approve=False)
        notify.assert_called_once_with(["boss@x.com"], "joe@x.com")      # the admins are told
        status, body = joe("/api/me")
        self.assertEqual((status, body.get("pending"), body.get("email")), (403, True, "joe@x.com"))
        self.assertEqual(joe("/api/bots")[0], 401)                        # nothing works until approved
        boss = self.client()
        self.sign_in(boss, "boss@x.com", approve=False)                   # admins never wait
        self.assertEqual(boss("/api/me")[0], 200)
        users = boss("/api/admin/users")[1]["users"]
        jid = next(u for u in users if u["email"] == "joe@x.com")["id"]
        self.assertFalse(next(u for u in users if u["id"] == jid)["approved"])
        self.assertEqual(joe(f"/api/admin/users/{jid}/approve", {})[0], 403)   # not by themselves
        users = boss(f"/api/admin/users/{jid}/approve", {})[1]["users"]
        self.assertTrue(next(u for u in users if u["id"] == jid)["approved"])
        self.assertEqual(joe("/api/me")[1]["email"], "joe@x.com")        # in now, same session
        self.assertEqual(joe("/api/bots", {"coin": "BTC", "timeframe": "5m"})[0], 200)
        with mock.patch.object(hosted, "send_approval_request") as notify:
            self.sign_in(joe, "joe@x.com", approve=False)                  # signing in again: no new request
        notify.assert_not_called()

    def test_an_old_guest_cookie_is_signed_out(self):
        guest = self.h.store.new_guest("1.2.3.4")              # made before guests were turned off
        token = self.h.store.new_session(guest["id"])
        req = urllib.request.Request(self.base + "/api/me", headers={"Cookie": f"{hosted.COOKIE}={token}"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 401)

    def test_sign_in_and_users_are_separate(self):
        alice, bob = self.client(), self.client()
        self.sign_in(alice, "alice@x.com")
        self.sign_in(bob, "bob@x.com")
        self.assertEqual(alice("/api/bots", {"coin": "BTC", "timeframe": "5m"})[1]["id"], "btc-5m")
        self.assertEqual(bob("/api/bots", {"coin": "BTC", "timeframe": "5m"})[1]["id"], "btc-5m")   # own namespace
        alice("/api/bots", {"coin": "ETH", "timeframe": "5m"})
        self.assertEqual(alice("/api/bots", {"coin": "SOL", "timeframe": "5m"})[0], 400)           # MAX_BOTS=2
        self.assertEqual(len(bob("/api/bots")[1]["bots"]), 1)
        self.assertEqual(alice("/api/me")[1]["email"], "alice@x.com")
        self.assertEqual(alice("/api/admin/users")[0], 403)
        alice("/api/auth/logout", {})
        self.assertEqual(alice("/api/bots")[0], 401)
        self.assertEqual(alice("/api/me")[0], 401)               # signed out: the sign-in screen again

    def test_live_start_checks_the_users_country(self):
        us = self.client(ip="3.1.2.3")
        self.sign_in(us, "us@x.com")
        us("/api/bots", {"coin": "BTC", "timeframe": "5m"})
        self.assertEqual(us("/api/bots/btc-5m/settings", {"network": "live", "budget_usd": 20})[0], 200)
        status, body = us("/api/bots/btc-5m/start", {})
        self.assertEqual(status, 403)
        self.assertIn("United States", body["error"])
        self.assertFalse(us("/api/me")[1]["live_allowed"])
        us("/api/bots/btc-5m/settings", {"network": "paper"})
        self.assertEqual(us("/api/bots/btc-5m/start", {})[0], 200)                  # paper works anywhere

    def test_admin_sees_users(self):
        boss = self.client()
        self.sign_in(boss, "boss@x.com")
        status, body = boss("/api/admin/users")
        self.assertEqual(status, 200)
        self.assertEqual([u["email"] for u in body["users"]], ["boss@x.com"])

    def test_post_needs_header_and_host(self):
        req = urllib.request.Request(self.base + "/api/auth/code", data=b'{"email":"a@b.com"}', method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)
        req = urllib.request.Request(self.base + "/api/me", headers={"Host": "evil.com"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)


class AdoptTest(unittest.TestCase):
    def test_owner_bots_move_into_account(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "profiles" / "btc-5m").mkdir(parents=True)
            (d / "profiles" / "btc-5m" / "config.json").write_text("{}")
            h = Hosted(d / "hosted", pm=FakePM(Clock()))
            self.assertEqual(h.adopt("Owner@X.com", d / "profiles"), 1)
            uid = h.store.user_by_email("owner@x.com")["id"]
            self.assertTrue((h.folder(uid) / "btc-5m" / "config.json").exists())
            self.assertEqual(h.adopt("owner@x.com", d / "profiles"), 0)


if __name__ == "__main__":
    unittest.main()
