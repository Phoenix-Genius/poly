import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from poly_bot import live
from poly_bot.config import BotConfig
from poly_bot.engine import Bot
from poly_bot.live import LiveClient, LiveError, keys_view, read_keys, save_keys
from tests.fakes import Clock, FakePM

PK = "0x" + "ab" * 32
WALLET = "0x" + "12" * 20


class FakeSDK:
    """Stands in for polymarket.SecureClient: a pUSD balance, FOK market buys at a given price, redeems."""

    def __init__(self, pusd=100.0, price=0.5, fee_in_usd=True):
        self.pusd, self.price, self.fee_in_usd = pusd, price, fee_in_usd
        self.orders, self.redeemed = [], []
        self.redeem_fails = 0
        self.reject = None
        self.wallet, self.wallet_type = WALLET, "DEPOSIT_WALLET"

    auto_paid = False

    def get_balance_allowance(self, asset_type, token_id=None):
        if asset_type == "CONDITIONAL":
            return SimpleNamespace(balance=0 if self.auto_paid else 10_000_000, allowances={})
        return SimpleNamespace(balance=int(round(self.pusd * 1e6)), allowances={})

    def place_market_order(self, *, token_id, side, amount, max_price, order_type):
        self.orders.append(dict(token_id=token_id, amount=amount, max_price=max_price, order_type=order_type))
        if self.reject:
            return SimpleNamespace(ok=False, code=self.reject, message="no")
        amount = float(amount)
        shares = round(amount / self.price, 2)
        fee = shares * 0.07 * self.price * (1 - self.price)
        self.pusd -= amount + (fee if self.fee_in_usd else 0)
        got = shares if self.fee_in_usd else shares - fee / self.price
        return SimpleNamespace(ok=True, order_id=f"o{len(self.orders)}", making_amount=Decimal(str(amount)),
                               taking_amount=Decimal(str(round(got, 6))))

    def wait_for_order_fill_settlement(self, order, timeout_s=30):
        return ()

    def redeem_positions(self, *, condition_id):
        if self.redeem_fails:
            self.redeem_fails -= 1
            raise RuntimeError("not resolved on chain yet")
        self.redeemed.append(condition_id)
        return SimpleNamespace(wait=lambda: None)

    approvals_ok = True
    perps = "0xdca4af75705dbb50f62437045aff9921947917d2"

    def get_trading_approvals_state(self):
        if self.approvals_ok is None:
            raise RuntimeError("Request to https://polygon.drpc.org was blocked by Cloudflare with status 400")
        missing = SimpleNamespace(erc20=(SimpleNamespace(spender=self.perps),) if not self.approvals_ok else (),
                                  erc1155=())
        return SimpleNamespace(is_fully_approved=False, missing=missing)

    _ctx = SimpleNamespace(environment_config=SimpleNamespace(perps_deposit_contract=perps))

    def setup_trading_approvals(self):
        raise AssertionError("check() must never approve anything")


class KeysTest(unittest.TestCase):
    def test_save_read_mask(self):
        with tempfile.TemporaryDirectory() as d:
            view = save_keys(Path(d), {"private_key": "ab" * 32, "wallet": WALLET, "relayer_key": "relay-secret-123",
                                       "relayer_address": WALLET})
            self.assertTrue(view["complete"])
            self.assertNotIn("relay-secret-123", json.dumps(view))
            self.assertEqual(read_keys(Path(d))["POLY_PRIVATE_KEY"], PK)          # 0x added
            save_keys(Path(d), {"wallet": WALLET})                                 # empty fields keep the rest
            self.assertEqual(read_keys(Path(d))["POLY_RELAYER_KEY"], "relay-secret-123")
            self.assertEqual((Path(d) / ".env").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(LiveError):
                save_keys(Path(d), {"private_key": "123"})
            with self.assertRaises(LiveError):
                save_keys(Path(d), {"wallet": "0x12"})

    def test_missing_keys(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LiveError):
                LiveClient(Path(d), FakePM(Clock())).balance()


class LiveClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = Clock(1_800_000_000.0)
        self.pm = FakePM(self.clock, up=0.5, down=0.5)
        self.sdk = FakeSDK()

    def tearDown(self):
        self.tmp.cleanup()

    def round(self):
        return self.pm.current_round("BTC", "5m")[0]

    def test_buy_records_actual_fill(self):
        c = LiveClient(self.dir, self.pm, self.sdk)
        fill = c.buy(self.round(), "up", 10, 0.6)
        self.assertEqual(self.sdk.orders[0]["order_type"], "FOK")
        self.assertEqual(self.sdk.orders[0]["max_price"], "0.6")
        self.assertEqual(self.sdk.orders[0]["amount"], "5.0")
        self.assertAlmostEqual(fill.shares, 10)
        self.assertAlmostEqual(fill.cost, 5 + 10 * 0.0175, places=4)       # fee included from the balance
        self.assertAlmostEqual(c.balance(fresh=True), 100 - fill.cost, places=4)
        saved = json.loads((self.dir / "live-account.json").read_text())
        self.assertIn(self.round().slug, saved["positions"])

    def test_fee_counted_when_balance_lags(self):
        c = LiveClient(self.dir, self.pm, self.sdk)
        real = self.sdk.place_market_order
        def lagging(**kw):                 # the balance read right after the order still shows the old amount
            before = self.sdk.pusd
            resp = real(**kw)
            self.sdk.pusd, self.lagged = before, self.sdk.pusd
            return resp
        self.sdk.place_market_order = lagging
        fill = c.buy(self.round(), "up", 10, 0.6)
        self.assertAlmostEqual(fill.cost, 5 + 10 * 0.0175, places=4)       # fee counted though the balance lagged
        self.assertAlmostEqual(fill.cost, 100 - self.lagged, places=4)

    def test_fee_taken_in_shares(self):
        self.sdk.fee_in_usd = False
        c = LiveClient(self.dir, self.pm, self.sdk)
        fill = c.buy(self.round(), "up", 10, 0.6)
        self.assertLess(fill.shares, 10)
        self.assertAlmostEqual(fill.cost, 5.0, places=4)

    def test_unfilled_and_rejected(self):
        c = LiveClient(self.dir, self.pm, self.sdk)
        self.sdk.reject = "fok_not_filled"
        self.assertIsNone(c.buy(self.round(), "up", 10, 0.6))
        self.sdk.reject = "not_enough_balance"
        with self.assertRaises(LiveError):
            c.buy(self.round(), "up", 10, 0.6)
        self.pm.prices["up"] = 0.7
        self.sdk.reject = None
        self.assertIsNone(c.buy(self.round(), "up", 10, 0.6))               # book above max price: no order
        self.assertEqual(len(self.sdk.orders), 2)

    def test_settle_win_claims_loss_does_not(self):
        c = LiveClient(self.dir, self.pm, self.sdk)
        r = self.round()
        c.buy(r, "up", 10, 0.6)
        self.assertAlmostEqual(c.settle(r.slug, "up"), 10)
        self.assertEqual(len(self.sdk.redeemed), 1)
        self.assertEqual(c.unclaimed(), 0)
        c.buy(r, "down", 10, 0.6)
        self.assertEqual(c.settle(r.slug, "up"), 0)
        self.assertEqual(len(self.sdk.redeemed), 1)

    def test_auto_paid_win_is_not_claimed_again(self):
        self.sdk.auto_paid = True
        c = LiveClient(self.dir, self.pm, self.sdk)
        r = self.round()
        c.buy(r, "up", 10, 0.6)
        self.assertAlmostEqual(c.settle(r.slug, "up"), 10)
        self.assertEqual(self.sdk.redeemed, [])
        self.assertEqual(c.unclaimed(), 0)

    def test_failed_claim_is_retried(self):
        c = LiveClient(self.dir, self.pm, self.sdk)
        r = self.round()
        c.buy(r, "up", 10, 0.6)
        self.sdk.redeem_fails = 1
        c.settle(r.slug, "up")
        self.assertAlmostEqual(c.unclaimed(), 10)
        bal = c.balance(fresh=True)
        self.assertAlmostEqual(bal, self.sdk.pusd + 10)                     # counted as won already
        again = LiveClient(self.dir, self.pm, self.sdk)                      # survives a restart
        again.housekeeping()
        self.assertEqual(again.unclaimed(), 0)
        self.assertEqual(len(self.sdk.redeemed), 1)

    def test_balance_read_failure_after_fill_still_records(self):
        c = LiveClient(self.dir, self.pm, self.sdk)
        real = self.sdk.get_balance_allowance
        calls = {"n": 0}

        def flaky(asset_type):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("network")
            return real(asset_type)
        self.sdk.get_balance_allowance = flaky
        fill = c.buy(self.round(), "up", 10, 0.6)
        self.assertIsNotNone(fill)
        self.assertIn(self.round().slug, c.data["positions"])

    def test_check_blocked_location(self):
        c = LiveClient(self.dir, self.pm, self.sdk)
        with mock.patch.object(live, "geoblock", return_value={"blocked": True, "country": "US"}):
            with self.assertRaises(LiveError):
                c.check()
        with mock.patch.object(live, "geoblock", return_value={"blocked": False, "country": "JP"}):
            self.assertEqual(c.check()["balance"], 100)
            self.sdk.approvals_ok = False             # only the Perps approval missing: fine for betting
            self.assertTrue(c.check()["approved"])
            self.sdk.approvals_ok = None              # the public blockchain server is busy: still connects
            res = c.check()
            self.assertIsNone(res["approved"])
            self.assertEqual(res["balance"], 100)

    def test_engine_with_live_client(self):
        cfg = BotConfig(self.dir, network="live", budget_usd=50, side_mode="up_only", base_profit_usd=2.5)
        bot = Bot(cfg, LiveClient(self.dir, self.pm, self.sdk), self.pm, clock=self.clock)
        self.clock.t += 5
        bot.tick()
        self.assertIsNotNone(bot.bet)
        slug = bot.bet["slug"]
        self.pm.results[slug] = "down"
        self.clock.t = bot.bet["end"] + 1
        bot.tick()
        self.assertEqual(bot.ladder.step, 2)
        self.assertAlmostEqual(bot.client.balance(fresh=True), 100 + bot.ladder.pnl, places=3)


if __name__ == "__main__":
    unittest.main()


try:
    import Crypto  # noqa: F401
    HAVE_AES = True
except ImportError:
    HAVE_AES = False


@unittest.skipUnless(HAVE_AES, "pycryptodome not installed (run the tests with .venv/bin/python)")
class EncryptedKeysTest(unittest.TestCase):
    def test_hosted_keys_are_encrypted_at_rest(self):
        from poly_bot import secretbox
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {"POLY_SECRET_KEY": secretbox.new_key()}):
            save_keys(Path(d), {"private_key": "ab" * 32, "wallet": WALLET, "relayer_key": "relay-secret-123",
                                "relayer_address": WALLET})
            raw = (Path(d) / ".env").read_text()
            self.assertNotIn("ab" * 32, raw)
            self.assertNotIn("relay-secret-123", raw)
            self.assertIn(WALLET, raw)
            self.assertEqual(read_keys(Path(d))["POLY_PRIVATE_KEY"], PK)
            self.assertEqual(read_keys(Path(d))["POLY_RELAYER_KEY"], "relay-secret-123")
