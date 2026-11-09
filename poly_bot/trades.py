"""The trade log: one CSV row per settled bet, in the bot's trades-<network>.csv."""
from __future__ import annotations

import csv
from pathlib import Path

COLUMNS = ["time", "round", "title", "side", "step", "shares", "price", "cost", "fee", "result", "payout", "pnl",
           "total_pnl", "balance", "placed", "held_sec", "best_price", "slippage"]
# placed      when the bet was bought (JST); time is when it was paid out
# held_sec    seconds from buying to the payout (the round plus Polymarket's wait for the result)
# best_price  the best ask when the bot decided; price is the average actually paid
# slippage    (price - best_price) x shares: what walking the order book cost
# Rows written before these columns existed have them empty.


def upgrade_trade_log(path: Path) -> None:
    """Rewrite an older log with today's columns (older rows get the new columns empty)."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == COLUMNS:
            return
        rows = list(reader)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") or "" for k in COLUMNS})
    tmp.replace(path)


def append_trade(path: Path, row: dict) -> None:
    new = not path.exists()
    if not new:
        upgrade_trade_log(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def read_trades(path: Path, last: int = 0) -> list[dict]:
    """The logged bets, oldest first (only the newest `last` ones when last > 0)."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-last:] if last else rows
