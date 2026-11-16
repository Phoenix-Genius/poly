"""The trade log: one CSV row per settled bet, in the bot's trades-<network>.csv."""
from __future__ import annotations

import csv
from pathlib import Path

COLUMNS = ["time", "round", "title", "side", "step", "shares", "price", "cost", "fee", "result", "payout", "pnl",
           "total_pnl", "balance", "placed", "held_sec", "best_price", "slippage", "beat", "coin_at_bet", "coin_end", "result_by", "fast_move"]
# placed      when the bet was bought (JST); time is when it was paid out
# held_sec    seconds from buying to the payout (the round plus Polymarket's wait for the result)
# best_price  the best ask when the bot decided; price is the average actually paid
# slippage    (price - best_price) x shares: what walking the order book cost
# beat        the round's Price To Beat (Chainlink at its start); coin_at_bet / coin_end: the coin's Chainlink price
#             when the bet was bought / at the round's end (History shows each minus beat)
# result_by   "fast" (the bot's own TWAP and the market agreed, Result = fast), "official" (Polymarket's result),
#             "corrected" (a fast result Polymarket's official one overturned: the row was rewritten)
# fast_move   a fast result: the bot's own TWAP end minus its TWAP Price To Beat when it decided (History compares it
#             with Polymarket's official final price minus Price To Beat)
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


def fix_trade(path: Path, slug: str, changes: dict) -> bool:
    """Rewrite the logged bet on round `slug` with `changes` (a fast result that turned out wrong)."""
    if not path.exists():
        return False
    upgrade_trade_log(path)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    hit = False
    for r in reversed(rows):
        if r.get("round") == slug:
            r.update({k: v for k, v in changes.items() if k in COLUMNS})
            hit = True
            break
    if hit:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        tmp.replace(path)
    return hit


def read_trades(path: Path, last: int = 0) -> list[dict]:
    """The logged bets, oldest first (only the newest `last` ones when last > 0)."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-last:] if last else rows


# --- events: Resets and settings changes, shown between the bets in the History table ------------------------------
# One JSON object per line in the bot's events.jsonl (for both networks, so a switch shows too):
# {"time": "YYYY-MM-DD HH:MM:SS" (JST, like a bet's time), "kind": "reset" | "paper_reset" | "settings", "text": "..."}
EVENTS_FILE = "events.jsonl"


def jst_now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 9 * 3600))


def append_event(folder: Path, kind: str, text: str) -> None:
    import json
    with open(Path(folder) / EVENTS_FILE, "a") as f:
        f.write(json.dumps({"time": jst_now(), "kind": kind, "text": text}) + "\n")


def read_events(folder: Path, last: int = 0) -> list[dict]:
    """The logged events, oldest first (only the newest `last` ones when last > 0)."""
    import json
    path = Path(folder) / EVENTS_FILE
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out[-last:] if last else out
