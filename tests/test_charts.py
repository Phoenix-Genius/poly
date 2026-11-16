import csv
import json
import tempfile
import unittest
from pathlib import Path

from poly_bot.charts import budget_history, jst_unix
from poly_bot.trades import COLUMNS
from poly_bot.wallet_history import build


class BudgetHistoryTest(unittest.TestCase):
    def test_bets_earn_and_a_budget_change_is_money_put_in(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            log = folder / "trades-live.csv"
            rows = [("2026-11-16 10:05:00", "2026-11-16 10:03:00", 2.5, 1002.5),
                    ("2026-11-16 10:10:00", "2026-11-16 10:08:00", -10.0, 992.5),
                    ("2026-11-16 10:20:00", "2026-11-16 10:18:00", 3.0, 1495.5)]   # budget raised by 500 before
            with open(log, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS)
                w.writeheader()
                for t, placed, pnl, bal in rows:
                    w.writerow({"time": t, "placed": placed, "pnl": pnl, "balance": bal})
            (folder / "events.jsonl").write_text(json.dumps(
                {"time": "2026-11-16 10:12:00", "kind": "settings",
                 "text": "Settings: Budget (USDC) 1000 → 1500 · Until earn ($) 0 → 200"}) + "\n")
            h = budget_history(log, folder, 1497.0, now=jst_unix("2026-11-16 10:25:00"))
        self.assertEqual((h["start"], h["put_in"], h["earned"], h["now"]), (1000.0, 500.0, -4.5, 1497.0))
        self.assertEqual([f["note"] for f in h["flows"]], ["Starting balance", "Budget (USDC) 1000 → 1500"])
        self.assertEqual(h["points"][-1][1], 1497.0)


class WalletHistoryTest(unittest.TestCase):
    def test_deposits_withdrawals_swaps_and_trading(self):
        w = "0xabc"
        transfers = [
            {"t": 100, "tx": "0xd1", "token": "USDC", "amt": 1000.0},          # deposit
            {"t": 200, "tx": "0xb1", "token": "pUSD", "amt": -50.0},           # buy (in the activity)
            {"t": 600, "tx": "0xr1", "token": "pUSD", "amt": 80.0},            # payout (in the activity)
            {"t": 700, "tx": "0xs1", "token": "USDC.e", "amt": -500.0},        # swap USDC.e -> pUSD: not money in
            {"t": 700, "tx": "0xs1", "token": "pUSD", "amt": 500.0},
            {"t": 800, "tx": "0xw1", "token": "USDC", "amt": -300.0},          # withdrawal
        ]
        rows = [{"timestamp": 200, "type": "TRADE", "side": "BUY", "usdc_size": 50, "condition_id": "c1",
                 "transaction_hash": "0xb1", "slug": "btc-updown-5m-150"},
                {"timestamp": 600, "type": "REDEEM", "usdc_size": 80, "condition_id": "c1", "transaction_hash": "0xr1"}]
        h = build(transfers, rows, 730.0, now=900)
        flows = [(f["amount"], f["note"]) for f in h["flows"] if not f.get("start")]
        self.assertEqual(flows, [(-300.0, "Withdrawal (USDC)")])        # the deposit is the start, the swap is gone
        self.assertEqual((h["start"], h["put_in"], h["earned"]), (1000.0, -300.0, 30.0))
        values = dict((t, v) for t, v in h["points"])
        self.assertEqual(values[200], 1000.0)                           # the bet at cost: no change
        self.assertEqual(values[450], 950.0)                            # the round ended (150 + 300): lost until paid
        self.assertEqual(values[600], 1030.0)


if __name__ == "__main__":
    unittest.main()
