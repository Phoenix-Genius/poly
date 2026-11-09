"""Paper trading: bets on the real Polymarket rounds with virtual USDC.

Buys walk the round's live order book (cheapest asks first, never above the max price, all or nothing), the fee is
Polymarket's taker fee for that market, and positions are paid out from the round's real result: $1 per winning
share, $0 per losing one. Saved to the bot's paper-account.json.

Simplification: Polymarket takes the fee on a buy in shares; paper charges it in USDC on top. The profit is the same
to within a cent on these bet sizes.

A live client (a later version) must offer the same methods: balance(), buy(), settle().
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from .config import write_json
from .markets import Polymarket, Round
from .strategy import Fill, walk_asks


class PaperClient:
    def __init__(self, path: Path, start_balance: float, pm: Polymarket):
        self.path, self.pm = Path(path), pm
        self._lock = threading.Lock()
        self.data = {"balance": float(start_balance), "start_balance": float(start_balance), "positions": {}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except ValueError:
                pass
        else:
            self._save()

    def _save(self) -> None:
        write_json(self.path, self.data)

    def balance(self) -> float:
        return float(self.data["balance"])

    def positions(self) -> dict:
        return dict(self.data["positions"])

    def buy(self, r: Round, side: str, shares: float, max_price: float) -> Optional[Fill]:
        """Buy `shares` of `side` in round r, or None when the book can't fill it within max_price."""
        book = self.pm.book(r.token(side))
        fill = walk_asks(book["asks"], shares, max_price, r.fee_rate, r.fee_exponent)
        if fill is None:
            return None
        with self._lock:
            if fill.cost > self.balance() + 1e-9:
                raise ValueError(f"Not enough paper balance: the bet costs ${fill.cost:.2f}, "
                                 f"the balance is ${self.balance():.2f}")
            self.data["balance"] = round(self.balance() - fill.cost, 6)
            pos = self.data["positions"].setdefault(r.slug, {"side": side, "shares": 0.0, "cost": 0.0,
                                                             "end": r.end, "time": time.time()})
            pos["shares"] = round(pos["shares"] + fill.shares, 6)
            pos["cost"] = round(pos["cost"] + fill.cost, 6)
            self._save()
        return fill

    def settle(self, slug: str, result: str) -> float:
        """Pay out the position in round `slug` now that it ended `result`. Returns the payout (0 for a loss)."""
        with self._lock:
            pos = self.data["positions"].pop(slug, None)
            if not pos:
                return 0.0
            payout = pos["shares"] if pos["side"] == result else 0.0
            self.data["balance"] = round(self.balance() + payout, 6)
            self._save()
            return payout

    def reset(self, balance: float) -> None:
        with self._lock:
            self.data = {"balance": float(balance), "start_balance": float(balance), "positions": {}}
            self._save()
