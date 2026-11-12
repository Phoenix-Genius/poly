# AGENTS.md: guide for AI coding agents

This file is for any AI coding agent working in this repo (Claude Code, GitHub Copilot, Codex, Cursor, Gemini, …).
`CLAUDE.md` points here, so all agents follow the same rules.

**Before you start:** read this file, especially [Working with the owner](#working-with-the-owner), then the top of
[CHANGELOG.md](CHANGELOG.md). CHANGELOG.md is the history (every change: what, why, how, tests); AGENTS.md is the
lasting context (rules, code map, the owner's preferences and open questions).

## Required on every change and every commit

These rules come from the owner (the same as in their `../hyperliquid` project). Follow them without being asked.

1. **Record every change in CHANGELOG.md as you make it**, under `## [Unreleased]` at the top: *what* changed
   (behaviour the user sees), *why*, *how* (key functions, files, data formats, settings) and *tests*.
2. **Bump the patch version once per commit**, not per change. The version is `__version__` in
   [poly_bot/__init__.py](poly_bot/__init__.py), semantic `MAJOR.MINOR.PATCH`. Each commit raises PATCH by one.
   Change MAJOR or MINOR only when the owner asks (then reset the lower numbers to 0).
3. **When committing:** bump the version; rename `## [Unreleased]` to `## [x.y.z] - YYYY-MM-DD` and add a fresh
   empty `## [Unreleased]` above it; stage `__init__.py`, `CHANGELOG.md` and the code together; subject
   `vX.Y.Z: <what changed, in plain words>`, body says what and why.
4. **Run the tests before committing.** If they fail, say so.
5. **Commit or push only when the owner asks.** "commit plz" means: bump, date the changelog, run the tests, commit.
6. **Keep [Working with the owner](#working-with-the-owner) up to date** when the owner states a preference, makes
   or reverses a decision, rejects an idea or leaves a question open.
7. **Tag minor and major versions** as `vMAJOR.MINOR` (e.g. `v0.2`, not `v0.2.0`), annotated, with the commit
   subject as the message. Push tags only when asked.

## What this project is

A martingale bot for **Polymarket's crypto "Up or Down" rounds** (BTC, ETH, SOL, XRP, DOGE, BNB, HYPE; 5m, 15m, 1h,
4h, daily) with a local web UI. Each bot bets one side per round; after a loss the next bet is bigger so that one
win pays back the streak; a win resets to step 1. The ladder is limited by a budget and a max price per share.

Networks: **paper** (real rounds, live order books, real results, virtual money) and **live** (real pUSD in the
owner's Polymarket account through Polymarket's official SDK). Many bots run in one app, each in its own folder.

The user-facing documentation is [README.md](README.md). Keep it in sync when behaviour or settings change.

## Run and test

```bash
./start.sh                                        # creates .venv, opens the web app at http://127.0.0.1:8090
.venv/bin/python -m poly_bot web --no-browser     # same, without opening a browser
.venv/bin/python -m poly_bot run --profile btc-5m # headless: run one bot
.venv/bin/python -m poly_bot plan --profile btc-5m --price 0.5   # print a bot's ladder
python3 -m unittest discover -s tests -t .        # run the tests (no network needed)
```

64-bit Python 3.11-3.14. Paper mode uses the standard library only; live mode imports **`polymarket-client`**
(Polymarket's unified SDK, pinned in requirements.txt) lazily in `live.py`, so tests and paper bots run without it.
The legacy `py-clob-client` is superseded: don't use it.

## Polymarket facts this code relies on (checked 2026-11-09)

- Each coin/timeframe is a Gamma **series** with slug `<coin>-up-or-down-<5m|15m|hourly|4h|daily>` (our `1h` is
  Polymarket's `hourly`). Missing: SOL hourly/daily, DOGE daily.
- Rounds are **events** of that series, each with one market: outcomes `["Up","Down"]`, `clobTokenIds` in the same
  order, `eventStartTime`/`endDate`. Current rounds: `/events?series_id=…&end_date_min=<now>&closed=false&
  order=endDate&ascending=true` (**`closed=false` matters**: without it the query is slow and sometimes HTTP 500).
- A round resolves from Chainlink (TWAP stream) 1-5 min after it ends (seen: 80 s, and 5 min on 2026-11-09 21:10 JST): `closed: true`, `outcomePrices`
  `["1","0"]` (Up) or `["0","1"]` (Down), `umaResolutionStatus: "resolved"`.
- **Results come faster from the CLOB:** `clob.polymarket.com/markets/<conditionId>` shows `closed: true` and a
  token `winner` minutes before Gamma's `outcomePrices`/`closed` update. Polymarket also **pays out won shares
  automatically** (the env config has an `auto_redeem_operator`), often before Gamma says resolved.
- Order book: CLOB `/book?token_id=…`; **asks come highest first**, so `Polymarket.book` sorts them.
- Fees: `feeSchedule {rate: 0.07, exponent: 1, takerOnly: true}`; fee = shares × rate × (p(1−p))^exponent
  (100 shares at 0.50 → $1.75, per Polymarket's docs). Min order 5 shares; tick 0.01 (0.001 near the end).
- Markets are `restricted: true` (geo-blocked for some countries). `https://polymarket.com/api/geoblock` says
  whether the calling IP may trade; `LiveClient.check()` refuses when blocked. Never add ways around it (VPNs etc.).
- **Live trading (SDK `polymarket-client` 0.17.0, checked 2026-11-09):** collateral is **pUSD** (ERC-20 on Polygon,
  6 decimals, 1:1 USDC). `SecureClient.create(private_key, wallet, api_key=RelayerApiKey(key, address))`; wallet
  types EOA / POLY_PROXY (email/Google, legacy) / GNOSIS_SAFE (MetaMask, legacy) / DEPOSIT_WALLET (all accounts
  since 2026-05-04). `place_market_order(token_id, side="BUY", amount=<pUSD>, max_price, order_type="FOK")` →
  `AcceptedOrder(making_amount = pUSD, taking_amount = shares)` or `RejectedOrder(code)`;
  `get_balance_allowance(asset_type="COLLATERAL").balance` is in base units (÷1e6);
  `redeem_positions(condition_id=…).wait()` claims winnings gaslessly (needs the Relayer key);
  `setup_trading_approvals()` does the one-time approvals, **but its list includes the Perps deposit contract**
  (`perps_deposit_contract`), so the bot never calls it: `betting_approvals_missing()` filters that out and the
  order call recovers a missing allowance itself. Approvals are read over the SDK's default public RPC
  `polygon.drpc.org`, which Cloudflare sometimes blocks; treat RPC reads as best-effort. Up/Down rounds are `version: "v1"` (CTF) markets: the
  Gamma `clobTokenIds` are the asset ids. Session keys (a separate trading-only signer) exist but need a Builder key
  approved by Polymarket by email, so the bot uses the owner key instead. The owner's account (email login, 2026-11-09):
  Deposit Wallet = Settings → Profile → "Address" (not the Deposit dialog's bridge address); the Relayer key's
  Signer Address equals the owner key's address; Settings → Session keys only lists keys (no create button); one
  pUSD approval was missing before the first API trade (Check connection sets it up).

## Code map

| File | Role |
|---|---|
| `poly_bot/__init__.py` | `__version__` |
| `poly_bot/__main__.py` | CLI: `web` (default), `run`, `plan` |
| `poly_bot/config.py` | `FIELDS` (every setting: default, type, options, label, help), `BotConfig`, load/save, `write_json` |
| `poly_bot/strategy.py` | Pure maths: fees, `shares_for` (recover/multiply sizing), `build_ladder`, `Ladder` state, `choose_side`, `walk_asks` (all-or-nothing fill) |
| `poly_bot/markets.py` | `Polymarket`: Gamma/CLOB reads (series ids, current/next round, result, book, midpoint), `Round` |
| `poly_bot/live.py` | `LiveClient` (same interface as PaperClient + `check`, `housekeeping`): FOK buys, fills read back, claims with retry (`live-account.json`); keys in the bot's `.env` (`read_keys`/`save_keys`/`keys_view`, secrets never sent to the page); `geoblock()` |
| `poly_bot/paper.py` | `PaperClient`: virtual balance, buys walking the live book, payouts from real results (`paper-account.json`) |
| `poly_bot/engine.py` | `Bot`: the per-bot loop (`tick`: enter → hold → settle), state file, ladder end, Until earn |
| `poly_bot/trades.py` | Trade log CSV |
| `poly_bot/hosted.py` | Hosted multi-user app: `Store` (users, hashed codes/sessions), Resend email, `UserApp`, `Hosted` (apps per user, admin list, `adopt`), handler (cookie `poly_session`, `/api/auth/*`, `/api/me`, `/api/admin/users`, location check on live start/check) |
| `poly_bot/geo.py` | DB-IP country lookup (`CountryDB`), Polymarket's `BLOCKED` countries, `live_allowed(ip)`, `download()` |
| `poly_bot/secretbox.py` | AES-256-GCM for stored keys with `POLY_SECRET_KEY` (no key = plain, the single-user app) |
| `poly_bot/web.py` | `App` (all bots), `Controller` (one bot + thread), HTTP handler/JSON API, logging |
| `poly_bot/instance.py` | Bot folders, per-folder lock, free-port binding (copied from hyperliquid) |
| `poly_bot/clock.py` | JST timestamps (copied from hyperliquid) |
| `poly_bot/static/index.html` | The whole UI (HTML + CSS + vanilla JS); the settings form is built from `/api/schema` |
| `tests/` | unittest: strategy, engine (with `tests/fakes.py`: fake Polymarket + clock), live (fake SDK), web/app |

**Data layout:** every bot is `profiles/<id>/` with `config.json`, `state-<network>.json`, `trades-<network>.csv`,
`paper-account.json`, `live-account.json`, `.env` (live keys, mode 600), `bot.log`, `.lock`. The project folder holds `app.log` and `.app-url`. All git-ignored.

## Conventions

- Match the style: type hints, `from __future__ import annotations`, docstrings that explain *why*, ~120-char lines.
- New setting → add it to `FIELDS` (UI and validation follow) and to the README settings table.
- Keep `strategy.py` free of I/O. Anything that trades goes through the client interface (`balance`, `buy`,
  `settle`, `reset`); a live client must implement the same.
- UI text is plain English for a non-programmer trader. 📈/📉 mark Up/Down; green/red mean win/loss.
- Safety: keep the localhost-only + `X-Requested-With: bot-ui` POST checks, the per-folder lock, the budget check
  and the all-or-nothing fill (a partly filled bet breaks the ladder maths).
- Old data files must keep working; upgrade formats in place.

## Working with the owner

- Same owner as `../hyperliquid` (Hyperliquid martingale bot) and `../bccrashautoextension`. A trader, not a
  programmer: short English requests, often screenshots. Reply in plain, short English with concrete numbers and
  small tables. They ask for changes one at a time, look at the result, then say "commit plz".
- UI preferences carried over from hyperliquid: compact, at a glance, flat design (no rounded corners, no shadows),
  foldable sections with a coloured left stripe on light grey, columns bot list | settings | page, times in JST.
- **Decisions (2026-11-09, first request):** "build martingale about polymarket btc price prediction 5m, 15m …".
  Owner chose: mirror the hyperliquid project's structure (web UI, AGENTS/CHANGELOG, versioning); **paper first,
  live later**; side modes: all of them configurable (incl. follow last result, cheaper side, one side only); all
  timeframes (5m, 15m, 1h, 4h, daily) and coins beyond BTC (ETH, SOL, XRP, …).
- **Live trading (2026-11-09):** the owner asked "how can i play with real fund?". Built with the owner key + Relayer
  key (simplest for a single owner); a Budget was required for live bots until the owner asked (2026-11-10) "if empty budget then use whole budget
  plz": empty = the whole balance, for live as for paper; Start's confirmation then says the whole balance can be
  lost. Start asks for a real-money confirmation.
  Not yet tested with real funds: the first live run should be small (e.g. Budget $20) and watched.
- **The owner uses the app while agents work** (2026-11-09: they made bot `btc-5m-2` from the page mid-session).
  Restarting the app stops their running bots: start them again afterwards and say so.
- **poly.zodypay.com (2026-11-09):** owner asked "lets link to domain poly.zodypay.com". Cloudflare DNS A record
  (DNS only) → 95.217.248.136; Caddy block with `basicauth` (Caddy 2.6.2 spells it `basicauth`, not
  `basic_auth`) user `zody` → localhost:8090; app run with `--public-host poly.zodypay.com`; unit
  `poly-bot.service` enabled by the owner on 2026-11-10 (`systemctl restart poly-bot` after an update; bots
  resume by themselves; `journalctl -u poly-bot` for the log). Keep the password in front: the app has no login of its own (single owner; the
  hyperliquid-style email sign-in was not built). Bots resume after an app restart via `.running` markers.
- **Multi-user (2026-11-09):** "i want anyone can use this bot with their api key and private key. with email
  login." Built `hosted.py` (port of hyperliquid's: email codes via Resend, SQLite users/codes/sessions, one
  `UserApp` per user, admin list; no guests, no deposits). Owner's choices: users paste their **full private key**
  (owner said "store with raw"; it is still stored AES-encrypted with POLY_SECRET_KEY — invisible to everyone,
  protects users if files leak; ask before removing that); **JP/KR/IE/NL/MT allowed** (Polymarket restricts them on
  its website only); location from the **offline DB-IP country DB** (country-level, so all of CA and UA blocked,
  unknown IPs blocked). Polymarket's docs require apps to geoblock users: never weaken `geo.live_allowed` or the
  `_live_refusal` check on start/check. The owner's bots were adopted into user 1 (onetodaysoft@proton.me, admin).
  Bot log keys are `u<id>/<bot>` so users' bots never share a logger. Not done: session keys (need Polymarket
  Builder approval; would let users give trade-only keys), disabling users from the page (API only), a terms page.
- **No sign-in (2026-11-09, same day):** "no need to signin to use this. just make use freely." Guests (cookie
  `poly_session`, 365 days, max 20 new guests per IP per hour) use everything, live included; email sign-in stays
  optional (to keep bots across browsers, and for admins). A guest signing in with an email that already has an
  account has their bots moved (and kept running) into it. Open risk: an abandoned guest's live bot keeps trading
  with nobody able to stop it except the admin.
- **Profit is after fees, always (2026-11-13):** "show after fee plz. only real profit plz." Every profit figure
  (tile, history Net/Total, Until earn, the streak to win back) must match the wallet. Live fees come out of the
  pUSD on top of the order and the balance read right after an order is often stale: never record a live cost
  without the fee.
- **Bet modes (2026-11-13):** side (as before), **hit** (first side to reach a price, default 0.75) and **time**
  (the leader with N seconds left, default 120). The owner wanted them as separate modes, not as extra options on
  side mode. Backtests shown to the owner (7 days of BTC 5m, 1-minute prices): a side first at 0.70-0.80 won 76.9%
  vs ~76% needed (about break-even); a leader above 0.90 with ~2 min left is fairly priced; a leader at 0.60-0.70
  with ~2 min left won ~74% vs ~67% needed (small sample, untested on paper yet). Pay at most empty = no limit.
- **UI choices (2026-11-13):** no "At price" box on the ladder (it prices at the dearest the settings pay);
  Simulation panel after History, with a run-1 history table; Bet mode tile instead of Budget left; Stop menu
  "right away / after a win" like bcauto; bot list drag + pin like hyperliquid; round volume must be real time
  (Gamma's lags: use the CLOB WebSocket, `volume.py`).
- **Versions:** the owner asked for 1.0.0 ("c and p with major update", 2026-11-13).
- **Open questions:** Telegram/Google Sheet notifications like hyperliquid;
  translations; a backtest over past rounds; whether to settle early from the closing price instead of waiting
  1-5 min for the official result (would let 5m bots bet every round).
