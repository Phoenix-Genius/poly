# Changelog

Every change, newest first. See AGENTS.md for the rules (what / why / how / tests for each entry).

## [Unreleased]

## [0.1.2] - 2026-11-11

### Max price defaults to 0.55
- **What:** a new bot's **Max price** is 0.55 (was 0.60).
- **Why:** the owner's setting change, made before this commit.
- **How:** `max_price` default in `config.py`; README table.
- **Tests:** existing tests.

### No bot limit; a blank Bot name gets a random animal
- **What:** users can make as many bots as they like (before: 5). A new bot with no name, or a bot whose Bot name
  is cleared and saved, is called a random animal (Fox, Otter, Falcon, …) that none of the user's other bots has.
- **Why:** the owner: "why bot limit 5? plz make it unlimited. and name random animal, if blank name input."
- **How:** `MAX_BOTS` defaults to 0 (= no limit) in `hosted.py` and the server's `.env`; `App.animal_name`,
  used by `App.create` and `Controller.put_settings` (via `Controller.name_for_blank`). A nameless bot's id stays
  coin-timeframe (`btc-5m`), so links keep working.
- **Tests:** `test_web`: blank names get different animals (also on save); 7 bots can be made.

### Fixed: Reset stuck with "A bet is open" on a stopped bot
- **What:** **Reset** on a stopped bot now settles an open bet whose round has already ended and has a result
  (records the win/loss in History, claims a win unless it was already claimed by hand), then resets. It's still
  refused while the round is running or Polymarket has no result yet.
- **Why:** the owner: "i got stuck with this error" (live bot stopped at step 5 with a bet on a finished round; a
  stopped bot never settles, and Reset refused while the bet was open, so only Start could clear it).
- **How:** `Bot.settle_ended()`; `Bot.reset()` calls it before refusing.
- **Tests:** `test_engine`: Reset refused while the round runs / has no result, settles and resets once it has.

## [0.1.1] - 2026-11-10

### Fixed: a new Paper balance didn't change the balance
- **What:** saving a new **Paper balance** now changes the paper account at once (before, only **Reset paper**
  did). Profit, history, the ladder and an open bet stay: the balance moves by the difference (start $1,000, balance
  $1,041.93, new Paper balance $10,000 → $10,041.93). Also: an open page reloads itself when the app restarts (unless
  settings are unsaved), so it never shows an old version.
- **Why:** the owner: "when i change the paper balance, but not changed on main part's balance." (Their tab also still
  showed the page from before the update.)
- **How:** `PaperClient.set_start`; `Controller.put_settings` calls it when a paper bot's Paper balance changes;
  `loadBots` reloads the page when `started` changes.
- **Tests:** `test_web`: a new Paper balance shows in the balance and the start balance without Reset paper.

### Bot list and settings look like the hyperliquid bot
- **What:** the bot list is a column of cards: name, paper/LIVE badge, coin and round length, balance, profit, wins and
  losses, a step bar with the next bet (or the open bet), the status line and a Start/Stop button on each card. Above
  them: **Bots N**, **＋ New bot**, and totals (running, Paper P&L, Live P&L). Theme and version sit at the bottom.
  The settings column shows the bot's name, **Settings** with the network and market, and the settings as chips
  (Budget, Until earn, Profit/win or Start, Sizing, Survive, Ladder end, Price, Side, Entry). **Details ▾** opens the
  full form, grouped in coloured boxes (Market & money, Bet size, Loss recovery, Side, Entry); clicking a chip opens
  it at that setting. The save bar says *All changes saved* / *Unsaved changes*, with **Discard** and **Save
  settings**. Empty boxes say what empty means (whole balance, off, no limit, auto). Settings can still be changed
  while a bot runs.
- **Why:** the owner: "poly bot list and settins section should be looks like as hl."
- **How:** `index.html` (CSS, layout 290/440 px like hl, JS `renderBots`, `renderSettingsHead`, `GROUPS`,
  `setDetails`, `setDirty`); `Controller.summary(full=True)` adds balance, wins/losses, status, steps that fit, next
  bet cost and open bet cost for the cards (`/api/bots`); `_phase()` shared with `status()`.
- **Tests:** the existing web tests; checked in headless Chromium (light and dark) against a local copy.

### Steps to survive (like the hyperliquid bot)
- **What:** a new setting **Steps to survive** (in Bet size). Set e.g. 7 and the bot works out the first bet so the
  budget pays for 7 losses in a row, even if every bet costs the Max price: recover sizing sets the Profit per win,
  multiply sizing the Starting bet (that box is hidden while it's on). It's worked out again at each new streak
  (step 1) from the budget left, so bets grow after wins and shrink after a lost streak; mid-streak it stays. The
  ladder panel says what it chose ("Profit per win $3.17 for this streak"). If even 5-share bets can't cover the
  steps, the bot stops with a message. 0 or empty = off.
- **Why:** the owner: "add survive setting plz".
- **How:** `config.target_steps` (0–30; Max losses in a row may not be below it); `strategy.base_for_steps` (binary
  search on the Profit per win / Starting bet, in cents, using `build_ladder` at the Max price), `with_base`,
  `streak_sizing`; `Ladder.base` keeps the streak's value (reset on a win or restart); `Bot.sizing()` in `_enter`;
  the web ladder view and plan use the same sizing and return `auto`.
- **Tests:** `StepsToSurviveTest` (recover and multiply fit exactly the steps and not a cent more, budget too small,
  mid-streak keeps its value), engine `test_steps_to_survive` (value kept through losses, reset after a win, the win
  earns it) and `test_steps_to_survive_too_few_funds`, web plan returns `auto` and the Max-losses check.

## [0.1.0] - 2026-11-09

### Min price setting
- **What:** a new **Min price** setting next to Max price: the bot never bets on a side cheaper than it (e.g. 0.30).
  If the side is cheaper, the bot waits; after the entry window it skips the round. Default **0.45** (owner:
  "default is 0.45"), which also applies to existing bots that never saved one; 0 = no minimum.
- **Why:** the owner: "setting add min price as max price". Late in a round one side can cost 0.01–0.10 (a long
  shot that almost always loses), and the ladder then keeps losing.
- **How:** `min_price` field in config (validated: 0 ≤ min < max); `_enter` waits with "below the min price".
- **Tests:** `test_price_below_min_waits`; settings refuse a min price at or above the max price.

### History with fees, slippage, held time and balance (like the hyperliquid bot)
- **What:** the History table shows, per bet: paid-out time, round, side, step, **Best** (best ask when the bot
  decided), **Price** (average paid), shares, cost, **Slippage**, **Fee**, **Costs** (fee + slippage as % of the
  bet), result, **Payout**, **Net**, running total and **Balance**. (A Held column was shown briefly; the owner:
  "no need held time". The log still records `held_sec`.)
- **Why:** the owner: "plz add details about fees, held time, balance like hl bot".
- **How:** the bet records `best_price`; the trade log gets `placed`, `held_sec`, `best_price`, `slippage`;
  `upgrade_trade_log` rewrites an older log with the new header (old rows keep empty cells, shown as "–"). Live:
  when a fill shows no fee, the bot records Polymarket's documented fee for it (shares × 0.07 × p(1−p)) instead of
  $0.00.
- **Tests:** `TradeLogDetailsTest` (old log upgraded in place, held 365 s, slippage on a thin book, best price).

### Live bots: empty Budget = the whole balance
- **What / why:** a live bot no longer needs a Budget; empty (the default) uses the whole balance, like paper (owner:
  "if empty budget then use whole budget plz. other input works as well. empty is default"). The Start confirmation
  says "Budget: none: the WHOLE balance $x can be lost" in that case.
- **How:** dropped the live check in `BotConfig.validate`; help texts, README, the Start confirm in the page.
- **Tests:** `test_keys_and_switch_to_live` saves a live bot with an empty budget.

### Ladder table follows the current streak
- **What:** the Martingale ladder panel starts at the bot's current step, after what the streak already lost
  ("now at step 4: this streak lost $11.22 · 2 more bets fit in the $22.03 left"), with a line explaining that each bet
  is sized to win back the streak at the price paid. Before, it always showed a fresh ladder from step 1 at 0.50, so
  a real step 4 of $8.33 at 0.38 appeared as "$21.14, over budget" and confused the owner ("step 4 is not
  available?").
- **How:** `build_ladder(..., step, lost)`, `ladder_view(..., step, lost)` (adds `left`, `step`, `lost`); `/plan`
  takes `from_now` (the page sends it unless the settings form has unsaved changes, which still previews a fresh
  ladder).
- **Tests:** `MidStreakLadderTest` (step 4 after $11.22 lost: about $14 at 0.50, fits $22 left, wins about $2).
  Then (owner: "why hide step 1 and 2. plz fix this") the table shows the whole ladder again: the streak's steps
  already played come first, with the real bets from the trade log (cost, side, price, "❌ lost", running loss),
  then the current and next steps (`streak_bets`, `ladder_view(played=…)`; `StreakBetsTest`).

### Favicon
- **What / why:** the browser tab shows a yellow circle with a light green "P" (owner: first "favicon gold color
  green text color 'P'", then "yellow circle and light green text").
- **How:** an inline SVG data URI in `static/index.html` (yellow `#FACC15` circle, light green `#86EFAC` P), no
  extra file.

### No sign-in needed: guests
- **What:** poly.zodypay.com works right away without signing in: a first visit becomes a guest tied to that browser.
  "✉️ Keep my bots with email" (optional) signs in with a code and keeps the guest's bots; guests with live bots see a
  warning to do that first. Sign out shows only for email accounts. Hidden buttons stay hidden (`[hidden]` CSS).
  A visit through localhost (SSH/VS Code tunnel) can't show the visitor's country: the live refusal now says so
  and points to https://poly.zodypay.com (`_live_check` in hosted.py) instead of "couldn't tell which country".
- **Why:** the owner: "no need to signin to use this. just make use freely."
- **How:** `Store.new_guest` (email NULL, per-IP limit), users table migrated to a nullable email in place,
  `check_code(guest_id=…)`, `Hosted.sign_in` / `_move_bots` (bots moved into an existing account and restarted if
  they were running), `/api/me` creates the guest and its 365-day cookie. Page: no sign-in screen at start, a 401
  reloads once (then asks to allow cookies, no reload loop).
- **Tests:** guests make and start bots without email, the same guest on the next visit, adding an email keeps the
  bots, a guest's running bot moves into an existing account and keeps running, sign-out gives a fresh guest.
  Upgrade of the real users.db checked on a copy.

### Many users with email sign-in at poly.zodypay.com
- **What:** poly.zodypay.com is open to anyone: sign in with an email code, make up to 5 bots, paper trade, or
  trade live with your own Polymarket keys (stored encrypted). Live bots start only from countries where Polymarket
  allows trading. Admins see a Users list. The shared Caddy password is gone (each user signs in instead).
- **Why:** the owner asked "i want anyone can use this bot with their api key and private key. with email login."
- **How:** `hosted.py` (Store/Resend/UserApp/Hosted/handler, ported from ../hyperliquid), `geo.py` (DB-IP Lite
  country DB, Polymarket's restricted list from docs.polymarket.com/api-reference/geoblock as of 2026-11-09),
  `secretbox.py` (AES-GCM, `POLY_SECRET_KEY`; pycryptodome added to requirements). `live.save_keys` encrypts the
  private key and relayer key when the server key is set; `read_keys` decrypts. `web.make_handler` takes
  `app=None` and calls `self._app()` / `self._live_refusal()` so the hosted handler can plug in per-user apps and
  the location check (`X-Forwarded-For` from Caddy). `App(prefix=…)` gives bots unique log names (`u1/btc-5m`) and
  `max_bots`. Page: sign-in screen, email + Sign out, 👥 Users (admin), location warning on live bots, DB-IP
  credit. `python -m poly_bot hosted [--adopt EMAIL]`; the owner's 3 bots were adopted into
  onetodaysoft@proton.me and the live bot's keys re-saved encrypted. SDK HTTP logs quieted (httpx at WARNING).
- **Tests:** `tests/test_hosted.py`: country rules (JP allowed, US blocked, unknown blocked), codes (too soon,
  wrong, once only), sessions and disabling, two users with the same bot name kept apart, bot limit, admin list
  only for admins, live start refused from a blocked country while paper works, POST header/host checks, adopting
  the owner's bots. `tests/test_live.py`: keys encrypted at rest. Live checks: the site answers 401 to the API
  without a session and shows the sign-in page; hl.zodypay.com unaffected.

### Open from anywhere at poly.zodypay.com; bots start again with the app
- **What:** the app is reachable at https://poly.zodypay.com behind a password, and bots that were running when the
  app stopped start again by themselves when it comes back.
- **Why:** the owner asked "lets link to domain poly.zodypay.com"; a service restart (update, reboot) must not leave
  live bots stopped.
- **How:** `web --public-host DOMAIN` (repeatable) adds names to the Host check (`make_handler(app, public_hosts)`);
  it still binds 127.0.0.1. Caddy `poly.zodypay.com { basicauth { zody <bcrypt> } reverse_proxy localhost:8090 }`.
  `Controller.start` writes `profiles/<id>/.running`, a user Stop removes it, `App.stop_all` (shutdown) keeps it,
  `App.resume()` at startup starts bots whose marker exists and that aren't halted. `poly-bot.service` systemd unit.
- **Tests:** `tests/test_web.py`: a running bot resumes after an app restart while a stopped one stays stopped; the
  public host is answered and other hosts get 403. Checked live: no password → 401, with password → 200;
  hl.zodypay.com and zodypay.com unaffected.

### Live trading with real money
- **What:** a bot's Network can now be **live**: real bets with the pUSD in the owner's Polymarket account. A
  Polymarket keys panel (wallet address, private key, Relayer API key + signer address) with Save keys and Check
  connection (wallet, wallet type, pUSD balance, location, trading approvals); a red LIVE badge; Start asks for a
  real-money confirmation; won rounds are claimed automatically. `#bot=<id>` in the page URL opens that bot. An open page notices when the app was restarted (`started` in
  `/api/bots` and `/api/schema`, checked every 10 s) and reloads its settings form, so new settings and options
  (like Network = live) appear without pressing F5; an unsaved form is left alone.
  Check connection is **read-only**: it reports the betting approvals but never approves anything (the SDK's
  approval list includes Polymarket Perps' deposit contract `0xDCa4…17d2`, which betting doesn't need and which
  was the only "missing" one on the owner's account), and a busy public Polygon server (`polygon.drpc.org`,
  blocked by Cloudflare once on 2026-11-09) no longer fails the check.
  **Faster results:** `Polymarket.round()` asks the trading server (`clob.polymarket.com/markets/<condition_id>`,
  token `winner` flags) when Gamma has no result yet. On the first live bet (2026-11-09, round ended 13:50 UTC)
  the CLOB had the winner by 13:52:28 while Gamma still said open, so the bot had waited and a 5m bot skipped the
  next round. **Auto-paid wins:** Polymarket pays won shares out by itself; before claiming, the bot checks the
  won token's balance (`get_balance_allowance(asset_type="CONDITIONAL")`) and drops the claim when it's 0.
  Positions now store the bought token id.
- **Why:** the owner asked "how can i play with real fund?".
- **How:** `live.py` `LiveClient` on Polymarket's unified SDK `polymarket-client==0.17.0` (the old
  `py-clob-client` is superseded; collateral is now pUSD). Buy = FOK market order for the book-walked cost with
  `max_price`; shares and all-in cost read back from the order and the balance; nothing after a fill can stop the
  bet being recorded (else the bot would buy twice). Settle = `redeem_positions(condition_id)`; failed claims are
  queued in `live-account.json` and retried every minute by `housekeeping()` (called each tick). `check()` refuses
  when `polymarket.com/api/geoblock` says blocked, and runs `setup_trading_approvals()` when needed. `Round` now
  carries `condition_id`. Config: `network` = paper|live, live requires `budget_usd > 0`. web.py: `keys` and
  `check` actions, network switch only while stopped with no open bet (rebuilds the Bot with the right client),
  balance shown as "–" when it can't be read. The engine shows plain-word errors (ValueError) and retries.
  Start scripts now need 64-bit Python 3.11-3.14 (SDK minimum; ckzg wheels maximum).
- **Tests:** `tests/test_live.py` with a fake SDK: keys saved/masked/validated (.env mode 600), FOK order params,
  fill read-back with the fee in pUSD or in shares, unfilled/rejected orders, win claims and loss doesn't, failed
  claim retried after a restart, balance-read failure after a fill still records the bet, blocked location, engine
  with the live client. `tests/test_web.py`: live needs a budget, keys never in the status, switch back to paper.
  Also: the real SDK signs in and rejects a throwaway key/wallet pair with a clear message (no funds involved).
  **Not yet tested with real money.**

### 0.1.0: first version, paper trading
- **What:** a martingale bot for Polymarket's crypto Up or Down rounds with a local web page. Coins BTC, ETH, SOL,
  XRP, DOGE, BNB, HYPE; rounds 5m, 15m, 1h, 4h, daily (where Polymarket lists them). Many bots side by side, each
  with its own settings, ladder, history and log. Paper trading only: live order books and real results, virtual
  USDC.
- **Why:** the owner asked for a martingale on Polymarket's BTC 5m/15m price rounds, built like their hyperliquid
  bot, paper first, with every side mode and timeframe.
- **How:**
  - `markets.py` finds rounds through Gamma series (`<coin>-up-or-down-<tf>`) and events (`closed=false`, ordered by
    end date), reads results from `outcomePrices` once a market is closed, and order books from the CLOB.
  - `strategy.py`: fee = shares × 0.07 × p(1−p) (from each market's `feeSchedule`); **recover** sizing buys
    `(profit + streak losses) / (1 − p − fee)` shares so one win pays back the streak; **multiply** sizing is
    start × multiplier^(step−1); minimum 5 shares. Side modes: alternate, follow_last, opposite_last, cheaper,
    up_only, down_only, flip_on_loss, flip_on_win, random. `walk_asks` fills all or nothing up to the max price.
  - `engine.py` `Bot.tick`: one bet per round inside the entry window (auto = first third of the round), skip when
    the side costs more than Max price; after the round, poll the result every 10 s, pay out, move the ladder.
    Ladder end on budget or Max losses in a row (stop or reset), Until earn target. State in
    `state-paper.json`, so a restart resumes an open bet.
  - `paper.py` virtual account; `trades.py` CSV log; `web.py` + `static/index.html` the app (bot list | settings |
    page with tiles, current bet with the live market odds, ladder with a preview of unsaved settings, history, log).
- **Tests:** `tests/test_strategy.py` (fees vs Polymarket's docs example, sizing, ladder, side modes, book walking),
  `tests/test_engine.py` (loss-loss-win recovers, one bet per round, entry window, max price, restart with an open
  bet, budget/max-steps ladder end, Until earn), `tests/test_web.py` (create/plan/settings/delete, reload, POST
  header check). Also run live against Polymarket on paper (BTC 5m).
