"""Live trading: real bets with the pUSD in your Polymarket account, through Polymarket's official Python SDK
(`polymarket-client`, imported only when a live bot runs, so paper bots need nothing installed).

Same interface as PaperClient: balance(), buy(), settle(), plus housekeeping() for claims that couldn't be made yet.

- **Buy**: a fill-or-kill market order for the cost the bot worked out from the live book, never above the max
  price. The fill (shares received, pUSD spent) is read back from the order and the balance, so fees are included
  whichever way Polymarket charges them.
- **Settle**: a won round's shares are redeemed for pUSD (gasless, through Polymarket's relayer, which is why the
  Relayer API key is needed). If redeeming fails (the chain hasn't caught up yet), the claim is kept in
  live-account.json and retried every minute; the money counts as won right away.

Keys live in the bot's own .env (never sent to the page): POLY_PRIVATE_KEY, POLY_WALLET, POLY_RELAYER_KEY,
POLY_RELAYER_ADDRESS.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from . import secretbox
from .config import write_json
from .markets import Polymarket, Round, get_json

DATA_API = "https://data-api.polymarket.com"
from .strategy import Fill, ceil2, fee_per_share, walk_asks

KEY_VARS = ("POLY_PRIVATE_KEY", "POLY_WALLET", "POLY_RELAYER_KEY", "POLY_RELAYER_ADDRESS")
GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
REDEEM_RETRY_SEC = 60
log = logging.getLogger("poly.live")


class LiveError(ValueError):
    """A live-trading problem to show on the page in plain words (keys missing, blocked, rejected order)."""


# --- keys --------------------------------------------------------------------------------------------------
def read_keys(folder: Path) -> dict:
    env = Path(folder) / ".env"
    keys = {k: "" for k in KEY_VARS}
    if env.exists():
        for line in env.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() in keys:
                keys[k.strip()] = secretbox.decrypt(v.strip().strip('"').strip("'"))
    return keys


def save_keys(folder: Path, body: dict) -> dict:
    """Write the keys in `body` (empty values keep the saved ones) to the bot's .env, readable only by you."""
    keys = read_keys(folder)
    fields = {"private_key": "POLY_PRIVATE_KEY", "wallet": "POLY_WALLET", "relayer_key": "POLY_RELAYER_KEY",
              "relayer_address": "POLY_RELAYER_ADDRESS"}
    for field, var in fields.items():
        value = str(body.get(field) or "").strip()
        if value:
            keys[var] = value
    pk = keys["POLY_PRIVATE_KEY"]
    if pk:
        pk = pk if pk.startswith("0x") else "0x" + pk
        if len(pk) != 66 or not all(c in "0123456789abcdefABCDEF" for c in pk[2:]):
            raise LiveError("Private key: 64 hex characters (with or without 0x)")
        keys["POLY_PRIVATE_KEY"] = pk
    for var, label in (("POLY_WALLET", "Wallet address"), ("POLY_RELAYER_ADDRESS", "Relayer key address")):
        v = keys[var]
        if v and not (v.startswith("0x") and len(v) == 42):
            raise LiveError(f"{label}: an address like 0x1234…abcd (42 characters)")
    env = Path(folder) / ".env"
    # the hosted app (POLY_SECRET_KEY set) stores the secrets encrypted; addresses are public and stay readable
    secret = {"POLY_PRIVATE_KEY", "POLY_RELAYER_KEY"}
    env.write_text("".join(f"{k}={secretbox.encrypt(v) if k in secret else v}\n" for k, v in keys.items() if v))
    try:
        env.chmod(0o600)
    except OSError:
        pass
    return keys_view(keys)


def keys_view(keys: dict) -> dict:
    """What the page may see: which keys are set, and the public addresses. Never the secrets."""
    def mask(v: str) -> str:
        return f"{v[:6]}…{v[-4:]}" if v else ""
    return {"private_key": bool(keys.get("POLY_PRIVATE_KEY")), "wallet": keys.get("POLY_WALLET", ""),
            "relayer_key": mask(keys.get("POLY_RELAYER_KEY", "")),
            "relayer_address": keys.get("POLY_RELAYER_ADDRESS", ""),
            "complete": all(keys.get(k) for k in KEY_VARS)}


def geoblock() -> dict:
    """Polymarket's own check of this computer's location: {"blocked": bool, "country": "JP", ...}."""
    return get_json(GEOBLOCK_URL)


def betting_approvals_missing(sdk) -> list:
    """The approvals betting needs that the wallet hasn't given (Perps' deposit contract left out)."""
    state = sdk.get_trading_approvals_state()
    if state.is_fully_approved:
        return []
    perps = str(sdk._ctx.environment_config.perps_deposit_contract).lower() if hasattr(sdk, "_ctx") else ""
    missing = getattr(state, "missing", None)
    erc20 = [a for a in getattr(missing, "erc20", ()) if str(a.spender).lower() != perps]
    return erc20 + list(getattr(missing, "erc1155", ()))


# --- client ------------------------------------------------------------------------------------------------
class LiveClient:
    def __init__(self, folder: Path, pm: Polymarket, sdk_client=None, logger: Optional[logging.Logger] = None):
        self.folder, self.pm = Path(folder), pm
        self.log = logger or log
        self.path = self.folder / "live-account.json"
        self._sdk = sdk_client                 # tests pass a fake; otherwise made on first use
        self._lock = threading.Lock()
        self.data = {"positions": {}, "claims": {}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except ValueError:
                pass
        self._balance_cache = (0.0, 0.0)       # (value, time)
        self._positions_cache = (None, 0.0)     # (value of open and unclaimed positions, time)
        self._next_claim = 0.0

    def _save(self) -> None:
        write_json(self.path, self.data)

    @property
    def sdk(self):
        if self._sdk is None:
            keys = read_keys(self.folder)
            missing = [k for k in KEY_VARS if not keys[k]]
            if missing:
                raise LiveError("Live trading needs your Polymarket keys: open the Polymarket keys panel.")
            try:
                from polymarket import RelayerApiKey, SecureClient
            except ImportError:
                raise LiveError("The Polymarket SDK isn't installed. Close the app and run start.sh / start.bat "
                                "again (it installs it; needs Python 3.11 or newer).")
            try:
                self._sdk = SecureClient.create(
                    private_key=keys["POLY_PRIVATE_KEY"], wallet=keys["POLY_WALLET"],
                    api_key=RelayerApiKey(key=keys["POLY_RELAYER_KEY"], address=keys["POLY_RELAYER_ADDRESS"]))
            except Exception as e:  # noqa: BLE001 - the SDK raises many kinds; the owner needs the message
                raise LiveError(f"Couldn't sign in to Polymarket with these keys: {e}") from e
        return self._sdk

    # --- checks ------------------------------------------------------------------------------------------
    def check(self) -> dict:
        """Connect and report: location allowed, wallet, wallet type, pUSD balance, betting approvals.

        Read-only: it never approves anything. The SDK's list of "trading approvals" also includes Polymarket
        Perps' deposit contract, which betting doesn't need, so that one is ignored here; if a betting approval
        is missing, the SDK's order call sets it up itself. The approvals are read from a public Polygon server
        that is sometimes busy; then the check says so instead of failing.
        """
        if not all(read_keys(self.folder).values()) and self._sdk is None:
            raise LiveError("Live trading needs your Polymarket keys: open the Polymarket keys panel.")
        geo = geoblock()
        if geo.get("blocked"):
            raise LiveError(f"Polymarket doesn't allow trading from this computer's location "
                            f"({geo.get('country', '?')}). The bot can only paper trade here.")
        sdk = self.sdk
        approved: Optional[bool] = None
        try:
            approved = not betting_approvals_missing(sdk)
        except Exception as e:  # noqa: BLE001 - a busy public RPC must not block trading
            self.log.warning("Couldn't read the trading approvals from the blockchain right now: %s", e)
        return {"country": geo.get("country", ""), "wallet": str(sdk.wallet), "wallet_type": str(sdk.wallet_type),
                "balance": round(self.balance(fresh=True), 2), "approved": approved,
                "claims": round(self.unclaimed(), 2)}

    # --- interface (same as PaperClient) -----------------------------------------------------------------
    def pusd(self) -> float:
        """pUSD in the wallet now."""
        raw = self.sdk.get_balance_allowance(asset_type="COLLATERAL").balance
        return int(raw) / 1e6

    def balance(self, fresh: bool = False) -> float:
        """pUSD in the wallet plus won shares not yet claimed. Cached for 10 s (the page asks every 2 s)."""
        value, at = self._balance_cache
        if fresh or time.time() - at > 10:
            value = self.pusd()
            self._balance_cache = (value, time.time())
        return value + self.unclaimed()

    def unclaimed(self) -> float:
        return sum(c["shares"] for c in self.data["claims"].values())

    def portfolio(self) -> Optional[dict]:
        """The whole account like Polymarket's Portfolio: cash (pUSD) plus every position's value now (open bets of
        every bot on this wallet, and won shares not claimed yet), from Polymarket's data API. Cached 15 s; None when
        it can't be read."""
        value, at = self._positions_cache
        if value is None or time.time() - at > 15:
            try:
                d = get_json(DATA_API + "/v2/value", {"user": str(self.sdk.wallet)}, timeout=5, tries=1)
                value = float((d.get("data") or {}).get("value") or 0.0)
                self._positions_cache = (value, time.time())
            except Exception as e:  # noqa: BLE001 - the tile shows cash only
                self.log.debug("Couldn't read the positions' value: %s", e)
                if value is None:
                    return None
        cash = self.balance() - self.unclaimed()
        return {"cash": round(cash, 2), "positions": round(value, 2), "total": round(cash + value, 2)}

    def buy(self, r: Round, side: str, shares: float, max_price: float) -> Optional[Fill]:
        """Buy about `shares` of `side` in round r with a fill-or-kill market order; None if the book can't fill it."""
        book = self.pm.book(r.token(side))
        plan = walk_asks(book["asks"], shares, max_price, r.fee_rate, r.fee_exponent)
        if plan is None:
            return None
        spend = ceil2(plan.cost - plan.fee)          # the shares' price; fees come on top or out of the shares
        before = self.pusd()
        try:
            resp = self.sdk.place_market_order(token_id=r.token(side), side="BUY", amount=str(spend),
                                               max_price=str(max_price), order_type="FOK")
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "InsufficientLiquidityError":
                return None                              # the book moved; the bot tries again next tick
            raise LiveError(f"The order couldn't be placed: {e}") from e
        if not resp.ok:
            if resp.code in ("fok_not_filled", "unmatched"):
                return None
            if resp.code == "not_enough_balance":
                raise LiveError(f"Not enough pUSD in your Polymarket account for a ${spend:.2f} bet.")
            raise LiveError(f"Polymarket refused the order ({resp.code}): {resp.message}")
        try:
            self.sdk.wait_for_order_fill_settlement(resp, timeout_s=30)
        except Exception as e:  # noqa: BLE001 - the order is filled; settlement on chain is only slower
            self.log.warning("Order %s filled; settlement still pending: %s", resp.order_id, e)
        # From here on the order is filled: nothing may stop the bet being recorded, or the bot would buy again.
        got = float(resp.taking_amount)                 # shares received
        paid = float(resp.making_amount)                # pUSD for the shares
        cost, seen = paid, False
        try:
            after = self.pusd()
            self._balance_cache = (after, time.time())
            spent = before - after
            if paid - 0.01 <= spent <= paid * 1.2 + 0.05:   # all-in (fee included), if the balance agrees
                cost, seen = spent, True
        except Exception as e:  # noqa: BLE001
            self.log.warning("Couldn't read the balance after the bet (%s); using the order's $%.2f", e, paid)
        # Polymarket's documented taker fee; on these rounds it comes out of the pUSD on top of the order (checked
        # against the wallet balance on every live bet so far). The order's shares can be a little under what was
        # asked just from rounding the amount, so a short fill is not a fee.
        fee = got * fee_per_share(paid / got, r.fee_rate, r.fee_exponent) if got else 0.0
        if seen and cost - paid >= 0.005:
            fee = cost - paid             # the balance shows the real fee
        elif not seen:                    # the balance hadn't caught up yet: count the fee, or the profit
            cost = paid + fee             # runs ahead of the wallet
        fill = Fill(round(got, 6), paid / got if got else 0.0, round(cost, 6), round(fee, 6))
        with self._lock:
            pos = self.data["positions"].setdefault(r.slug, {"side": side, "shares": 0.0, "cost": 0.0,
                                                             "condition_id": r.condition_id, "end": r.end,
                                                             "token": r.token(side),
                                                             "order_ids": []})
            pos["shares"] = round(pos["shares"] + fill.shares, 6)
            pos["cost"] = round(pos["cost"] + fill.cost, 6)
            pos["order_ids"].append(resp.order_id)
            self._save()
        return fill

    def settle(self, slug: str, result: str) -> float:
        """The round ended `result`: a won position is claimed (or queued to claim). Returns the payout."""
        with self._lock:
            pos = self.data["positions"].pop(slug, None)
            if not pos:
                return 0.0
            payout = pos["shares"] if pos["side"] == result else 0.0
            if payout:
                self.data["claims"][slug] = {"condition_id": pos["condition_id"], "shares": payout, "tries": 0,
                                             "token": pos.get("token") or self._token(slug, pos["side"])}
            self._save()
        if payout:
            self._claim(slug)
        return payout

    def housekeeping(self) -> None:
        """Retry claims that failed earlier (called by the bot every tick; does something once a minute)."""
        if self.data["claims"] and time.time() >= self._next_claim:
            self._next_claim = time.time() + REDEEM_RETRY_SEC
            for slug in list(self.data["claims"]):
                self._claim(slug)

    def _token(self, slug: str, side: str) -> str:
        try:
            return self.pm.round(slug).token(side)
        except Exception:  # noqa: BLE001 - without it the claim just tries to redeem
            return ""

    def _still_held(self, claim: dict) -> Optional[bool]:
        """Whether the won shares are still in the wallet (None = couldn't tell). Polymarket often pays wins out
        by itself right after the round resolves, and then there's nothing left to claim."""
        if not claim.get("token"):
            return None
        try:
            bal = self.sdk.get_balance_allowance(asset_type="CONDITIONAL", token_id=claim["token"]).balance
            return int(bal) > 0
        except Exception:  # noqa: BLE001
            return None

    def _claim(self, slug: str) -> None:
        claim = self.data["claims"].get(slug)
        if not claim:
            return
        if self._still_held(claim) is False:
            with self._lock:
                self.data["claims"].pop(slug, None)
                self._save()
            self._balance_cache = (0.0, 0.0)
            self.log.info("Polymarket paid the $%.2f win from %s automatically", claim["shares"], slug)
            return
        try:
            self.sdk.redeem_positions(condition_id=claim["condition_id"]).wait()
        except Exception as e:  # noqa: BLE001 - retried later; the owner sees it in the log
            claim["tries"] += 1
            self._save()
            self.log.warning("Claiming $%.2f from %s didn't work yet (try %d), retrying in a minute: %s",
                        claim["shares"], slug, claim["tries"], e)
            return
        with self._lock:
            self.data["claims"].pop(slug, None)
            self._save()
        self._balance_cache = (0.0, 0.0)
        self.log.info("Claimed $%.2f from %s", claim["shares"], slug)

    def reset(self, balance: float) -> None:
        raise LiveError("Reset paper is only for paper bots.")

