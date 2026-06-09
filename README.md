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
├── vercel.json                # Cron schedules + function config
├── config.py                  # Pairs, timeframes, kill zones, risk params
├── requirements.txt
│
├── api/                       # Vercel serverless endpoints
│   ├── scan.py                # GET /api/scan — cron-triggered scan (every 15 min)
│   ├── track.py               # GET /api/track — cron-triggered signal tracker (every 1 min)
│   ├── webhook.py             # POST /api/webhook — Telegram webhook handler
│   ├── health.py              # GET /api/health — health check
│   └── setup.py               # GET /api/setup — one-time Telegram webhook + DB init
│
├── feeds/
│   ├── deriv_client.py        # Ephemeral Deriv WebSocket client (forex)
│   └── bybit_client.py        # Bybit REST client (crypto)
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
│   ├── telegram.py            # Direct Telegram Bot API client (HTTP, no polling)
│   └── scan.py                # Core scan cycle logic
│
├── database/
│   ├── db.py                  # ServerlessDB — single asyncpg connection wrapper
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
| `CRON_SECRET` | Yes | Vercel cron secret (auto-set by Vercel, used to verify cron requests) |
| `TELEGRAM_WEBHOOK_SECRET` | Recommended | Secret token to authenticate Telegram webhook calls |
| `WEBHOOK_BASE_URL` | No | Override for the webhook domain (defaults to the Vercel production URL) |

## Active Pairs

| Pair | Feed | Pip Size |
|------|------|----------|
| EURUSD | Deriv (`frxEURUSD`) | 0.0001 |
| GBPUSD | Deriv (`frxGBPUSD`) | 0.0001 |
| XAUUSD | Deriv (`frxXAUUSD`) | 0.10 |
| GBPJPY | Deriv (`frxGBPJPY`) | 0.01 |
| BTCUSDT | Bybit | 1.0 |

> **US30**: Not yet active. Add the correct symbol to `DERIV_PAIRS` or `BYBIT_PAIRS` in `config.py` once confirmed with your feed provider.

## Deployment (Vercel)

**Requires Vercel Pro plan** for cron jobs (every 1-15 min) and 300s function timeout.

1. Connect your GitHub repo to Vercel
2. Set all required environment variables in Vercel dashboard
3. Deploy — Vercel auto-detects `api/` serverless functions and `vercel.json` cron config
4. After first deploy, hit `/api/setup` once to register the Telegram webhook and create DB tables
5. Cron jobs start automatically: scan every 15 min, tracker every 1 min

### How it works (serverless)

Each cron trigger is a fresh function invocation:
1. Opens a Deriv WebSocket connection
2. Fetches candles for all pairs
3. Runs the full scan pipeline
4. Sends signals via Telegram HTTP API
5. Closes all connections

No persistent state between invocations — every run is independent.

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

## Why We Rebuilt — Problems With the Old Bot

The previous bot (dual-engine: Precision 15-point / Flow 8-point) stopped producing signals entirely after February 26, 2026. A full investigation uncovered multiple compounding failures:

### 1. WebSocket Concurrency Race Condition

The `DerivClient` shared a single WebSocket connection across 3+ concurrent APScheduler jobs (`tracking_job`, `precision_scan`, `flow_scan`) with no synchronization. When multiple coroutines called `ws.recv()` simultaneously, one would steal another's response, causing:

- `"cannot call recv while another coroutine is already waiting"` errors
- `run_flow_scan` permanently hanging — it never completed a single run after the bug triggered
- APScheduler logging `"maximum number of running instances reached"` every 5 minutes indefinitely

**Impact**: Flow engine was 100% dead. No Flow signals could ever be generated.

### 2. Structure Shift Detection Too Narrow

`detect_structure_shift()` only checked `candles[-1]` (the very last candle) for CHoCH/BOS breakouts, with a 1.5x displacement threshold. This meant:

- On the **Daily timeframe** (Precision Gate 2), a structure break was only detectable on the exact day it happened — a single 24-hour window
- On **M15** (Flow Gate 4, Precision Gate 6), a CHoCH was only visible for one 15-minute bar
- Combined with the WebSocket bug corrupting data fetches, the detection window was effectively zero

**Impact**: Both Precision and Flow pipelines were blocked at their MSS/CHoCH gates. Even when the WebSocket worked, signals couldn't pass through.

### 3. COT Data Fetch Failing for XAUUSD

The CFTC COT API was returning errors/empty data for gold (contract 088691), and the `cot_cache` database table had no fallback data. Since COT alignment was Gate 1 of the Precision pipeline for XAUUSD, this pair was hard-blocked from ever generating a signal.

### 4. Overly Complex Pipeline

The old bot required signals to pass through 7 sequential gates (Precision) or 5 gates (Flow), each with strict thresholds. With multiple gates silently failing due to the bugs above, the compounding rejection rate was 100%. The complexity made it difficult to diagnose which gate was actually blocking signals.

### Fixes Applied (Before Rebuild)

| Fix | Commit |
|-----|--------|
| Added `asyncio.Lock` to serialize WebSocket requests, 15s recv timeout, 120s per-pair fetch timeout | `9d3b9b7` |
| Changed structure shift detection to check last 3 candles, lowered displacement threshold to 1.2x | `7476b77` |

These fixes would have restored signal generation, but the decision was made to rebuild the bot with a simpler, more mechanical strategy rather than patch the old architecture.

### Initial Deployment Failure (Vercel)

The first Vercel attempt failed with `"No python entrypoint found"` because the old bot was a long-running process (`main.py` with APScheduler + Telegram polling). The rebuild restructures everything as serverless functions with Vercel cron triggers, ephemeral connections, and Telegram webhooks — solving the architectural mismatch.

## What Changed (Full Rebuild)

The old bot was deleted and rebuilt from scratch with a simplified, mechanical BOS-based strategy and serverless architecture:

| Old Bot | New Bot |
|---------|---------|
| Precision engine (7 gates, 15 points) | Single scan pipeline |
| Flow engine (5 gates, 8 points) | Two setup types: Continuation + Reversal |
| COT + Wyckoff + Volume Profile | BOS bias only (PDC vs PPDH/PPDL) |
| Kill zones optional (24/7 scanning) | Kill zones enforced (London + NY only) |
| AI rationale via DeepSeek | Removed |
| Payment/subscription tiers | Removed |
| Long-running process (Railway) | Serverless functions (Vercel) |
| APScheduler (6 jobs) | Vercel cron (2 endpoints) |
| Telegram long-polling | Telegram webhooks |
| Persistent WebSocket + DB pool | Ephemeral connections per invocation |
| `python-telegram-bot` + `apscheduler` | Direct HTTP calls, 3 deps total |
