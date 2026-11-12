# Poly Martingale Bot

A martingale bot for **Polymarket's crypto "Up or Down" rounds** with a local web page. Pick a coin and a round
length, set your profit per win, click **Start**, and it bets every round:

```
round 1: bet Up    $2.69  -> lost
round 2: bet Down  $5.56  -> lost    (the bet grows so one win pays back the streak)
round 3: bet Up   $11.53  -> won: pays $22.28 = +$10.75, so the streak earned +$2.50. Back to step 1.
```

Each round asks: *will the price at the end be at or above the price at the start?* Up wins if yes, Down if no.
Polymarket settles it from Chainlink's price feed 1 to 5 minutes after the round ends. A winning share pays $1,
a losing one $0.

| Rounds | BTC | ETH | SOL | XRP | DOGE | BNB | HYPE |
|---|---|---|---|---|---|---|---|
| 5m, 15m, 4h | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 1h | ✓ | ✓ | – | ✓ | ✓ | ✓ | ✓ |
| daily | ✓ | ✓ | – | ✓ | – | ✓ | ✓ |

Two modes per bot: **paper** (the real rounds, live order books and real results, with virtual money and no wallet)
and **live** (real bets with the pUSD in your Polymarket account, see [Live trading](#live-trading-real-money)).
Paper-trade a setup first.

---

## 1. Quick start

```bash
cd ~/poly
./start.sh            # Windows: double-click start.bat
```

The first run sets up Python by itself (needs 64-bit Python 3.11-3.14 on the computer; it installs Polymarket's
SDK for live trading). Your browser opens **http://127.0.0.1:8090**, then:

1. **＋ New bot**: pick the coin and the round length.
2. **Settings** (middle column): choose the sizing, side mode and max price, then **Save settings**.
3. Check the **Martingale ladder**: how many losses in a row your budget pays for.
4. Click **▶ Start**.

Leave the terminal window open while the bots run. Closing it (or Ctrl+C) stops them. A bet still open when you
stop is paid out the next time you start that bot.

## Live trading (real money)

1. **Check your location.** Polymarket blocks trading from some countries. The bot asks Polymarket
   (`polymarket.com/api/geoblock`) at Check connection and at Start, and refuses to trade live where it is blocked.
2. **Fund your Polymarket account** (deposit USDC; Polymarket holds it as pUSD, 1:1 with USDC).
3. **Get four values from polymarket.com:**

   | Value | Where |
   |---|---|
   | Wallet address | profile menu → copy the address |
   | Private key | the key that owns that wallet. Email/Google login: Settings → Export Private Key. MetaMask/Rabby login: that account's private key |
   | Relayer API key + its Signer Address | Settings → API Keys → Relayer API Keys → create |

   The Relayer key lets the bot claim winnings without paying gas.
4. In the bot: Settings → **Network = live**, set a **Budget** (e.g. 50; empty = the whole balance), Save. The
   **Polymarket keys** panel appears: paste the four values, **Save keys**, then **Check connection**. It shows
   the wallet, its type, your pUSD balance, and sets up Polymarket's trading approvals if needed (one gasless
   transaction).
5. **Start** asks you to confirm real money, with the budget and the first bet's cost.

How live bets work:
- Each bet is a **fill-or-kill market order** for the cost worked out from the live book, never above Max price:
  it fills completely or not at all. The shares received and the pUSD spent (fee included) are read back from
  Polymarket, so the history shows the real numbers.
- After a **won** round the bot **claims** the winnings (redeems the shares for pUSD). If Polymarket's chain
  hasn't caught up yet, the claim is kept and retried every minute; the page shows "$x to claim" meanwhile, and
  it counts as won right away. Lost shares are worth $0 and are left alone.
- A bot switches between paper and live only while stopped with no open bet. Paper and live keep separate
  ladders and histories (`state-live.json`, `trades-live.csv`; claims in `live-account.json`).
- Keys are saved in the bot's folder (`profiles/<id>/.env`, readable only by you) and never shown on the page
  again. **Anyone with the private key can move your funds**: use this only on a computer you trust, and consider a
  separate Polymarket account holding just the bot's money.

## 2. How a round goes

1. A round starts (e.g. 12:05:00 for a 5-minute round). Within the **entry window** (default: the first third of
   the round) the bot picks a side and looks at its order book.
2. If the side's best price is at or under **Max price**, it buys the number of shares this step needs, walking the
   book cheapest first. It's all or nothing: if not enough shares are for sale up to Max price, it tries again a
   few seconds later.
3. One bet per round. After the round ends the bot waits for Polymarket's result (1 to 5 minutes), pays it out,
   and moves the ladder: a **win** goes back to step 1, a **loss** goes one step up.
4. Because the result arrives after the next round has started, a 5-minute bot usually bets in the next round a
   minute or more late, or skips a round when the result takes long.

## 3. The maths, with numbers

Polymarket charges the buyer a fee of `shares × 0.07 × price × (1 − price)`: **1.75% of the bet at a price of
0.50**, less at other prices. So at 0.50 a share costs about $0.5175 and a win pays $1, i.e. **a win returns about
1.93× the bet**, not 2×.

**recover sizing** (default) sizes each bet so one win pays back the whole streak plus **Profit per win**, at the
price actually paid. With $2.50 profit at a price of 0.50 (each bet is about 2.07× the one before):

| Step | Bet costs | A win earns | Lost if it loses |
|---|---|---|---|
| 1 | $2.69 | +$2.50 | −$2.69 |
| 2 | $5.56 | +$2.50 | −$8.25 |
| 3 | $11.53 | +$2.50 | −$19.78 |
| 4 | $23.90 | +$2.50 | −$43.68 |
| 5 | $49.53 | +$2.50 | −$93.21 |
| 6 | $102.65 | +$2.50 | −$195.86 |
| 7 | $212.75 | +$2.50 | −$408.61 |
| 8 | $440.93 | +$2.50 | −$849.54 |

So $1,000 pays for 8 losses in a row. Up/Down at 0.50 is close to a coin flip: 8 losses in a row happen about once
every 256 streaks, and then the $2.50 wins of all those streaks are gone at once. A martingale doesn't change the
odds; it trades many small wins for a rare big loss. Paper-trade first.

Higher entry prices win more often but pay less, so the ladder grows faster: at a price of 0.60 the bets go $4.03,
$10.51, $27.43, $71.58, $186.79: step 5 costs almost 4× as much as at 0.50. **Max price** keeps the bot from buying expensive sides.

**multiply sizing** is the classic martingale: the first bet costs **Starting bet**, each loss multiplies the next
one by **Multiplier**. Because a win returns only ~1.93× at 0.50, a multiplier of 2 does *not* recover a streak;
use 2.1 or more. recover sizing works it out for you.

### Simulation: what your settings do in the long run

A few days of betting can't show what a martingale does over months: the rare long losing streaks decide it. The
**🎲 Simulation** panel on a bot's page plays the settings in the form (saved or not) through many runs at once:
**Bets per run** and **Runs** (up to 1 million bets in total) and **Budget** (empty = the bot's budget left).

**Market: Past rounds (real)**, the default, replays Polymarket's real rounds of the bot's coin and timeframe (the
last 2,016 rounds, about 7 days of 5m, with the Up price minute by minute; downloaded once in about a minute, kept in
`data/history/`, then topped up) through the Bet mode's own entry rule:

- **side:** the side the Side mode picks, at the first moment in the Entry window its price is within Min/Max price;
- **hit:** the first side to reach the Hit price. Prices jump between the bot's checks, so the fill is placed 60%
  of the way from the Hit price to the next recorded price (the paper bots' real hit fills averaged 0.83 for 0.75;
  the simulation gives 0.825); above Pay at most the bot waits;
- **time:** the leader when Time left is reached, within Leader at least / Pay at most (else the first later
  moment it is, until 5 s before the end).

So every bet has the price the bot would really have paid (plus a 1-cent spread), wins only if that round really went
that way, and the next bet waits until the previous one is paid out (about 3 minutes after its round). Runs stitch
random half-day stretches of the history together, so each sees real streaks but runs differ. A week is a small
sample: past results don't promise future ones.

**Market: Fair coin** prices every bet at the ladder table's price and lets it win as often as that price says (or
the **Win chance** you enter): a market the bot can't predict, where the fee makes every setting lose a little.

Sizing, Polymarket's fee, the 5-share minimum, Recover over N wins, Steps to survive, Max losses in a row and the
budget are the bot's own. A ladder end starts again at step 1 with the money left; a run stops when even step 1 no
longer fits. It shows the average, typical, worst-5% and best-5% result, how many runs ended in profit / had a
ladder end / used up the budget, the longest losing streak, the win rate against the break-even rate, a histogram,
5 sample runs and run 1 bet by bet (with the past round and side of each bet).

## 4. Settings reference

| Setting | Default | What it does |
|---|---|---|
| `bot_name` | coin + timeframe | Shown in the bot list and log. |
| `network` | `paper` | `paper` = virtual money. `live` = real pUSD in your Polymarket account (needs keys). |
| `paper_balance_usd` | 1000 | Virtual USDC; applies on **Reset paper** and on the first run. |
| `coin` | BTC | BTC, ETH, SOL, XRP, DOGE, BNB, HYPE. |
| `timeframe` | 5m | 5m, 15m, 1h, 4h or daily (see the table at the top). |
| `budget_usd` | 0 (= all) | How much the bot may lose. Profits are added, losses taken off. When the next bet costs more than what's left, the ladder ends. |
| `stop_profit_usd` | 0 (= off) | **Until earn**: stop once Profit since Reset reaches this (Reset starts counting again). |
| `sizing` | recover | `recover` = one win pays back the streak + Profit per win. `multiply` = Starting bet × Multiplier^(step−1). |
| `base_profit_usd` | 2.5 | recover: what each winning streak earns after fees. |
| `start_usd` | 3 | multiply: the first bet's cost, fee included. |
| `multiplier` | 2.2 | multiply: the next bet after a loss is this many times bigger. |
| `recover_wins` | 1 | **Recover over (wins)**, recover sizing: how many wins pay back a losing streak. 1 = one win pays back everything (bets ~2× per loss). 2 = each win pays back half of what is still owed plus the Profit per win (~1.5× per loss), 3 = a third (~1.35×). The rest is carried to the next bets; a loss spreads the debt over N wins again. |
| `target_steps` | 0 (= off) | Steps to survive: works out the first bet (Profit per win, or Starting bet with multiply) so the budget pays for this many losses in a row even at the Max price. Recalculated at each new streak from the budget left. |
| `max_steps` | 0 (= budget decides) | End the ladder after this many losses in a row. |
| `on_ladder_end` | stop | `stop` = halt (press Reset). `reset` = accept the loss and start again at step 1. |
| `entry_mode` | side | `side` = pick a side by `side_mode` and buy it within Min/Max price in the entry window. `hit` = watch both sides and buy whichever first reaches `hit_price`, any time in the round. `time` = when `time_left_sec` are left, buy the leading (dearer) side. In hit and time mode side mode and Min/Max price are unused. |
| `time_left_sec` | 120 | time mode: bet when this many seconds are left; if the leader's price isn't within the limits then, keep checking until 5 s before the end. |
| `time_min_price` | 0 (any) | time mode: bet only if the leader costs at least this, e.g. 0.60. |
| `hit_price` | 0.75 | hit mode: buy the first side whose best ask reaches this. |
| `hit_max_price` | 0 (no limit) | hit and time mode: never pay more per share, e.g. 0.80; if a side jumped past it, wait for a price back in between. Empty/0 = no limit (up to 0.99). |
| `side_mode` | alternate | `alternate` Up/Down/Up…, `follow_last` = what the last finished round was, `opposite_last` = against it, `cheaper` = the lower-priced side, `up_only`, `down_only`, `flip_on_loss`, `flip_on_win`, `random`. |
| `first_side` | up | The first side for alternate/flip modes, and the fallback when the last result isn't known. |
| `min_price` | 0.45 | Never bet on a side cheaper than this (stops long shots). If it is cheaper, the bot waits; after the entry window it skips the round. 0 = no minimum. |
| `max_price` | 0.55 | Never pay more per share. If the side costs more, the bot waits; after the entry window it skips the round. |
| `entry_window_sec` | 0 (= auto) | Bet only this many seconds into a round. Auto = the first third (5m: 100 s, 15m: 5 min, 1h: 20 min, 4h: 80 min, daily: 8 h). |
| `poll_interval_sec` | 2 | How often the bot checks. |

Minimum bet: Polymarket's minimum order is 5 shares (about $2.60 at a price of 0.50), so smaller bets are rounded
up to 5 shares.

## 5. Many bots, files

Each bot is a folder `profiles/<id>/` with `config.json`, `state-paper.json` (the ladder and any open bet),
`trades-paper.csv` (every settled bet), `paper-account.json` and `bot.log`. Run as many bots as you like side by
side, e.g. BTC 5m with `cheaper` next to ETH 15m with `follow_last`. Times in the log and history are Japan time
(JST).

Headless, without the page:

```bash
.venv/bin/python -m poly_bot run --profile btc-5m       # run one bot until Ctrl+C
.venv/bin/python -m poly_bot plan --profile btc-5m --price 0.55   # print its ladder
```

## The public site: poly.zodypay.com (many users, email sign-in)

On the server the app runs in **hosted mode**: `python -m poly_bot hosted --host 127.0.0.1 --port 8090` (systemd
unit `poly-bot`), behind Caddy, which adds HTTPS for **https://poly.zodypay.com**.

- **No sign-in needed:** a first visit becomes a guest account tied to that browser (a cookie for a year).
  **Keep my bots with email** (optional, a 6-digit code sent with Resend) lets a user reach their bots from any
  browser; a guest who adds an email keeps their bots. Admins sign in with their email. Guests with live bots are
  warned to add an email first: a live bot keeps trading even if its browser's cookies are lost.
- **Each user has their own bots** (`hosted/users/<id>/profiles/`), as many as they like (`MAX_BOTS` can set a limit; 0 = none), and sees only those.
- **Paper bots** work for everyone. **Live bots** use the user's own Polymarket keys, stored **encrypted**
  (`POLY_SECRET_KEY`), and can only be started from a country where Polymarket allows trading. The app checks the
  *user's* address with DB-IP's free country database (`data/dbip-country-lite.csv.gz`; refresh monthly with
  `python -m poly_bot.geo`), never sending it anywhere. Blocked: Polymarket's "block completely" and "close-only on
  frontend and API" lists (US, UK, France, Germany, Australia, Canada, …); unknown addresses are blocked too.
  Japan, South Korea, Ireland, Netherlands and Malta are allowed (Polymarket restricts them on its website only).
- **Admins** (`ADMIN_EMAILS`) get a 👥 Users list: email, country, bots, running and live bots.
- Bots that were running when the app stopped start again with it.

Server settings live in the project `.env` (never commit it): `POLY_SECRET_KEY`, `RESEND_API_KEY`, `MAIL_FROM`,
`ADMIN_EMAILS`, `PUBLIC_HOSTS`, `MAX_BOTS`. **Losing `POLY_SECRET_KEY` makes every stored key unreadable** (users
must enter them again); keep a copy somewhere safe.

```bash
systemctl restart poly-bot          # after an update
journalctl -u poly-bot -f           # its log
python -m poly_bot.geo              # refresh the country database (monthly)
```

On your own computer `./start.sh` still runs the single-user app (no sign-in, localhost only).

## 6. Paper-trading limits

Paper results are an estimate of live results:

- The fee is charged in USDC on top of the bet; on Polymarket the buy fee comes out of the shares. The difference is
  under a cent at these sizes.
- The order book is read when the bot buys and is assumed to fill instantly. Real orders can lose a fast-moving
  price.
- Your paper bets don't move the real book, so very large paper bets fill better than real ones would.
