# Signalix Trading Bot

Automated forex and crypto signal bot built on ICT/SMC methodology. Generates mechanical, rule-based trade signals via Telegram based on Break of Structure (BOS) directional bias and M15 setup detection.

## Strategy Overview

### 1. HTF Bias (Daily/Weekly)

The bot determines directional bias using BOS logic on completed daily and weekly candles:

| Condition | Bias |
|-----------|------|
| PDC > PPDH | **Bullish** — Break of Structure up |
| PDC < PPDL | **Bearish** — Break of Structure down |
| PDL < PPDL & PDC > PPDL | **Bullish** — Sweep + reclaim (liquidity grab reversal) |
| PDH > PPDH & PDC < PPDH | **Bearish** — Sweep + reject (liquidity grab reversal) |

- **PDC** = Previous Day Close, **PPDH/PPDL** = Previous Previous Day High/Low
- Weekly bias is computed the same way from weekly bars (built from daily candles)
- **Weekly bias overrides daily** when not neutral

### 2. Key Level Mapping

Every scan cycle maps these levels from candle data:

- **PDH / PDL** — Previous Day High / Low
- **PWH / PWL** — Previous Week High / Low
- **Asian High / Low** — Asian session (00:00–08:00 UTC) range from H1 candles

### 3. Setup Detection (M15 Framework)

Two setup types, both trading WITH the HTF bias:

**Continuation** — Price continues in bias direction after displacement:
1. HTF bias confirmed
2. M15 or M5 displacement candle in bias direction (body > 1.5x average)
3. FVG or Order Block forms (Point of Interest)
4. Price retraces into the FVG/OB zone
5. Optional: liquidity sweep of a key level adds confluence

**Reversal** — Price manipulates against bias first, then reverses back:
1. HTF bias confirmed (e.g., bullish)
2. Price sweeps against bias (wick below PDL / Asian Low, body closes back above)
3. CHoCH (Change of Character) on M15 — break above recent swing high with displacement
4. FVG or Order Block forms
5. Price retraces into the FVG/OB zone

### 4. Trade Execution

- **Entry**: FVG consequent encroachment (midpoint) or OB midpoint
- **Stop Loss**: Below OB/FVG low (longs) or above OB/FVG high (shorts), or below sweep wick. Minimum 3 pips.
- **Take Profit**: Next liquidity zone (PDH/PDL/PWH/PWL/Asian levels)
- **R:R minimum**: 1:3
- **Kill Zones**: London 07:00–11:00 UTC, New York 12:00–17:00 UTC
- **Max daily losses**: 3 (tracked via signal outcome monitoring)
- **Duplicate prevention**: No repeat signal for same pair + direction within 4 hours

### 5. Signal Tracking

A background job runs every 1 minute to check open signals against current price:
- If price hits SL → marked as loss, daily loss counter incremented
- If price hits TP → marked as win

## Architecture

```
Bot/
├── main.py                    # Entry point, wires everything together
├── config.py                  # Pairs, timeframes, kill zones, risk params
├── requirements.txt
├── Procfile                   # Railway deployment
├── railway.json               # Railway config (auto-restart on failure)
│
├── feeds/
│   ├── deriv_client.py        # Deriv WebSocket client (forex: EURUSD, GBPUSD, XAUUSD, GBPJPY)
│   └── bybit_client.py        # Bybit REST client (crypto: BTCUSDT)
│
├── strategy/
│   ├── bias.py                # HTF bias computation (daily + weekly BOS)
│   ├── levels.py              # PDH/PDL/PWH/PWL/Asian level mapping
│   ├── detectors.py           # FVG, OB, displacement, CHoCH, BOS, liquidity sweep
│   ├── setups.py              # Continuation + Reversal setup scanning
│   └── execution.py           # Entry/SL/TP calculation, R:R validation
│
├── signals/
│   ├── generator.py           # Signal persistence + Telegram formatting
│   └── tracker.py             # Open signal monitoring (SL/TP hit tracking)
│
├── delivery/
│   ├── telegram_bot.py        # Telegram bot (polling, commands, signal delivery)
│   └── scheduler.py           # APScheduler jobs, candle fetching, scan orchestration
│
├── database/
│   ├── db.py                  # asyncpg connection pool
│   └── schema.py              # Table definitions (signals, daily_stats, errors, bot_settings)
│
└── utils/
    ├── logger.py              # Logging setup
    └── helpers.py             # Kill zone check, pip conversion
```

## Scan Pipeline (every 15 minutes during kill zones)

```
1. Check kill zone active (London / New York)
2. Check daily loss limit not reached
3. For each pair:
   a. Fetch candles (D, H1, M15, M5) from Deriv or Bybit
   b. Compute HTF bias (daily BOS, weekly BOS override)
   c. Skip if bias is NEUTRAL
   d. Map key levels (PDH, PDL, PWH, PWL, Asian H/L)
   e. Scan for Continuation setup on M15
   f. Scan for Reversal setup on M15
   g. For each setup found:
      - Check duplicate prevention (4h window)
      - Compute entry, SL, TP
      - Validate R:R >= 1:3 and SL >= 3 pips
      - Persist to database
      - Deliver via Telegram
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `TELEGRAM_TOKEN` | Yes | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Yes | Chat/channel ID for signal delivery |
| `DERIV_APP_ID` | Yes | Deriv API application ID |
| `BYBIT_API_KEY` | No | Bybit API key (for BTCUSDT) |
| `BYBIT_API_SECRET` | No | Bybit API secret |
| `ADMIN_CHAT_IDS` | No | Comma-separated Telegram user IDs for admin access |
| `PORT` | No | Health server port (default: 8080) |

## Active Pairs

| Pair | Feed | Pip Size |
|------|------|----------|
| EURUSD | Deriv (`frxEURUSD`) | 0.0001 |
| GBPUSD | Deriv (`frxGBPUSD`) | 0.0001 |
| XAUUSD | Deriv (`frxXAUUSD`) | 0.10 |
| GBPJPY | Deriv (`frxGBPJPY`) | 0.01 |
| BTCUSDT | Bybit | 1.0 |

> **US30**: Not yet active. Add the correct symbol to `DERIV_PAIRS` or `BYBIT_PAIRS` in `config.py` once confirmed with your feed provider.

## Deployment (Railway)

1. Push to your Railway-connected branch
2. Set all required environment variables in Railway dashboard
3. Railway auto-detects `Procfile` and runs `python main.py`
4. Bot starts, connects to Deriv/Bybit/Telegram/PostgreSQL, and begins scanning

The `railway.json` configures auto-restart on failure with up to 10 retries.

## Database Tables

- **signals** — All generated signals with entry/SL/TP, status (open/closed), result (win/loss)
- **daily_stats** — Per-day signal count, wins, and losses
- **bot_settings** — Key-value config (extensible)
- **errors** — Error log for debugging

Tables are auto-created on first run via `database/schema.py`.

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Bot info and command list |
| `/status` | Check if bot is running |
| `/pairs` | List active trading pairs |

## Signal Format (Telegram)

```
🟢 EURUSD LONG
Setup: Continuation

Entry: 1.08500
SL: 1.08200 (30.0 pips)
TP: 1.09400 (90.0 pips)
R:R: 1:3.0

  ✅ HTF bias: BULLISH
  ✅ Displacement confirmed
  ✅ FVG @ 1.08350-1.08550
  ✅ Liquidity swept: PDL

Session: London
```

## What Changed (Full Rebuild)

The previous bot used a dual-engine architecture (Precision 15-point / Flow 8-point) with COT data, Wyckoff phases, Volume Profile, and intermarket correlation. It was deleted and rebuilt from scratch with this simplified, mechanical BOS-based strategy:

| Old Bot | New Bot |
|---------|---------|
| Precision engine (7 gates, 15 points) | Single scan pipeline |
| Flow engine (5 gates, 8 points) | Two setup types: Continuation + Reversal |
| COT + Wyckoff + Volume Profile | BOS bias only (PDC vs PPDH/PPDL) |
| Kill zones optional (24/7 scanning) | Kill zones enforced (London + NY only) |
| AI rationale via DeepSeek | Removed |
| Payment/subscription tiers | Removed |
| 6 scheduler jobs | 2 jobs (scan + tracker) |
