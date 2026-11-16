"""The Account portfolio tile's chart (live): the wallet's value over time, split into deposits / withdrawals and
trading.

- Deposits and withdrawals come from the blockchain: every pUSD, USDC.e and USDC transfer into or out of the wallet
  (Polygon, a free public server: 10,000 blocks per request). A transaction that is part of Polymarket's activity
  (a trade, a payout, a rebate, a fee) is trading; any other transaction's net flow is money put in or taken out. A
  transaction that only swaps one of the three coins for another (Polymarket's switch from USDC.e to pUSD,
  unwrapping pUSD back to USDC) nets to zero and isn't counted.
- The value over time is the coins in the wallet (the sum of those transfers) plus the open positions at what they
  cost (from the activity: a buy opens or adds, a sale or payout closes; an Up/Down round's position closes when the
  round ends). The last point is the real portfolio value now.

The transfers are cached per wallet in data/wallets/<wallet>.json and topped up from the last block read. The first
scan reads about 40 days of blocks (~400 requests, a few minutes) in the background; the page shows its progress.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from typing import Optional

from .config import write_json
from .instance import ROOT
from .markets import get_json

log = logging.getLogger("wallet")

RPC = "https://polygon-bor-rpc.publicnode.com"      # allows 10,000-block log queries (drpc's free plan doesn't)
DATA_API = "https://data-api.polymarket.com"
TOKENS = {"0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb": "pUSD",
          "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.e",
          "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC"}
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SPAN = 10_000                  # blocks per request (the server's limit)
BLOCK_SEC = 2.0                # Polygon's block time, to find where to start
FOLDER = ROOT / "data" / "wallets"
ROUND_LEN = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
MAX_POINTS = 1500

_lock = threading.Lock()
_jobs: dict[str, dict] = {}                          # wallet -> {"done", "total", "error", "thread"}
_activity: dict[str, tuple[float, list]] = {}         # wallet -> (when read, rows)


def rpc(method: str, params: list, tries: int = 5):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    err = None
    for i in range(tries):
        try:
            import urllib.request
            req = urllib.request.Request(RPC, json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "User-Agent": "poly-bot"})
            with urllib.request.urlopen(req, timeout=30) as r:
                got = json.load(r)
            if "error" not in got:
                return got["result"]
            err = got["error"]
        except Exception as e:  # noqa: BLE001 - a busy public server: try again
            err = e
        time.sleep(2 + 2 * i)
    raise RuntimeError(f"Polygon server: {err}")


def activity(wallet: str) -> list[dict]:
    """Every Polymarket activity row of the wallet (trades, payouts, rebates), oldest first. Cached 60 s."""
    with _lock:
        hit = _activity.get(wallet)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
    rows, cursor = [], None
    for _ in range(200):
        q = {"user": wallet, "limit": 500, **({"cursor": cursor} if cursor else {})}
        page = get_json(DATA_API + "/v2/activity", q, timeout=15)
        rows += page.get("data") or []
        p = page.get("pagination") or {}
        if not p.get("has_more"):
            break
        cursor = p.get("next_cursor")
    rows.sort(key=lambda r: r.get("timestamp") or 0)
    with _lock:
        _activity[wallet] = (time.time(), rows)
    return rows


def _path(wallet: str):
    return FOLDER / f"{wallet.lower()}.json"


def _load(wallet: str) -> dict:
    try:
        return json.loads(_path(wallet).read_text())
    except (OSError, ValueError):
        return {}


def _scan(wallet: str, job: dict, since: float) -> None:
    """Read the wallet's coin transfers from the last block read (or from `since`) to now, into the cache."""
    try:
        w = wallet.lower()
        pad = "0x" + "0" * 24 + w[2:]
        cache = _load(w)
        head = int(rpc("eth_blockNumber", []), 16)
        start = cache.get("to_block", 0) + 1 if cache else head - int((time.time() - since) / BLOCK_SEC) - 43_200
        transfers = cache.get("transfers", [])
        job["total"] = max(1, (head - start) // SPAN + 1)
        b = start
        while b <= head:
            e = min(b + SPAN - 1, head)
            for topics in ([TRANSFER, None, pad], [TRANSFER, pad]):
                for lg in rpc("eth_getLogs", [{"address": list(TOKENS), "fromBlock": hex(b), "toBlock": hex(e),
                                               "topics": topics}]):
                    fr, to = "0x" + lg["topics"][1][-40:], "0x" + lg["topics"][2][-40:]
                    transfers.append({"t": int(lg.get("blockTimestamp") or "0x0", 16), "b": int(lg["blockNumber"], 16),
                                      "tx": lg["transactionHash"].lower(), "i": int(lg["logIndex"], 16),
                                      "token": TOKENS.get(lg["address"].lower(), "?"),
                                      "amt": round(int(lg["data"], 16) / 1e6 * (1 if to == w else -1), 6),
                                      "other": fr if to == w else to})
            job["done"] += 1
            b = e + 1
        seen, uniq = set(), []
        for t in sorted(transfers, key=lambda x: (x["b"], x["i"], x["amt"])):
            key = (t["tx"], t["i"], t["amt"])
            if key not in seen:
                seen.add(key)
                uniq.append(t)
        FOLDER.mkdir(parents=True, exist_ok=True)
        write_json(_path(w), {"wallet": w, "to_block": head, "transfers": uniq})
        log.info("Wallet %s…: %d coin transfers read up to block %d", w[:6], len(uniq), head)
    except Exception as e:  # noqa: BLE001 - shown on the page; the next request tries again
        job["error"] = str(e)
        log.warning("Wallet scan failed: %s", e)


def _transfers(wallet: str, since: float) -> tuple[list, Optional[dict]]:
    """(cached transfers, progress of a scan still running or None). Tops up when older than a minute."""
    w = wallet.lower()
    cache = _load(w)
    with _lock:
        job = _jobs.get(w)
        running = job and job["thread"].is_alive()
        stale = not cache or time.time() - _path(w).stat().st_mtime > 60
        if stale and not running:
            job = {"done": 0, "total": 0, "error": ""}
            job["thread"] = threading.Thread(target=_scan, args=(w, job, since), daemon=True, name="wallet-scan")
            _jobs[w] = job
            job["thread"].start()
            running = True
    if not cache:                                   # the first scan: nothing to show until it's done
        return [], {k: job[k] for k in ("done", "total", "error")}
    return cache.get("transfers", []), None


def round_end(slug: str) -> Optional[float]:
    m = re.search(r"-(5m|15m|1h|4h)-(\d+)$", slug or "")
    return int(m.group(2)) + ROUND_LEN[m.group(1)] if m else None


def build(transfers: list, rows: list, now_value: Optional[float], now: float) -> dict:
    """The chart from the transfers and the activity (both oldest first)."""
    txs = {(r.get("transaction_hash") or "").lower() for r in rows}
    # money put in / taken out: the net of every non-Polymarket transaction (a coin swap nets to zero)
    by_tx: dict[str, list] = {}
    for t in transfers:
        if t["tx"] not in txs:
            by_tx.setdefault(t["tx"], []).append(t)
    flows = []
    for tx, ts in by_tx.items():
        net = sum(t["amt"] for t in ts)
        if abs(net) >= 0.01:
            coins = "/".join(sorted({t["token"] for t in ts}))
            flows.append({"t": min(t["t"] for t in ts), "amount": round(net, 2), "tx": tx,
                          "note": f"{'Deposit' if net > 0 else 'Withdrawal'} ({coins})"})
    flows.sort(key=lambda f: f["t"])
    # value over time: coins in the wallet + open positions at cost. Events: (time, order, kind, condition, $)
    events = [(t["t"], 0, "coin", "", t["amt"]) for t in transfers]
    for r in rows:
        cid, usd, typ = r.get("condition_id") or "", float(r.get("usdc_size") or 0), r.get("type")
        if typ == "TRADE" and r.get("side") == "BUY":
            events.append((r["timestamp"], 1, "buy", cid, usd))
            end = round_end(r.get("slug") or "")
            if end:
                events.append((end, 2, "close", cid, 0.0))
        elif (typ == "TRADE" and r.get("side") == "SELL") or typ == "REDEEM":
            events.append((r["timestamp"], 2, "close", cid, 0.0))
    events.sort(key=lambda e: (e[0], e[1]))
    open_cost: dict[str, float] = {}
    cash = pos = 0.0
    points = []
    for t, _, kind, cid, usd in events:
        if t > now:
            continue
        if kind == "coin":
            cash += usd
        elif kind == "buy":
            open_cost[cid] = open_cost.get(cid, 0.0) + usd
            pos += usd
        else:
            pos -= open_cost.pop(cid, 0.0)
        points.append([t, round(cash + pos, 2)])
    if not points:
        return {"points": [], "flows": flows, "start": 0.0, "put_in": 0.0, "earned": 0.0, "now": now_value}
    if now_value is not None:
        points.append([now, round(now_value, 2)])
    if len(points) > MAX_POINTS:                          # thin out evenly, keep the last
        step = len(points) / MAX_POINTS
        points = [points[int(i * step)] for i in range(MAX_POINTS - 1)] + [points[-1]]
    start = points[0][1]
    first = points[0][0]
    later = [f for f in flows if f["t"] > first]
    put_in = round(sum(f["amount"] for f in later), 2)
    start_flow = {"t": first, "amount": start, "note": "Starting value", "start": True}
    end_value = points[-1][1]
    return {"points": points, "flows": [start_flow] + later, "start": round(start, 2), "put_in": put_in,
            "earned": round(end_value - start - put_in, 2), "now": now_value}


def wallet_history(wallet: str, portfolio: Optional[dict]) -> dict:
    rows = activity(wallet)
    since = min((r["timestamp"] for r in rows), default=time.time())
    transfers, progress = _transfers(wallet, since)
    if progress is not None:
        return {"loading": progress, "points": [], "flows": []}
    out = build(transfers, rows, portfolio["total"] if portfolio else None, time.time())
    out["note"] = ("Deposits and withdrawals: from the blockchain (pUSD, USDC.e, USDC). The value between trades counts "
                   "open positions at what they cost; the last point is the real portfolio value now.")
    return out
