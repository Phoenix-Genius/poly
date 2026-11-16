"""History charts for the Balance and Account portfolio tiles: the value over time, split into money put in or
taken out (deposits, withdrawals, budget changes, resets) and money earned by trading.

Budget balance (any bot): from the bot's trade log. Each bet's row has its pnl and the balance after it; a jump
between two rows that the bet's pnl doesn't explain is money put in or taken out (a Budget change, a Reset, a new
paper balance), named after the settings/reset event logged in between.

Account portfolio (live): see wallet_history.py (Polygon pUSD transfers + Polymarket's activity).
"""
from __future__ import annotations

import calendar
import time
from pathlib import Path
from typing import Optional

from .trades import read_events, read_trades

JST = 9 * 3600


def jst_unix(s: str) -> Optional[float]:
    """'2026-11-17 02:58:31' (JST) -> unix seconds."""
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%d %H:%M:%S")) - JST
    except (TypeError, ValueError):
        return None


def num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def flow_note(events: list[dict], after: float, upto: float) -> str:
    """What changed the balance between two bets: the settings/reset events logged in that time."""
    notes = []
    for e in events:
        t = jst_unix(e.get("time", ""))
        if t is None or not (after < t <= upto) or e.get("kind") not in ("settings", "reset", "paper_reset"):
            continue
        text = e.get("text", "")
        if e.get("kind") == "settings":
            parts = [p.strip() for p in text.removeprefix("Settings:").split("·")
                     if any(w in p for w in ("Budget", "Paper balance", "Network"))]
            if not parts:
                continue
            text = " · ".join(parts)
        notes.append(text)
    return " / ".join(dict.fromkeys(notes))


def budget_history(trade_log: Path, folder: Path, now_balance: Optional[float], now: Optional[float] = None) -> dict:
    """{"points": [[unix, balance]], "flows": [{"t", "amount", "note"}], "start", "put_in", "earned", "now"}."""
    rows = [r for r in read_trades(trade_log) if num(r.get("balance")) is not None and jst_unix(r.get("time", ""))]
    events = read_events(folder)
    points, flows = [], []
    earned = 0.0
    prev_t = prev_bal = None
    for r in rows:
        t, bal, pnl = jst_unix(r["time"]), num(r["balance"]), num(r.get("pnl")) or 0.0
        if prev_bal is None:
            start = bal - pnl
            points.append([jst_unix(r.get("placed", "")) or t - 1, round(start, 2)])
            flows.append({"t": points[0][0], "amount": round(start, 2), "note": "Starting balance", "start": True})
        else:
            adj = bal - prev_bal - pnl
            if abs(adj) >= 0.01:
                points.append([t - 0.5, round(prev_bal + adj, 2)])       # the step happens before this bet's result
                flows.append({"t": t - 0.5, "amount": round(adj, 2),
                              "note": flow_note(events, prev_t, t) or "Balance changed outside trading"})
        earned += pnl
        points.append([t, round(bal, 2)])
        prev_t, prev_bal = t, bal
    now = now or time.time()
    if now_balance is not None and points:
        points.append([now, round(now_balance, 2)])
    start = flows[0]["amount"] if flows else (now_balance or 0.0)
    put_in = sum(f["amount"] for f in flows[1:])
    return {"points": points, "flows": flows, "start": round(start, 2), "put_in": round(put_in, 2),
            "earned": round(earned, 2), "now": round(now_balance, 2) if now_balance is not None else None}
