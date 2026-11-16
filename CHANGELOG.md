# Changelog

Every change, newest first. See AGENTS.md for the rules (what / why / how / tests for each entry).

## [Unreleased]

## [1.4.5] - 2026-11-17

### A stopped bot's open bet shows its win chance too
- **What:** on the bot list, a bot stopped with a bet open now shows that bet's win chance and the green/red
  (winning/losing) card colour, like a running bot.
- **Why:** the owner stopped Manta with a bet open and its card showed no chance or colour.
- **How:** `Controller.bet_chance()`: a running bot's polled midpoint as before; a stopped bot's bet asks
  `Polymarket.midpoint()` itself, at most every 5 s and only until the round ends (then the last price is kept).
- **Tests:** the full suite.

## [1.4.4] - 2026-11-17

### 📈 Balance and Account portfolio charts: deposits vs trading
- **What:** click the **Budget balance** (or Balance) tile, or a live bot's **Account portfolio** tile, for a chart of
  its value over time (solid line) and the money put in less taken out (dashed line, a dot per deposit or
  withdrawal); the gap is what trading earned. Above: Started with, Put in since, Earned by trading, Now. Hover shows
  the date, value, money put in and earned. Below: every deposit / withdrawal (the account's link to Polygonscan).
- **Why:** the owner wanted to see how the balance and the account changed, and how much was deposited vs earned.
- **How:** Balance: `charts.budget_history()` from the bot's trade log: a balance jump a bet's pnl doesn't explain is
  money put in / taken out, named after the Budget / Network / Paper balance / Reset event logged in between.
  Account: `wallet_history.py`: every pUSD, USDC.e and USDC transfer of the wallet from Polygon
  (polygon-bor-rpc.publicnode.com, 10,000 blocks per request; drpc's free plan refuses log ranges), cached in
  data/wallets/<wallet>.json and topped up each minute; transactions in Polymarket's activity (/v2/activity:
  trades, payouts, rebates) are trading, any other transaction's net flow is a deposit or withdrawal (a coin swap
  nets to zero). Value = coins + open positions at cost (an Up/Down round's closes at its end); the last point is
  the real portfolio value. First scan runs in the background, the dialog shows progress. Endpoints
  /api/bots/<id>/balance_history and /account_history. Checked on the live wallet: deposits − withdrawals − trading
  matches the pUSD it holds.
- **Tests:** tests/test_charts.py.

### Bot list: an open bet's win chance
- **What:** a running bot's card shows its open bet's side, cost and win chance now, e.g. "📈 Up $8.53 · 78%"
  (green from 50%, red below); the list refreshes every 4 s while any bot has a bet open (else every 10 s).
- **Why:** to see at a glance how every open bet is doing.
- **How:** `Controller.summary()` "bet_chance" = the bot's live midpoint of the bet's token (`Bot.live["mid"]`,
  polled every 2-5 s while holding); `renderBots()`.
- **Tests:** the full suite.

### The page no longer jumps to the top while scrolled
- **What / why:** the 2 s redraw could reset the main page's scroll position.
- **How:** `render()` keeps #mainCol's (and the window's) scroll position across the redraw.

### 🔎 Rounds: Polymarket's own past rounds, for research
- **What:** a **🔎 Rounds** button next to 📋 History opens Polymarket's last ~7 days of BTC 5m or 15m rounds
  (not the bots' bets): start, round link, result, official Price To Beat / final price / final move, the Up price
  and BTC's move at 5m (the open), 4m, 3m, 2m, 90s, 60s, 30s left (15m: 15m, 10m, 5m, 3m, 2m, 90s, 60s, 30s), and the round's high and
  low. A question box: "the leader at X left, costing A–B, BTC moved $M+ its way" shows the rounds matched, how
  often the leader won, the break-even (average price), profit per $1 (no fees) and the longest losing run; the
  matched rounds can be listed alone. ⬇ CSV downloads every round.
- **Why:** the owner wanted to research Polymarket's round history itself.
- **How:** `poly_bot/rounds.py` `rounds_view()` (GET /api/rounds?coin=btc&tf=5m, cached 60 s) from the cached
  `History` (results, Up price about once a minute) and `CoinPaths` (BTC 60 s TWAP move, Binance), plus
  data/research/polymarket-twap-<tf>.json for the official prices. Page: `#roundsDlg`, `rdRender()`, `rdPick()`.
- **Tests:** tests/test_rounds_view.py.

### 📋 History: every bot in one table
- **What:** a **📋 History** button at the top of the bot list opens every bot's results side by side (bets,
  wins, losses, win %, net P&L, staked, fees, best and worst bet, longest losing run, balance, first and last bet;
  an All row) and every bet of every bot merged, newest first, with a Bot column. Filter by bot (or click its row)
  and paper/live; ⬇ CSV downloads the bets shown.
- **Why:** the owner wanted all the bots' results in one place.
- **How:** `App.all_history()` (GET /api/history) reads each bot's trade log; `bot_results()` totals one log; the
  newest `ALL_HISTORY_MAX` (3000) bets are sent. Page: `#allHistDlg`, `ahRender()`.
- **Tests:** tests/test_all_history.py.

### Move at bet no longer missing after a price-feed stall
- **What:** History's Move at bet was empty on about 1 bet in 5 for bots without a Move condition.
- **Why:** the Chainlink price socket sometimes stays open but stops sending; nothing reconnected it, so a bet bought
  then found no live price. Move bots never showed it only because they can't bet without a price.
- **How:** `PriceFeed._run` reconnects after `STALL_SEC` (8 s) without a watched coin's price (the subscribe backfill
  fills the gap); at payout `Bot._coin_at()` fills a missing coin_at_bet from the feed's kept prices (TWAP at the
  bet's time). Old rows stay empty.
- **Tests:** the full suite.

### Conditions: "A or (B and C)"
- **What:** a new setting **ANDs _ ORs** (Mode & side card, conditions mode). **and** (default, as before) = every
  AND condition and at least one OR: (A or B) and C. **or** = any one OR condition alone, or every AND condition
  together: A or (B and C), e.g. time ≤ 90 s OR (move $20+ AND price 0.60-0.85). Identical when every condition is
  AND or every one is OR. With **or**, a Time AND no longer holds back the start of the entry window (an OR alone can
  pass earlier). Price to stays the most the bot pays.
- **Why:** the owner: "A or B and C?" then "yes, add A or (B and C)".
- **How:** config.py `cond_join`; strategy.py `sides_allowed`, `entry_span`, `conditions_text`; index.html
  `condText()`, `condShort()`; the simulator uses the same functions.
- **Tests:** test_conditions `test_or_join_an_or_alone_or_all_ands`, `test_or_join_is_the_same_without_mixing`.

## [1.4.3] - 2026-11-16

### Current bet (waiting): numbers picked out, round linked
- **What:** while the bot waits, the Current bet message shows its numbers, prices and times bold and bright
  ("2m 00s", "0.95", "9:50AM-9:55AM", "$64,113.39"), and the round's title links to it on Polymarket.
- **Why:** the owner: "highlight numbers, digits, time. and add link for current round."
- **How:** engine.py `_enter` keeps `live["round"]` (slug, title, url); web.py status `round`; index.html
  `hlNums()`, `waitHtml()`, `.waitmsg b.hl`.
- **Tests:** existing suite (page change checked by hand).

### "Martingale ladder" panel renamed "Step ladder"
- **What / why:** the owner: "change "martingale ladder" to "step ladder"". Page heading and README.
- **Tests:** none (page only).

### Bet mode tile: compact conditions
- **What:** the Bet mode tile shows the conditions short, e.g. "≤120s · $1+ · 0.50–0.95" (· = AND, "or" between
  OR conditions) instead of "≤120s left AND move $1+ AND 0.50–0.95"; hovering shows the long form.
- **Why:** the owner: "simplify description about conditions. compact it. just for me to see it."
- **How:** index.html `condShort()`, used by `modeTile()`.
- **Tests:** none (page only).

## [1.4.2] - 2026-11-16

### Settings column folds away
- **What:** a « button next to Details folds the settings column to a thin "⚙️ Settings" strip, so the bot's page
  gets the width; clicking the strip opens it again. Remembered per browser.
- **Why:** the owner: "make setting part as withdrawal, to wider main part."
- **How:** index.html `setFolded()`, `body.set-folded` (grid column 34 px), `#setHide`, `#setShow`; localStorage
  `setFolded`.
- **Tests:** none (page only).

### Fix: fast results on only half the rounds
- **What:** Result = fast now checks the market on the bet's live order-book price from the round's last 15 s
  (read every 2 s then), not on Gamma's outcome price (now only the fallback, e.g. right after a restart).
- **Why:** the owner asked for the fast-check results: all 55 fast rounds matched the official result (median gap
  $0.16, max $2.24), but 65 rounds, some ending $100-200 away, still waited the full ~3 min. Watching the bot's own
  feed and checks live showed the TWAP was fine every time; Gamma's outcome price after the end is a cached value
  that can stay stale for minutes (Up 0.265 for 30+ s on a round our TWAP had ending +$70). The CLOB book itself is
  gone (404) once a round ends, so the last live price before the end is the market's verdict.
- **How:** engine.py `_hold` stores `live["mid_at"]` and polls the midpoint every 2 s in the last LAST_MID_SEC;
  `_fast_result` uses that midpoint when it's from the last LAST_MID_SEC, Gamma's `up_price` otherwise.
- **Tests:** test_fast_result `test_the_last_live_price_before_the_end_counts_when_gamma_is_stale`,
  `test_an_old_live_price_does_not_count`.

## [1.4.1] - 2026-11-16

### Result: fast (bet again ~30 s after a round instead of ~3 min)
- **What:** a new setting **Result** (Entry card): **fast** (default) takes a bet's result about 10-30 s after its
  round ends, once the bot's own 60 s TWAP (what Polymarket settles on) ended at least 0.008% of the price
  (BTC: ~$5) from the TWAP Price To Beat **and** the market agrees (the winner's price on Gamma 0.97+; the CLOB's book is gone (404) once a round ends). A closer round, or
  a gap in the price feed, waits for the official result as before. **official** waits for Polymarket's result as
  before. Polymarket's official result is still checked for every fast one; if it ever differs, the ladder (step,
  debt, wins/losses, profit), the balance (paper) or claim (live) and the History row are corrected, and a ⚠️ row
  says so. Live: a fast win's pUSD is only claimable after the official result, so a bet that needs more than the
  wallet's cash waits for it. **Alpaca** was set to official, so it keeps betting exactly as before; every other
  bot is fast.
- **Why:** the owner: "result soon" took ~3 min, so after each bet the bot could only bet in the last ~2 min of
  the next round (Alpaca: 85% of 542 bets with under 2.5 min left; results came a median 181 s after the end).
  Timed live: the winner's price was 0.9995 at +30 s, the CLOB's winner flag came at +162 s and flapped until
  +230 s, Gamma's finalPrice later still. Our TWAP vs the official prices (76 rounds recorded): median error $0.02,
  max $5.26; it picked the wrong side only on rounds that ended within ~$1, hence the margin.
- **How:** engine.py `_fast_result`, `pending` (saved in state), `_confirm_pending`, `_fix_result`, constants
  FAST_MARGIN, FAST_MARKET; `_buy` checks `client.cash()` (live). live.py `settle(confirmed=False)` keeps the claim
  with `wait` until Gamma/CLOB have the result, `cash()`, `fix_result()`; paper.py `fix_result()`. prices.py
  `last_second()`, KEEP_SEC 20 min (a 15m round's TWAP beat). trades.py column `result_by` (fast / official /
  corrected) and `fix_trade()`. simulate.py `settle_after()`: FAST_SETTLE_SEC 30 s unless the round ended close.
- **Tests:** tests/test_fast_result.py (fast default, official unchanged, close round, market disagrees, feed gap,
  both corrections, restart keeps pending, live claim waits, simulator timing).

### History: Bet at, Missed, ⚡
- **What:** **Bet at** = when the bet was bought (JST); **Left** = seconds left in its round then (e.g. 182s);
  Round shows the rounds missed since the previous bet's round in brackets, e.g. "Nov 15 8:55-9:00PM (2)", nothing when none; Result shows ⚡ for
  a fast result and "⚠ corrected" when the official result overturned it. **Fast check** (after Result, fast
  results only): ✓ when Polymarket's official result was the same, with how far our TWAP move was from the official
  one (e.g. "✓ $0.02"); ⏳ until the official prices are published; ✗ when it differed (corrected). The bot now logs
  its own move in the new trade-log column `fast_move`.
- **Why:** the owner: "plz show bet time for each round on history table", "show missed rounds count after Round
  column. if no missed, then it would be blank."
- **How:** web.py `with_gaps()` (from each round's slug and `placed`, over the whole log so the oldest row shown is
  right too), `betAtTd()`/`resultBy()` in index.html.
- **Tests:** test_fast_result `HistoryGapsTest`.

## [1.4.0] - 2026-11-16

### Move condition on the 60-second TWAP, like Polymarket settles
- **What:** the Move condition, the live "BTC now vs Price To Beat" and History's Move at bet now use Chainlink's
  60-second TWAP, the price Polymarket settles on: the round's Price To Beat = the TWAP at its start, BTC now = the
  TWAP now (spot only in the first minute after a restart, when the feed doesn't cover the window yet). The TWAP
  is the mean of the per-second Chainlink price from 62 to 3 seconds before the moment (matches Polymarket's
  eventMetadata to the cent on every gap-free round recorded). Move at bet is measured against the official TWAP
  Price To Beat once Polymarket has published it. The simulation's Move condition uses the same TWAP (Binance 1 s
  closes, with a minute before each round; coin paths are fetched again once). Research data: official settled
  prices for the last 30 days (`polymarket-twap-<tf>.json`); rounds-*.csv's real result is now `result`/`twap_*`,
  the old "official" (spot) columns are `spot_*`. Spot gives a different result in 11.2% of 5m rounds (6.1% 15m).
- **Why:** the owner: "yes, do both" (official TWAP prices for the research, the Move condition on the TWAP), and
  "480 bet - why bet up even price moved down?": Move at bet showed -$7.27 against the bot's spot Price To Beat
  (64,634.92) while BTC was +$14.05 above the real TWAP one (64,613.60), so UP led at 0.77.
- **How:** prices `TWAP_FROM`/`TWAP_TO`, `PriceFeed.twap()`, `twap_to_beat()`, `settle_prices()`; engine uses
  settle_prices in `_enter_conditions` and `_buy`, the TWAP for `_coin_end` and the holding display, and watches the
  coin in every `_enter`; history `twap_path()`, CoinPaths `"a"`, `PRE_SEC`; simulate reads `"a"`;
  `moveTd(t, t.coin_at_bet, twap_beat || beat)`; data/research/tools/fetch_polymarket_twap.py, build_rounds.py.
- **Tests:** test_prices_recorder `TwapTest` (window, gap -> spot fallback); test_conditions `test_twap_path`.

### History: Move at bet and Move at end
- **What:** two columns after Bet in each bot's History. **Move at bet** = the coin's live Chainlink (spot) price when
  the bet was bought minus the spot price at the round's start (what the bot saw). **Move at end** = Polymarket's
  official final price minus its official Price To Beat, the prices the round was settled on. Green = the bet's
  side, red = against. Older bets get Move at end from Polymarket (looked up in the background like Volume) and
  Move at bet from the research data (`data/research/tools/backfill_history.py`).
- **Found while building it:** Polymarket settles these rounds on Chainlink's **BTC/USD TWAP** stream (60-second
  average; the market's rules say so), published per round in the Gamma event's `eventMetadata`
  (`priceToBeat`, `finalPrice`) once it has resolved. The live feed, the crypto-price endpoint and the bot's Price
  To Beat are **spot** prices; on 30 days of rounds the spot start/end gave a different result in ~1 round in 10
  (e.g. 12:55 ET on Nov 15: spot 64,520.59 -> 64,529.60 "up", TWAP 64,510.94 -> 64,505.31, paid Down). So the
  Move condition and `data/research` "official_*" columns are spot, not what settles.
- **Why:** the owner: "history table should show price move from start and end after "bet" column."
- **How:** trades.py columns `beat`, `coin_at_bet`, `coin_end` (spot); `Bot._buy` stores the feed's price and Price
  To Beat after the order; `_settle` logs them with `PriceFeed.close_price()`; `Polymarket.settled_prices()`;
  `volume.RoundPrices` (data/round_prices.json, like RoundVolumes); `Controller.with_volume` adds `twap_beat`,
  `twap_end`; `moveTd()` in index.html.
- **Tests:** test_conditions `test_history_gets_the_coins_moves`; test_web
  `test_history_shows_the_official_settled_prices`.

### Two Bet modes: side and conditions (hit, time and move removed)
- **What:** Bet mode is now **side** or **conditions** (the mixed mode below, renamed). hit, time and move are gone:
  every bot and preset saved with them is converted, when it's loaded, to the conditions that bet the same:
  hit = Price AND from the Hit price to Pay at most; time = Time left AND + Price AND from Leader at least to Pay at
  most (no limits: Time alone, the leader); move = Move AND + Price AND up to Pay at most (Price off with no limit).
  Alpaca (live, hit 0.75, no limit) became Price AND 0.75 to empty: the same bets. Pay at most, Hit price, Move ($),
  Time left (s) and Leader at least are no longer settings. Waiting messages now say e.g. "UP costs 0.90, above
  Price to 0.80" when the side the other conditions want is too dear.
- **Why:** the owner: "make mode 2 types - "side" and "conditions". remove hit, time, move."
- **How:** `config.upgrade()` (in `BotConfig`, `load_config`, `save_settings`, `App.presets`); `strategy`
  `sides_allowed` (was mixed_sides), `leader_side`/`hit_side` removed, `entry_window` without the hit case;
  engine `_enter_conditions` (was _enter_mixed), `_enter_move` and the hit/time branches removed; simulate
  `_entry_conditions`, the hit/time/move replays removed (DEFAULT_GAP keeps their measured gaps for
  `default_gap`); web `_own_bets` also matches which conditions are on.
- **Tests:** test_conditions (was test_mixed) `UpgradeTest` (each old mode's conversion, an old config file upgraded
  on load and save); test_engine's old hit/time/move tests now run old settings through the conversion and check
  the same bets; test_web uses conditions; test_strategy's hit/time/move tests removed.

### Simulation: ladder ends highlighted
- **What:** the "Runs with a ladder end" tile became **🪜 Ladder ends per run** (the average, big and red, on a red
  tile) with the typical run, the worst run, run 1 and the share of runs that had one; History (run 1) says how many
  ladder ends it had (the red rows).
- **Why:** the owner: "highlight ladder end counts on simulation."
- **How:** simulate returns `ladder_ends_median`, `ladder_ends_max`, `ladder_ends_run1`; `.simstats > div.hl`.

### Mixed Bet mode: AND/OR conditions
- **What:** a new Bet mode **mixed** (renamed conditions, above): the owner's own combination of three conditions, each Off, **AND** (must be
  true) or **OR** (one of the ORs is enough): **Time left** at most N s, **Move** at least $N from the Price To Beat
  (the coin's live Chainlink price right now, not the highest it reached), **Price** of the side within from-to.
  The bot bets a side when every AND condition is true for it and, if any condition is OR, at least one OR is
  too. Move points to the way the coin moved, Price to the side(s) in range, Time to both; when both sides pass,
  it bets the **leader** (the dearer side). No Side mode in mixed mode (the owner: "remove side mode"); the Side
  mode list also gained **leader** for side mode. No Pay at most in mixed mode: **Price to** is
  also the order's limit (the most it pays, so a fill can't slip above it), even for a bet from an OR Move; Price
  off = no limit; empty Price from/to = 0.50/0.95 (the owner first asked for "blank
  means any"; a simulation then showed 42% of bets going to the 0.02-0.10 losing side, winning ~1%, whenever the
  favourite was above Price to, and the owner chose "blank price from means favourite only", then "0.5 to 0.95": empty Price from =
  0.50, empty Price to = 0.95; both default to empty) (the owner: "pay at most and price to are same, aren't they? ... remove pay at most plz"). A new
  conditions in the Mode & side card, one row each (Time left + Move, then Price; each condition's values only
  while it's on; the owner: "these should be merged"), a
  Conditions chip and Bet mode tile ("≤90s left AND move $10+ AND 0.75–0.95"). side/hit/time/move are unchanged,
  so running bots (Alpaca is live in hit mode) behave exactly as before.
- **Why:** the owner: "better to config settings as like add conditions, not like current mode - hit, move, time.
  integrate these types into one - mixed ... time left 90s and move at least $10 and hit 0.75~0.95" and "every
  condition can be set as OR or AND mode."
- **How:** config `cond_time`/`cond_time_sec`, `cond_move`/`cond_move_usd`, `cond_price`/`cond_price_min`/
  `cond_price_max`, `BotConfig.conditions()`, `mixed_mode`; strategy `mixed_sides()` (the one rule, used by the bot
  and the simulation), `conditions_text()`, `entry_span`/`buy_cap`/`plan_price` for mixed, `choose_side` "leader";
  `leader_of()`; engine `Bot._enter_mixed()`. Simulation: `simulate._entry_mixed()` checks every 2 s on the 1-minute token prices
  and the coin's per-second position; `history.CoinPaths` now also keeps `"c"` (close − open of every candle) and
  re-fetches rounds cached without it; `default_gap()` (mixed: hit's gap when Price is an AND, since the bot sees
  the price before buying; else the biggest of its conditions' modes), each round's entries worked out once for
  all runs (`RealFeed(shared=...)`, also faster for hit/time/move),
  `needs_coin()`, `ladder_free()`.
- **Tests:** tests/test_mixed.py (sides for AND, OR, AND+OR, time alone; window, cap, plan price; validation;
  engine waits then bets, Side mode picks, Pay at most; simulation entry on the coin's position, price-only).

### Chainlink price recorder and multi-coin live prices
- **What:** the bot service now saves every Chainlink price Polymarket streams for BTC, ETH, SOL and XRP, one
  second per row, in `data/research/chainlink/<coin>/<UTC day>.csv` (finished days gzipped), for research. Fixed:
  the live price feed (move mode) only streamed live prices for one coin per connection, so move bots on two
  coins at once would have left one without live prices.
- **Why:** the owner: "save chainlink feed from now" and "fix the move mode feed too". Found while building the
  recorder: a symbol-filtered subscription sends its backfill, then live prices for only one symbol.
- **How:** `prices.Recorder` (own socket, unfiltered subscription `ALL`, each second written once and in order,
  resumes after a restart from today's file); started in `hosted.serve_hosted`. `PriceFeed` subscribes `ALL` plus
  the filtered ones (for their backfill) in one message and keeps only watched symbols.
- **Tests:** tests/test_prices_recorder.py (order, dedupe, restart, day files and gzip, coin filter, feed keeps
  watched coins). Checked live: BTC, ETH and SOL watched together all update every second.

### Recover sizing: a short win no longer writes off the rest of the streak
- **What:** with recover sizing a win now pays back only what it really earned over the Profit per win; a fill
  dearer than planned (slippage) leaves the shortfall owed and the next bet is sized for it. Before, the planned
  share was taken off what's owed whatever the win paid, so Alpaca (live) lost about $47 for good on 2026-11-16
  (bet 445: sized at 0.81, filled at 0.891, +$65.17 instead of +$117.74). Alpaca's state was given back the $47.25.
- **Why:** the owner: "why not recover loss after 2 streak loss? what's wrong?"
- **How:** `Ladder.record(..., base_profit)`; the bet stores `base_profit` (the Profit per win it was sized for);
  multiply sizing keeps the old behaviour.
- **Tests:** test_engine `test_short_win_keeps_the_shortfall_owed` (the real numbers); `test_recover_over_two_wins`
  allows the cent from rounding shares up.

### Setting presets
- **What:** **📚 Presets ▾** next to Save settings: "Save these settings as a preset…" stores the form as it is now
  (saved or not) under a name; picking a preset loads it into the form (you check it and press Save settings), ✕
  deletes one. New bot's Copy from lists presets too: a new paper bot with those settings. A preset is the strategy
  only: not the bot's name, live/paper, Budget, Paper balance or keys, so loading one into a live bot never changes
  how much money it may use. Kept per user (up to 100). The save bar says "✓ Saved" / "Unsaved".
  The menu also has **Load settings from a bot**: any of your other bots' settings into the form the same way (not
  its name, live/paper, money or keys), and New bot's Copy from says how to save one while there are none (the
  owner: "how can i input saved setting preset? ... i can't find ... can't see about existing bot as well").
- **Why:** the owner: "plz make save setting feature." (chose named presets)
- **How:** `App.presets()`, `save_preset()`, `delete_preset()`, `create(preset=...)`; `GET/POST /api/presets`,
  `POST /api/presets/delete`; `profiles/.presets.json`; `presetMenu()` in index.html.
- **Tests:** test_web `test_setting_presets`.

### Settings cards: 4 or fewer settings fill one row
- **What:** a settings card showing 4 settings or fewer puts them in one full-width row (Bet size 3 or 4, time
  mode's Mode & side 4, Loss recovery 2); a lone setting takes half the width. A card with more fills 2 even rows:
  Market & money is Bot name, Network, Paper balance (Budget when live) / Coin, Round length, Until earn (Bot name
  no longer takes 2 columns; the owner: "market setting should have only 2 rows").
- **Why:** the owner: "bet size can be 3 or 4, if needed. full width plz." and "for time mode, 3 or 4 columns at a
  row, if it need."
- **How:** `.card-body.fit` with `--cols`, set in `showFields()` from the shown fields; `WIDE` is empty.

### Steps to survive: side mode only
- **What:** Steps to survive is shown and used in side mode only. In hit, time and move mode the bot buys at
  0.75-0.99 a share, so each ladder step costs ~4-20x the last: fitting several steps into the budget leaves a tiny
  first bet, or none. There the bet is sized by Profit per win (recover) or Starting bet (multiply) as set. A value
  saved before is kept and counts again in side mode (Hawk, a new hit-mode paper bot with no bets yet, had 5).
- **Why:** the owner: "i think 'steps to survive' need for only side mode."
- **How:** `BotConfig.survive_steps` (side only) used by `streak_sizing`, the engine, simulate and the ladder view;
  `showFields()` / the summary in index.html.
- **Tests:** test_engine `test_steps_to_survive_is_side_mode_only`.

### Paper bots: the Paper balance is the budget (no Budget setting)
- **What:** Budget is shown and used for live bots only. A paper bot has its own paper account, so its Paper balance
  is all it can lose; its settings summary shows Paper balance instead of Budget. A Budget saved before is kept and
  counts again if the bot is switched to live (no paper bot had one set).
- **Why:** the owner: "for paper bot, need budget input? ... just paper balance enough."
- **How:** `BotConfig.budget` (live only) used by `Bot.budget_left()`, `Bot.own_balance()`, `Controller._budget()`
  and the bot list; `budgetOf()` and `showFields()` in index.html.
- **Tests:** test_engine budget tests use the Paper balance; test_web checks a paper bot ignores a saved Budget and
  keeps it for live.

### Compact settings: 3 settings a row
- **What:** the settings cards fit 3 settings a row (4+ on a wider column) instead of 2, with tighter spacing and
  smaller labels (a long one wraps to 2 lines, the boxes stay lined up). Bot name takes 2 columns; a setting's help floats
  under it while you edit it instead of pushing the row down.
- **Why:** the owner: "plz compact settings cards, it doesn't need to only 2 input at a row."
- **Bet size** fits 4 settings a row (the owner: "let bet size setting has 4 columns at a row").
- **How:** `.card-body` grid `minmax(104px, 1fr)`, `.card.tone-target` 4 columns, each field a CSS subgrid (label / box / note rows, so boxes line up), `.field` CSS, a focusin handler in `buildForm()`.
- **Tests:** none (page only).

### Duplicate a bot: settings and Polymarket keys copied
- **What:** a **⧉ Duplicate** button on each bot, and a **Copy from** choice in New bot. The new bot gets that bot's
  settings (network included) and its Polymarket keys, so a new live bot needs no keys typed again; coin and round
  length start the same and can be changed; it gets its own name, a fresh ladder, paper account and empty history,
  and starts stopped. Only your own bots can be copied.
- **Why:** the owner: "i need to enter api key and private key for new live bot every times. plz let me make as
  duplicate with existing one."
- **How:** `App.create(copy_from=...)` copies the settings and the bot's `.env` (encrypted as saved, mode 600);
  `newBotDlg()` in index.html.
- **Tests:** test_web `test_duplicate_copies_settings_and_keys`.

### Wins / Losses counts every bet; since Reset in small
- **What:** the ✅ Wins / ❌ Losses tile shows every bet in History (Resets don't clear it), with "since Reset W / L"
  under it when that differs. The bot list keeps the since-Reset count.
- **Why:** the owner: "wins/losses count card show about total bets regardless about reset. show small info about
  after reset then."
- **How:** status `all_wins` / `all_losses` (counted from the trade log); the tile in index.html.
- **Tests:** test_web `test_history_has_every_bet` checks the totals after a Reset.

### Win chance now: only its own box is coloured
- **What:** while a bet runs, the green/red background and stripe now cover only the "Win chance now" box, not the
  Up/Down bar beside it.
- **Why:** the owner: "background color reflection for only show about first div. not about status bar's."
- **How:** `.winnow .wn` in index.html (`winNow()`).
- **Tests:** none (page only).

### A bot without a Budget counts its open bet in its Balance too
- **What:** the 💰 Balance of a bot with no Budget was the account's cash only, so it dropped by the bet's cost
  while a bet ran. It's now cash + this bot's open bet at what it's worth now (moves with the price, checked every
  5 s), with "cash $X · in bet $Y" under it; also in the bot list. Live no-Budget bots: the tile is "Balance" (was
  "pUSD balance"). Budget bots already did this.
- **Why:** the owner: "why balance card not reflect current's bet's balance?" (time mode: $160.30 shown = cash
  after a $10.75 bet worth $13.25).
- **How:** `Bot.own_balance()`; the balance tile in index.html.
- **Tests:** test_web `test_budget_bot_shows_its_own_balance` checks a no-budget bot with an open bet.

### "reset" never stops the bot at step 1: it waits for a round it can pay
- **What:** with When the ladder ends = reset, a step 1 bet that costs more than the bot has left (or a budget too
  small for Steps to survive) no longer stops the bot. It skips that round and keeps checking ("Can't pay this
  round: ... Waiting for a round where the step 1 bet fits"), and bets as soon as one fits (in time/hit mode the
  cost follows the price, so a cheaper round comes). A bot already stopped this way starts again with Start, no
  Reset needed. With stop, nothing changes.
- **Why:** the owner: "this bot stopped at step1 due to lack of balance. then plz wait till can cover with its
  balance for next round. don't stop as far as setting is 'reset'." (Salmon, paper $616, time mode: step 1 cost
  $764 at that round's price.)
- **How:** `Bot.waits_at_step1()`; `_ladder_end()` waits instead of halting; `_buy()` (Steps to survive) and
  `on_start()`; the help of `FIELDS["on_ladder_end"]`.
- **Tests:** test_engine `test_reset_mode_waits_at_step1_when_short`, `test_reset_mode_start_works_after_an_old_step1_stop`;
  two stop-mode tests now set stop themselves.

### History shows each round's volume
- **What:** a **Volume** column after Round: everything traded in that round (USDC), Polymarket's final number. It
  shows – until the round has been over for 10 minutes and the number is fetched (a moment after the page loads,
  for old bets). The CSV download has a `volume` column too.
- **Why:** the owner: "can you show volume of each round on history table?"
- **Fix (same day):** a few rounds showed $0.00: Gamma leaves their volume out (`volume: null`). Those now come from
  Polymarket's data API (`v2/live-volume`, the same number to the cent where both have it), and $0 is never saved
  (asked again later); saved zeros are dropped on start.
- **Fix 2 (same day):** the newest rounds showed far too little (bet 501: $1.96 instead of $39.2k): Gamma's volume
  still lags 20+ minutes after a round ends. The data API (`v2/live-volume`) is now the main source (complete
  after the round; matches Gamma to the cent once Gamma catches up) and Gamma only the fallback. The saved
  volumes were cleared and fetched again.
- **How:** `Polymarket.final_volume()`; `volume.RoundVolumes` (the volume per round slug, looked up in a background thread, kept in
  `data/round_volumes.json` and shared by every bot); `Polymarket.round_volumes`; `Controller.with_volume()` adds
  it to status and history trades; the trade log file is unchanged.
- **Tests:** test_web `test_history_shows_each_rounds_volume`.

## [1.3.0] - 2026-11-14

### Recover over (wins) shows a recommended value
- **What:** the setting's label has a *, and under the box "* Recommended: 1–2" (when Pay at most is set at 0.85 or
  less, hit/time/move mode) or "3 or more" (otherwise), with a ✓ when the value is in range and the reason on hover.
- **Why:** the owner: "set * to recover over setting" (as a recommendation), after the real-history tests.
- **How:** `RECOMMEND` / `showRecs` in index.html.
- **Tests:** none (page only).

### Budget balance counts the open bet at its price now
- **What:** a Budget bot's balance is Budget + profit since Reset + what the open bet is worth now (its shares at
  the side's price, checked every 5 s) - what it cost, so it moves with the price while a bet runs. The Account
  portfolio never shows less than this bot's own open bet (Polymarket's value can lag a fresh bet).
- **Why:** the owner: "budget balance should show included current betting balance ... it changes in real-time".
- **How:** `Bot.bet_value()`, `Bot.own_balance()`, `Controller.portfolio()`.
- **Tests:** test_web `test_budget_bot_shows_its_own_balance` moves the price.

### Account tile shows the portfolio, not just cash
- **What:** the 🏦 tile is now "Account portfolio", like Polymarket's Portfolio: cash plus what every open bet on the
  account (every bot's) and unclaimed wins are worth now, with "cash $X · in bets $Y" under it. Paper bots show their
  paper cash plus this bot's open bet at its price now.
- **Why:** the owner: "account balance card show portfolio balance, not cash balance."
- **How:** `LiveClient.portfolio()` reads Polymarket's data API `/v2/value` for the wallet (cached 15 s);
  `Controller.portfolio()`; status `portfolio`.
- **Tests:** none new (network); checked the endpoint answers for the live wallet.

### Simulation's table looks like History
- **What:** run 1's table has History's columns and look: newest first; # in green (win) / red (loss); Paid out
  (JST); Round (short, linked to Polymarket); Bet; Step; Best (the replay's price); Price (with the price gap);
  Shares; Cost; Slippage (the price gap); Fee; Costs; Result; Payout; Net; Total; Balance. Ladder ends are red rows
  like History's.
- **Why:** the owner: "simulation table should be same as history table in all aspects. such as green and red
  number."
- **How:** `RealFeed` adds the round's slug, end, paid-out time and pre-gap price to each bet; `simLogHtml`.
- **Tests:** existing simulate tests (the page layout isn't unit tested).

### Settings order; "When the ladder ends" defaults to reset
- **What:** the settings panels are now Market & money, Mode & side, Bet size, Loss recovery, Entry. A new bot's
  When the ladder ends is **reset** (accept the loss, back to step 1) instead of stop; existing bots keep theirs.
- **Why:** the owner: "setting parts order - market, mode, bet size, loss... plz. "When the ladder ends" default
  value is reset."
- **How:** `GROUPS` in index.html; `FIELDS["on_ladder_end"]` default.
- **Tests:** test_engine `test_budget_ends_ladder` sets stop itself.

### History: show every bet, and download it as CSV
- **What:** History still shows the newest 100 bets by default (the page refreshes every 2 s); a **Show all N**
  button loads every bet (and every log row), and **⬇ Download CSV** saves every bet with all the trade log's
  columns (placed time, held seconds, best price, slippage...) for a spreadsheet.
- **Why:** the owner: "why don't keep all history log? plz keep all so that i can analyize that."
- **How:** `Controller.history()`, `GET /api/bots/<id>/history`; the page builds the CSV itself.
- **Tests:** test_web `test_history_has_every_bet`.

### Simulation: real past rounds only, at the prices the bots really pay
- **What:** Fair coin (and its Win chance) is gone: the simulation always replays the real past rounds. Every
  bet's price is now raised by the **price gap**: how much more your own bots really paid than the replay says,
  measured on the same rounds (your bots' paper and live bets in the same coin, timeframe and Bet mode, placed
  within 90 s of where the replay buys). Until 30 such bets exist it uses a default per mode. The summary says
  which gap was used and on how many bets.
- **Why:** the owner: "i want everything with this have to be based on real history and reality" (Fair coin said
  38% of runs end in profit, the replay 99%). Checked on the same rounds the bots really bet: the replay paid
  4-5 cents less than the bots in hit and time mode and 13.7 cents less in move mode (side mode matched), because
  1-minute prices can't show how fast they jump. With the gap the hit mode bot's settings go from 99% of runs in
  profit to about 61%, and move mode from 97% to about 16%.
- **How:** `simulate.price_gap()`, `with_gap()`, `DEFAULT_GAP`; `Controller._own_bets()`, `web.placed_in_round()`.
- **Tests:** test_web `test_simulate_adds_the_price_gap_measured_on_own_bets`.

## [1.2.0] - 2026-11-14

### History: a log row comes after the bet that caused it
- **What:** when a log row (e.g. "Ladder ended") and a bet have the same second, the log row is shown as the newer.
- **Why:** the owner: "reset log should be after loss log."
- **How:** `historyTable`'s sort breaks a same-time tie in favour of the event.
- **Tests:** none (a page-only sort).

### Account balance tile
- **What:** on a bot with a Budget, a "🏦 Account balance" tile after the Budget balance shows the whole account.
- **Why:** the owner: "show total balance for account after budget card."
- **How:** status `wallet`.
- **Tests:** test_web `test_budget_bot_shows_its_own_balance` checks `wallet`.

### A bot with a Budget shows its own balance
- **What:** when Budget is set, the bot's Balance (tile "💰 Budget balance", bot card, History's Balance column) is
  its own money: Budget + Profit since Reset - the open bet. Several bots on one Polymarket account each show only
  theirs, not the account's total. Without a Budget it's the account's balance as before. If the account holds less
  than the bot's balance, the tile says "the account has only $X". Reset puts it back to the Budget. Right after it,
  a "🏦 Account balance" tile shows the whole account (only on bots with a Budget).
- **Why:** the owner: "when set budget, then show balance as the budget. don't show total balance for each bot.
  each bot play with their budget." / "show total balance for account after budget card."
- **How:** `Bot.own_balance()`; `Controller.wallet()` is the account (still caps what a bet may cost); status
  has `wallet` too.
- **Tests:** test_web `test_budget_bot_shows_its_own_balance`.

### History logs when the ladder ends
- **What:** when the next bet doesn't fit, History gets a red 🪜 row: "Ladder ended at step N: <why> The bot
  stopped (streak lost $X); press Reset", or with When the ladder ends = reset, "... Reset by itself: the streak's
  $X loss is accepted, back to step 1."
- **Why:** the owner: "when the ladder ends, log on history table." / "when reset".
- **How:** `Bot._ladder_end` writes a `ladder_end` event (once per stop) to events.jsonl.
- **Tests:** test_engine ladder-end stop and reset-mode tests check the event.

### Reset has two options: Reset, and Reset only ladder
- **What:** the ↺ Reset button opens a menu (like Stop's). **↺ Reset** is as before: a fresh ladder at step 1 with
  Profit since Reset and Wins / Losses back to 0. **🪜 Reset only ladder** goes back to step 1 (the streak's loss is
  accepted, not won back) and clears a "Ladder ended" stop, but keeps Profit since Reset and Wins / Losses. Both are
  logged in History.
- **Why:** the owner: "reset has 2 options "reset" and "reset only ladder"(profit count and wins count remain)".
- **How:** `Bot.reset(keep_counts=True)` uses `Ladder.restart()`; new `POST /api/bots/<id>/reset_ladder`.
- **Tests:** test_web `test_reset_only_ladder_keeps_profit_and_counts`.

### The site needs an email sign-in and the admin's approval; no guests
- **What:** poly.zodypay.com shows only the sign-in screen until you sign in with your email (a 6-digit code, no
  password). There is no "Continue without email" and no guest account any more; a browser that had a guest cookie
  is signed out. A **new email's account waits for the admin's approval**: after signing in it sees "waiting for
  the admin's approval" (with Check again / Sign out) and can't use anything. The admins (ADMIN_EMAILS,
  onetodaysoft@proton.me) get an email "Approve <email>?" on that first sign-in, and the 👥 Users button shows
  "(1 waiting)"; the Users list puts waiting accounts first with **✅ Approve** / **Reject**, and the others with
  Disable / Enable. Accounts that already had an email were approved by the upgrade; admins never wait.
- **Why:** the owner: "lock this site with email login. not allowed guest mode." / "it should be allowed by admin -
  onetodaysoft@proton.me".
- **How:** `hosted.py`: `users.approved` column (migration approves existing email accounts); `Hosted.is_approved`
  (approved or admin); `Handler.user` is None for guests and unapproved accounts, `/api/me` answers 401
  `{"signin"}` or 403 `{"pending", "email"}`; `send_approval_request` (Resend, best effort, on the first sign-in);
  `POST /api/admin/users/<id>/approve`; `admin_users` lists email accounts with `approved` / `admin`. Unapproved
  and guest accounts' bots aren't loaded at start. `index.html`: sign-in screen can't be closed, a pending step,
  `usersHtml` with Approve / Reject / Disable / Enable, the waiting count refreshed every minute for admins.
- **Tests:** `test_hosted.test_email_sign_in_is_required`, `test_new_accounts_wait_for_the_admin` (admin emailed
  once, 403 until approved, only an admin can approve, works right after), `test_an_old_guest_cookie_is_signed_out`.

### Ladder shows the open bet as it was placed
- **What:** while a bet is open, its row in the Martingale ladder (marked "open") shows the real bet: price, shares,
  cost and side, the same as Current bet. The steps after it are priced at that bet's real price (or the settings'
  price when dearer), and the note says so. Before, the row showed a forecast at the settings' price (e.g. $57.43
  at 0.75 while the real step 2 bet was $93.51 at 0.83).
- **Why:** the owner: "why current bet info and ladder table info different?"
- **How:** `Controller._ladder`: with an open bet at the current step, `ladder_view` from the next step with the
  bet's cost added to the streak's loss, and the bet inserted as an `open` row; `fits` counts it. `index.html`
  renders the open row and the new note.
- **Tests:** `test_web.test_ladder_shows_the_open_bet_as_placed`.

### Move mode: bet when the coin moves $X from the Price To Beat
- **What:** a fourth Bet mode, **move**: when the coin's live price (Chainlink, what the rounds settle on) is
  **Move ($)** (default 50) above the round's **Price To Beat**, the bot buys Up; that far below, Down. Any time
  in the round (unless Entry window is set), within **Pay at most**. The status line shows "BTC $71,494.64 is
  $45.44 below the Price To Beat $71,540.08; betting at $50", and while a move bet runs the Current bet panel shows
  the Price To Beat and the coin's price now (▲/▼). The simulation replays it too.
  Research (BTC 5m, Nov 6-13, 2,016 rounds): the side the coin moved toward won 73% ($25), 82% ($50), 88% ($75),
  91% ($100) of the time; against the token's price at the next recorded minute the edge was +2 to +3 points
  (+10 points against the price in between: how fast Polymarket reprices decides it).
- **Why:** the owner: "current price differ from beat price. if set 50 then when reach $50 differ from start then
  bet side." / "no new mode about price diff reach i asked. plz add a new mode".
- **How:** `poly_bot/prices.py` `PriceFeed`: one socket to Polymarket's real-time data (`crypto_prices_chainlink`,
  e.g. `btc/usd`, about one price a second, plus the last minute on subscribing; the filter must be written
  without spaces or only the backfill comes), keeping 15 minutes per coin; a round's Price To Beat is the price at
  its start second, else Polymarket's crypto-price endpoint (rate-limited: asked at most once a minute per round).
  `Polymarket.coin_prices`; `config` `entry_mode` += "move", `move_usd`; `Bot._enter_move`; the bet keeps
  `price_to_beat`. Simulation: `history.CoinPaths` downloads the coin's 1-second Binance candles for the past
  rounds (only the seconds that set a new high/low: ~0.8 MB a week), `simulate.entry_for` move rule at the token's
  next recorded price (pessimistic).
- **Tests:** `test_engine` move mode waits until $50, bets Down below / Up above, respects Pay at most;
  `test_strategy.MoveModeSimTest`.

## [1.1.0] - 2026-11-13

### Simulation replays real past rounds for every Bet mode
- **What:** the simulation's default **Market** is now **Past rounds (real)**: it replays Polymarket's last 2,016
  rounds of the bot's coin and timeframe through the Bet mode's own entry rule, so each bet has the price the bot
  would really have paid (time mode: 0.56 to 0.99, not 0.75 flat) and wins only if that round really went that
  way. The old model stays as **Fair coin**. The result says which rounds were replayed, the entry prices, the win
  rate and the break-even rate; run 1's table shows each bet's round (JST), seconds in, and side.
  Example (BTC 5m, Nov 6-13, $1 profit per win, $1,000): time 2 min: bets averaged 0.826, won 86.0% vs 83.5%
  needed; hit 0.75: 0.825, 87.1% vs 83.5%; hit 0.75 with Pay at most 0.80: 0.780, 80.1% vs 79.2% (only slow
  climbers fill there); side random 0.45-0.55: 0.500, 49.0% vs 51.7%.
- **Why:** the owner: "how all price same 0.75 for time mode? sometimes 0.52 or 0.98. plz simulate as real for
  all modes."
- **How:** `poly_bot/history.py` `History`: closed rounds from Gamma (50 per page, newest first), each round's Up
  price path from the CLOB prices-history (1-minute points for 5m-1h), cached in `data/history/<COIN>-<tf>.json`,
  loaded in the background (about 40 s the first time; the page shows progress and asks again every 3 s), topped up
  when 30 min stale. `simulate.py`: `entry_for` (per mode: side within Min/Max in the Entry window; hit = first
  side at the Hit price, filled `HIT_FILL` = 60% of the way to the next recorded price, calibrated on the paper
  bots' real fills, 0.825 simulated vs 0.827 real; time = the leader at Time left within the limits), `SPREAD`
  1 cent over the mid, `SETTLE_SEC` 170 s before the next bet, `RealFeed` stitching random 12 h stretches;
  the ladder sizes each bet at its own price. `Controller.simulate` `market` = real / fair.
- **Tests:** `test_web.test_simulate_real_replays_past_rounds` (fake history: time mode buys the real leader, wins
  as the rounds went; loading progress is returned), the fair-coin tests now pass `market: fair`.

### History shows Resets and settings changes
- **What:** the bot's History table lists **Reset**, **Reset paper** and every **settings change** as rows between
  the bets, at the time they happened: e.g. "↺ Reset: fresh ladder at step 1, profit counted from 0 (was step 3,
  profit since Reset +$12.34)", "⚙️ Settings: Bet mode side → hit · Hit price 0.75 → 0.7".
- **Why:** the owner: "if reset or change setting, then plz log on history table."
- **How:** `trades.append_event` / `read_events`: one JSON line per event in the bot's `events.jsonl` (time in
  JST like the bets; both networks). `Controller.reset` / `paper_reset` / `put_settings` (with `settings_diff`, only
  settings that really changed) write them; `status()["events"]` (those since the oldest bet shown);
  `index.html` `historyTable` merges them by time. The trade log stays bets only, so stats are unchanged.
- **Tests:** `test_web.test_reset_and_settings_logged_for_history`.

### Simulation: the losing-streak tile says whose streak it is
- **What:** the tile read "Longest losing streak: 8 in a row" while run 1's table showed 10 losses in a row. 8 was
  the typical (median) run's longest streak across all runs; run 1 is just one run. The tile is now titled
  "Longest losing streak (typical run)" and also gives run 1's (the table's) and the worst run's.
- **Why:** the owner: "why longest losing streak 8. even first 10 losing streak. what's up?"
- **How:** `simulate` returns `longest_streak_run1`; `index.html` tile text. Losses in a row count across ladder
  ends (a ladder end doesn't break a streak of losses).
- **Tests:** existing simulation tests.

### Scrolled tables stay where you scrolled them
- **What:** the simulation's History (run 1) table (and the History and ladder tables when they scroll) jumped
  back to the top every 1-2 s; they now keep their scroll position.
- **Why:** the owner: "if i scroll down then after 1-2s scroll to top again. so i can't see table."
- **How:** `render()` (which redraws the page every 2 s) saves each `.panel .scroll` box's scrollTop/scrollLeft
  before the redraw and puts them back after it (after `renderLadder`, which fills the ladder table).
- **Tests:** none (display only).

## [1.0.0] - 2026-11-13

### Bot list: drag to reorder, pin to the top
- **What:** like the hyperliquid bot: drag a bot card to reorder the list (a line shows where it lands), or select
  one and press Alt+↑/↓. 📌 on a card (shown on hover, always when on) pins it to the top; dropping a bot onto a
  pinned one pins it, onto an unpinned one unpins it. The order is saved by the app, so every browser sees it.
- **Why:** the owner: "bot list should be dragable and pinned as like hyperliquid".
- **How:** `App.order` / `set_order` / `pin` and a sorted `App.list` (pinned first, then the saved order; new bots
  at the end, oldest first), stored in `profiles/.order.json` (per user on the hosted site); POST `/api/order` and
  `/api/bots/<id>/pin`. `index.html`: `wireBotOrder` (drag, drop, Alt+arrows), `.bi-pin`; the 10 s list refresh
  doesn't redraw mid-drag.
- **Tests:** `test_web.test_order_and_pins`.

### Stop right away / Stop after a win
- **What:** Stop (on the bot's page and in the bot list) opens a menu like bcauto's: **Stop right away** (as
  before), **Stop after a win** (keep betting and stop at the next winning bet, back at step 1), and while waiting
  **Don't wait for a win**. The button then says "Stop after win…" and the status line "🏁 stops after the next win".
  The bot then shows as Stopped (not halted); Start runs it again without a Reset.
- **Why:** the owner: "stop button has option "stop right away" and "stop after a win" as like bcauto stop."
- **How:** `Bot.stop_after_win` (saved in the state; cleared by Start/Reset), checked in `Bot._settle` after a win
  -> halt "after_win"; `Controller.stop_after_win` / `cancel_after_win` (POST `/api/bots/<id>/stop_after_win`,
  `/cancel_after_win`); the bot summary reports `stop_after_win` and doesn't count "after_win" as halted;
  `index.html` `stopMenu`.
- **Tests:** `test_engine.test_stop_after_win`, `test_web.test_stop_after_win_endpoints`.

### Simulation: history table
- **What:** the simulation's run 1 is listed as a **History (run 1)** table, open by default, with the same
  columns as the bot's History: #, Step, Price, Shares, Cost, Fee, Result, Payout, Net, Total, Budget left, plus a
  row at each ladder end or when the budget runs out.
- **Why:** the owner: "simulation part should have history table for that."
- **How:** `simulate.py` log rows carry price, shares, fee and payout; `index.html` `simLogHtml`.
- **Tests:** existing simulation tests.

### Bet mode tile replaces Budget left
- **What:** the tiles at the top of a bot's page show its **Bet mode** ("Hit 0.75 · first side to reach it",
  "Time · 2 min left, the leader", "Side · random · 0.45–0.55") instead of Budget left.
- **Why:** the owner: "show mode type on header part. remove budget left card instead".
- **How:** `index.html` `modeTile(settings)`.
- **Tests:** none (display only).

### Simulation panel
- **What:** a **🎲 Simulation** panel on each bot's page (after History), like the hyperliquid bot's: Bets per run, Runs, Budget
  (empty = budget left) and Win chance (empty = the price, a fair market) -> average / median / worst 5% / best 5%,
  runs in profit / with a ladder end / out of budget, longest losing streak, biggest drop, days at the bot's pace,
  a histogram, 5 sample runs and run 1 bet by bet. It shows the win chance needed to break even after fees.
- **Why:** the owner: "add simulate mode as like hyperliquid simulation."
- **How:** `poly_bot/simulate.py` (pure; the bot's own `shares_for`/`cost_of`/`streak_sizing`, cached per step);
  `Controller.simulate` (POST `/api/bots/<id>/simulate`, form settings unsaved, price = `plan_price`, pace from the
  trade log); `index.html` panel kept across the page's 2 s redraws.
- **Tests:** `test_strategy.SimulateTest` (fair market loses the fee, max steps cap, sure win/loss, limits),
  `test_web.test_simulate_uses_form_settings_and_budget`.

### Current bet: real-time round volume and a highlighted win chance
- **What:** the Current bet panel shows the round's traded **volume** in real time ($, number of trades, shares),
  and, while the bet runs, a big **Win chance now** for the bet's side: green when ahead (over 50%), red when
  behind, with both sides' percentages and the bar below. A round first seen mid-way (e.g. after an app restart)
  says "since <time>".
- **Why:** the owner: "show volume of current bet. and plz highlight percentage while bet is running." / "round
  volume is incorrect. it should update in real-time." Gamma's `volume` lags by minutes ($2-$4 shown for rounds
  that had traded over $1,000) and the data API's `/v2/live-volume` differs from server to server.
- **How:** `poly_bot/volume.py` `VolumeFeed`: one WebSocket to Polymarket's CLOB market channel for the whole app
  (`Polymarket.volumes`), adding up every `last_trade_price` (size x price) per round; bots subscribe the current
  and the next round from `Bot._enter`, so counting starts before a round does (new rounds are added to the open
  socket with `operation: subscribe`; a reconnect marks running rounds partial). `Bot._hold` reads it each tick;
  without the `websockets` package it falls back to Gamma's number, labelled delayed. `index.html` `winNow`.
- **Tests:** `test_markets.VolumeFeedTest` (trades counted per round, other events and tokens ignored); checked
  live against a raw socket: both counted $123 over the same 20 s.

### Time mode: bet on the leader with N seconds left
- **What:** Bet mode gets a third option, **time**: when **Time left** is reached (default 120 s = 2 minutes before
  the end), the bot buys the side that is leading (the dearer one). **Leader at least** (empty = any lead, e.g.
  0.60) and **Pay at most** (shared with hit mode; empty = no limit) bound the price; outside them the bot keeps
  checking until 5 s before the end. Side mode, Min/Max price and Entry window are hidden in time mode.
- **Why:** the owner: "add another mode plz. so side mode, hit mode, time mode" / "if set 2min, then bet when 2mins
  left." (after the comeback stats: a leader at 0.60-0.70 with ~2 min left won ~74% vs ~67% needed.)
- **How:** `config.FIELDS` `entry_mode` += "time", `time_left_sec`, `time_min_price`; `strategy.entry_span`,
  `leader_side`, `buy_cap`/`plan_price` (time with no limit: Leader at least, else 0.75); `Bot._enter` time branch;
  `index.html` shows each mode's fields and chips.
- **Tests:** `test_engine` time mode waits until 2 min left then buys the leader, and respects Leader at least / Pay
  at most / the 5 s cut-off; `test_strategy.TimeModeTest`.

### Ladder table priced by the bot's settings, no "At price" box
- **What:** the Martingale ladder's "At price" box is gone. The table prices every bet at the most the settings let
  the bot pay (side mode: Max price; hit mode: Pay at most, or the Hit price when it's empty) and says so above the
  table. The Step tile and the bot list use the same price, so they always agree with the table.
- **Why:** the owner: "why need input on ladder table?" The box only changed the table, which confused it with the
  tile (e.g. "Step 1 of 4" at 0.75 next to a table showing 2 steps at a typed 0.90).
- **How:** `strategy.plan_price` (used by `web.py` for the status ladder and the bot cards); `index.html` drops the
  input, previews only unsaved settings.
- **Tests:** `test_strategy.HitModeTest` checks plan_price for side, hit and hit-without-limit.

### Hit mode: buy the first side that reaches a price
- **What:** new setting **Bet mode** (Mode & side card): `side` (as before) or `hit`. In hit mode the bot watches
  both sides through the whole round and buys whichever first reaches the **Hit price** (default 0.75), paying at
  most **Pay at most** (empty by default = no limit, up to 0.99). Side mode, First side and Min/Max price are hidden and unused; Entry window
  0 = the whole round (minus 5 s). Sizing, ladder, budget, Until earn work as before.
- **Why:** the owner: "when it hits over 75 cents, it rarely goes back against". A 7-day backtest (2,016 BTC 5m
  rounds, 1-minute prices) found the side first at 0.70-0.80 won 76.9% vs ~76% needed after fees: about break-even,
  so start on paper.
- **How:** `config.FIELDS` `entry_mode`, `hit_price`, `hit_max_price` (validated: hit <= max <= 0.99, or 0 = no limit);
  `strategy.hit_side` / `buy_cap` / `plan_price`, `entry_window(..., hit)`; `Bot._enter` hit branch, the buying
  half split into `Bot._buy(cap)`; `web.py` ladder at `plan_price`; `index.html` shows/hides fields and chips.
- **Tests:** `test_engine` hit mode buys the first side to hit (side mode ignored, after the normal window) and
  waits when the price jumped past Pay at most; `test_strategy.HitModeTest`.

### Live profit is after fees
- **What:** a live bot's "Profit since Reset" ran ahead of the real pUSD balance: after a Reset at $153.36 it showed
  +$19.31 with $165.51 in the wallet (really +$12.15). The $7.16 gap was exactly the 29 bets' fees. Every live
  profit (tile, history Net/Total, Until earn, the streak to win back) is now after fees.
- **Why:** the owner: "this round start from 150 but why profit 19.31 even only 165? what's wrong?" / "show after
  fee plz. only real profit plz."
- **How:** `LiveClient.buy` took the all-in cost from the pUSD balance before/after the order, but the read right
  after the order still showed the old balance, so the cost fell back to the order amount *without* the fee, which
  Polymarket takes in pUSD on top. The fee was also guessed from "shares asked minus shares got", which is just
  rounding. Now the fee is Polymarket's documented taker fee (or the balance's, when the balance shows it), and
  when the balance hasn't caught up the cost is the order amount plus that fee. Checked against the wallet: the
  documented fee matched the balance change on all 236 live bets that had no deposit/withdrawal in between.
  The owner's live bot's history (`trades-live.csv`: cost, fee, pnl, total_pnl) and state (profit, streak lost)
  were corrected the same way once; backups saved next to them as `*.bak-<time>`.
- **Tests:** `test_live.test_fee_counted_when_balance_lags` (fake SDK whose balance read lags the order).

### Step bar shows only the steps the budget reaches
- **What:** when the ladder ends because the next step costs more than the budget left (e.g. 8 lost, step 9 costs
  $945 with $218 left), the Step tile said "Step 9 of 9" with a 9th red pip. It now says "Step 8 of 8 · step 9
  doesn't fit" with 8 lost pips; the bot list shows "step 8/8" and an 8-pip bar.
- **Why:** the owner: "this took only step 8 but show to step 9. show bar about available steps."
- **How:** `web.py` bot card `fits` = steps the budget reaches (lost + still affordable), no longer clamped up to the
  current step; `index.html` `stepPips` draws one pip per reachable step and the Step tile reuses it.
- **Tests:** existing suite passes.

## [0.1.6] - 2026-11-12

### Step tile counts the whole ladder
- **What:** the Step tile said "Step 3 of 6" while the ladder showed steps 1-8 fitting; it now says "Step 3 of 8",
  like the bot list, and its bar has 8 segments.
- **Why:** the owner: "why step 3 of 6? step 8 available, isn't it?"
- **How:** `ladder.fits` counts only the bets still ahead (the ladder header's "6 more bets fit"); `render()` now
  adds the steps already lost (`fits + step - 1`), as `web.py` already did for the bot list.
- **Tests:** the existing suite.

## [0.1.5] - 2026-11-12

### Recover over N wins (split recovery)
- **What:** a new recover-sizing setting, **Recover over (wins)** (1–5, default 1 = as before). With 2, each win
  pays back half of what the streak still owes plus the Profit per win, and the rest is carried to the next bets;
  with 3, a third. A loss adds to the debt and spreads it over N wins again; the debt is cleared after N wins.
  Bets grow ~1.5× (2 wins) or ~1.35× (3 wins) per loss instead of ~2×: at 0.50 and $10 per win, 10 losses in a row
  cost ~$1,445 or ~$608 instead of ~$14,613, so Steps to survive fits a much bigger Profit per win. The ladder
  shows what each win leaves owed ("+$10.00 · $16.50 left"), the step tile "N wins to clear", the Sizing chip
  "recover · 2 wins", and the log what's still to win back after a win.
- **Why:** the owner asked about loss-recovery strategies; recommended split recovery; owner: "ok, plz".
- **How:** `Ladder.wins_left` + `Ladder.record(..., recover_wins)` carry the debt; `strategy.recover_part` is what a
  win pays back (used by `shares_for`, `shares_for_book`, `build_ladder`; `Rung.left_after_win`); `Sizing` carries
  `recover_wins`; `streak_sizing(..., carried)` keeps a streak's Steps-to-survive base while its debt is still being
  won back. Page: field in Bet size (recover only), chip, tile, ladder note and column.
- **Tests:** `test_strategy.RecoverOverWinsTest` (debt carried and re-spread, 1 = old behaviour, slower growth,
  bigger Steps-to-survive base, book sizing); `test_engine`: two wins clear a streak with both profits kept. The
  page checked in headless Chromium with a bot $33 down at step 3.

## [0.1.4] - 2026-11-11

### Until earn counts since Reset, not since Start
- **What:** **Until earn** now stops the bot when **Profit since Reset** reaches it. Stop/Start and app restarts
  no longer restart the count; Reset does. Once reached, Start is refused until you Reset or raise Until earn. The
  "Since Start" tile is now **Until earn**: what's still to go, "✅ reached", or "off".
- **Why:** the owner: "until earn should calculate after reset." (Their live bot stopped at "Earned $103.22 since
  Start" while Profit since Reset was $77.24: an app restart had started the count again from a lower point.)
- **How:** `Bot.target_reached` / `Bot.until_earn_left` use `ladder.pnl`; `start_pnl` is gone (old state files
  still load); status sends `until_earn_left` instead of `earned_since_start`.
- **Tests:** `test_engine`: the count survives Start; Start refused after the target until it's raised or Reset.

### Fixed: a win after a long streak didn't pay the streak back
- **What:** recover sizing now works out the shares from the real order book instead of the best price alone. A
  big bet climbs the asks (slippage); the bot now buys enough shares that the win still pays back the whole streak
  plus the Profit per win. If the book up to Max price is too thin for that, the bot waits and retries in the
  entry window ("Not enough … for a win to pay back the streak") instead of betting too little.
- **Why:** the owner: "after long streak profit goes minus." A paper bot's step-9 bet had to win back $3,843.78 +
  $10; sized at the best ask 0.46 (7,374.10 shares) it filled at 0.508 on average ($353.31 slippage), so the win
  paid +$3,500.00 and the bot went back to step 1 at −$238.17.
- **How:** `strategy.shares_for_book` (bisects the share count on `walk_asks`); `Bot._enter` fetches the side's
  book, sizes with it, and checks the budget against the walked cost. multiply sizing is unchanged (no target).
  The ladder table and Steps to survive still assume no slippage.
- **Tests:** `test_strategy.BookSizingTest`: the old sizing falls short on that book, the new one pays the streak
  back with no extra shares, a too-thin book gives None, a deep book sizes as before.

## [0.1.3] - 2026-11-11

### Price ± (cents): a slider for Min and Max price
- **What:** a **Price ± (cents)** slider (1–25¢) in Settings → Entry sets Min and Max price evenly around 0.50:
  ±5¢ = 0.45–0.55, ±10¢ = 0.40–0.60; next to it the range it sets. Min/Max price stay and can still be set one by
  one; the slider then says "custom · 0.42–0.60".
- **Why:** the owner: "set min and max price by input 0.5 or 1 then 4.5-5.5 or 4-6 like this"; asked, they chose
  cents and keeping both boxes, linked; then "it should be range slider".
- **How:** page-only (`spreadFromPrices` / `pricesFromSpread` in `static/index.html`); nothing new is saved, only
  `min_price` / `max_price`. The form only sends named inputs.
- **Tests:** checked in headless Chromium: 5 → 0.45–0.55, sliding to 10 → 0.40–0.60, Min 0.42 → custom; the
  slider isn't sent with the settings.

### Large amounts shown with k and M, like the hyperliquid bot
- **What:** dollar amounts and share counts are exact up to 9,999.99 (now with thousands commas: $4,944.85) and
  short from 10,000: $12.3k, $1.44M. Hovering a short amount shows the exact one. Confirm dialogs stay exact.
- **Why:** the owner: "about large numbers, use k and m as like hl".
- **How:** `money` / `moneyFull` / `shortNum` / `sharesTxt` in `static/index.html` (same rule as hyperliquid's `usdS`).
- **Tests:** checked by hand (formatter outputs; the page script parses).

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
