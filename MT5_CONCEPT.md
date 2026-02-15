# MetaTrader 5 Expert Advisor — MSNR Sniper Protocol

## Overview

A fully automated MetaTrader 5 Expert Advisor (EA) that executes the MSNR (Miss, Sweep, No-Retest) Sniper Protocol. Pure SMC logic — no indicators. The EA scans for institutional supply/demand zones, validates them through a 5-layer invalidation filter, and places trades with precision entries, sweep-based stop losses, and opposing-zone take profits.

---

## Architecture

```
MSNR_Sniper_EA/
├── MSNR_Sniper.mq5              // Main EA file (OnInit, OnTick, OnDeinit)
├── Modules/
│   ├── ZoneEngine.mqh            // Zone detection, freshness, FLIP/MISS logic
│   ├── StructureEngine.mqh       // BOS, swing points, liquidity sweeps
│   ├── StorylineEngine.mqh       // HTF bias detection (rejection storyline)
│   ├── ArrivalFilter.mqh         // Compression vs momentum arrival
│   ├── RoadblockFilter.mqh       // RR gatekeeper + opposing zone scan
│   ├── TriggerEngine.mqh         // Engulfing detection, inducement sweep
│   ├── TradeManager.mqh          // Entry, SL, TP, position sizing, trailing
│   └── TimeFilter.mqh            // Session hours, news blackout
└── Presets/
    ├── Gold_Scalp_M15.set        // XAUUSD M15 with H4 storyline
    ├── EURUSD_Swing_H1.set       // EURUSD H1 with 1D storyline
    └── V75_Scalp_M5.set          // Volatility 75 M5 with H1 storyline
```

---

## Input Parameters

```mql5
// ===== Timeframes =====
input ENUM_TIMEFRAMES EntryTF         = PERIOD_M15;   // Entry timeframe (M5/M15/M30/H1)
input ENUM_TIMEFRAMES StorylineTF     = PERIOD_H4;    // HTF bias timeframe (H4/D1/W1)

// ===== Zone Detection =====
input int             ZoneLookback     = 40;           // Candles to scan for zones
input double          MitigationBuffer = 0.001;        // 0.1% — zone touch tolerance
input int             MissWindow       = 3;            // Candles for MISS classification

// ===== Arrival Physics =====
input double          MarubozuMultiple = 2.5;          // Body > N× avg = momentum (kill)
input int             ArrivalLookback  = 3;            // Candles to check for momentum
input int             AvgBodyWindow    = 50;           // Window for avg body calculation

// ===== Structure =====
input int             SwingLookback    = 20;           // Bars for swing high/low detection
input int             BOSLookback      = 5;            // Bars to check for BOS

// ===== Risk Management =====
input double          RiskPercent      = 1.0;          // Risk per trade (% of balance)
input double          MinRR            = 2.0;          // Minimum reward:risk ratio
input double          MaxRiskPips      = 50;           // SL cap in pips
input int             MaxOpenTrades    = 3;            // Max concurrent positions
input int             CooldownBars     = 4;            // Min bars between signals

// ===== Execution =====
input ENUM_EXEC_MODE  ExecMode         = EXEC_LIMIT;   // LIMIT (zone retest) or MARKET
input int             LimitExpiryBars  = 12;           // Cancel pending order after N bars
input int             Slippage         = 3;            // Max slippage in points

// ===== Session Filter =====
input bool            UseLondon        = true;         // Trade London session
input bool            UseNewYork       = true;         // Trade New York session
input bool            UseNewsFilter    = true;         // Pause around high-impact news
input int             NewsBlackoutMin  = 30;           // Minutes before/after news
```

---

## Core Logic — 5-Layer Invalidation Filter

Every `OnTick()` cycle runs through these layers **in order**. Any layer returning `false` kills the signal immediately. No signal = no trade. This is invalidation-first, not confirmation-stacking.

### Layer 1 — Fresh Zone Detection

**Purpose:** Find untouched institutional supply/demand zones.

**Zone Types:**
| Type | Formation | Direction |
|------|-----------|-----------|
| A-Level | Bullish candle → Bearish candle | Supply (resistance) |
| V-Level | Bearish candle → Bullish candle | Demand (support) |
| OC-Gap | Same direction, gap between C1.close and C2.open | Contextual |
| FLIP | Zone body-broken, direction reversed (SBR/RBS) | Opposite of original |

**Freshness Rules:**
- Zone is **fresh** if no subsequent candle wick enters it (including 0.1% mitigation buffer)
- Zone becomes **MISS** if 3 consecutive candles after formation don't touch it (strong displacement)
- If a candle **body closes through** a zone, the zone breaks and flips direction → **FLIP zone**
- FLIP zones checked for freshness independently from their new formation bar

**Priority Ranking:** FLIP > MISS > Most Recent

**Kill condition:** No fresh zone matching the required direction → no trade.

```
ZoneEngine Logic:
  for each candle pair in last 40 bars:
    if bullish → bearish:  create A-Level (supply)
    if bearish → bullish:  create V-Level (demand)
    if same direction + gap: create OC-Gap

  for each zone:
    scan all subsequent candles:
      if body closes through zone → mark unfresh, create FLIP zone
      if wick enters zone (± 0.1% buffer) → mark unfresh
      if first 3 candles all miss → mark as MISS zone

  return fresh zones sorted by: FLIP first, MISS second, recency third
```

### Layer 2 — HTF Storyline Gatekeeper

**Purpose:** Forbid trading against the higher-timeframe institutional direction.

**How it works:**
1. Scan HTF (H4/D1/W1) for fresh supply and demand zones
2. Check last 3 HTF candles for **rejection** off a fresh zone:
   - **Bullish rejection:** Wick dips into fresh demand zone, but body closes above it
   - **Bearish rejection:** Wick spikes into fresh supply zone, but body closes below it
3. Rejection determines bias direction
4. Confirm with LTF BOS in same direction

**Kill conditions:**
- HTF bias is BULL but no bullish BOS on LTF → no trade
- HTF bias is BEAR but no bearish BOS on LTF → no trade
- No HTF rejection detected → fall back to momentum bias (close vs close 20 bars ago)

**TP Targeting:** The nearest opposing fresh HTF zone becomes the take-profit target. If BULL bias, TP = nearest fresh supply zone above entry. If no opposing zone exists, use HTF high/low extreme.

### Layer 3 — Arrival Physics

**Purpose:** Determine HOW price arrived at the zone. Compression = safe. Momentum = danger.

**Momentum Detection:**
1. Calculate average body size over last 50 candles
2. Check the last 3 candles approaching the zone
3. If ANY candle has a body > 2.5× the average → **Marubozu detected** → momentum arrival

**Kill condition:** Momentum arrival = institutions are already breaking through. Zone is dead.

**Pass condition:** All recent candles have normal/small bodies = compression arrival. Price is being absorbed, not smashed.

### Layer 4 — Roadblock RR Check

**Purpose:** Ensure minimum 1:2 risk-to-reward ratio with no opposing zone blocking the path.

**Process:**
1. Calculate risk distance: entry price to zone boundary (where SL goes)
2. Find nearest opposing fresh zone between entry and TP
3. If distance to that blocker < 2× the risk distance → RR < 1:2

**Kill condition:** RR to nearest roadblock < 1:2 → no trade. No room to breathe.

**Pass condition:** Clear path with RR >= 1:2, or no opposing zones in the way at all.

### Layer 5 — Trigger Confirmation

**Purpose:** Require a specific candle pattern at the zone to confirm institutional intent.

**Engulfing Detection:**
- Current candle body fully wraps previous candle body
- Bullish engulfing: close > open, body engulfs prior, low touches demand zone
- Bearish engulfing: close < open, body engulfs prior, high touches supply zone

**Inducement Sweep Detection:**
- For BUY: wick dipped below swing low but body closed back above it (stop hunt trap)
- For SELL: wick spiked above swing high but body closed back below it
- Track the **deepest sweep wick level** for SL placement

**Confidence Tiers:**
| Tier | Condition | Label |
|------|-----------|-------|
| Gold | Inducement Sweep + Engulfing | HIGH |
| Silver | Engulfing only | MEDIUM |
| None | No engulfing | KILLED (no trade) |

**Kill condition:** No engulfing pattern detected at zone → no trade.

---

## Trade Execution

### Entry

**LIMIT Mode:**
- BUY: Pending buy-limit at zone top (demand zone upper boundary)
- SELL: Pending sell-limit at zone bottom (supply zone lower boundary)
- Pending order expires after `LimitExpiryBars` candles if not filled

**MARKET Mode:**
- Execute at current price on trigger candle close
- Only if current candle is touching the zone

### Stop Loss Placement

Priority order:
1. **Sweep wick level** — If inducement was swept, SL goes below/above the deepest wick (the actual liquidity grab point). This is the tightest, most precise SL.
2. **Zone boundary** — If no sweep detected, SL below zone bottom (BUY) or above zone top (SELL).
3. **Risk cap** — SL capped at `MaxRiskPips` from entry. If raw SL exceeds this, compress to max distance.

### Take Profit Placement

1. **Primary:** Nearest opposing fresh zone from HTF storyline scan
   - BUY → nearest fresh supply zone bottom above entry
   - SELL → nearest fresh demand zone top below entry
2. **Fallback:** HTF extreme (highest high for BUY, lowest low for SELL)

### Position Sizing

```
Lot Size = (AccountBalance × RiskPercent / 100) / (SL distance in price × TickValue / TickSize)
```

Capped by broker margin requirements and `MaxOpenTrades`.

---

## Trade Management

### Active Position Rules

- **No manual trailing.** TP and SL are set at entry and left alone. The zones define the battlefield — respect them.
- **Break-even option (optional):** Move SL to entry + spread after price moves 1:1 in favor. Disabled by default — tends to get stopped out on retests.
- **Partial close (optional):** Close 50% at 1:1 RR, let remainder run to full TP. Disabled by default.

### Cooldown

- After a signal fires (win or loss), wait `CooldownBars` candles before evaluating new signals on the same symbol
- Opposite direction signals bypass cooldown (trend reversal = new context)

### Pending Order Management

- LIMIT orders expire after `LimitExpiryBars` if not filled
- If zone becomes unfresh while order is pending → cancel immediately
- If HTF bias flips while order is pending → cancel immediately

---

## Session & News Filter

### Session Windows (UTC)
| Session | Open | Close |
|---------|------|-------|
| London | 07:00 | 16:00 |
| New York | 12:00 | 21:00 |

- If `UseLondon = true` and `UseNewYork = true` → trade 07:00–21:00 UTC
- Crypto and synthetic indices bypass session filter (24/7 markets)

### News Filter
- Fetch high/medium impact events from economic calendar
- Pause signal generation `NewsBlackoutMin` minutes before and after events
- Only filters currencies affected by the event (e.g., NFP only blocks USD pairs)

---

## Dashboard Panel (Chart Overlay)

On-chart panel showing real-time status:

```
┌──────────────────────────────────┐
│  MSNR SNIPER v1.0                │
├──────────────────────────────────┤
│  HTF Bias:    BULL (H4 demand    │
│               rejection)         │
│  Fresh Zones: 2 demand, 1 supply │
│  Best Zone:   FLIP @ 1.0842     │
│  Arrival:     COMPRESSION        │
│  Roadblock:   CLEAR (RR 1:3.2)  │
│  Trigger:     WAITING            │
│  Status:      SCANNING           │
├──────────────────────────────────┤
│  Open:  1 BUY EURUSD +42 pips   │
│  Today: 2W / 0L / 1 pending     │
│  Week:  7W / 2L (78% WR)        │
└──────────────────────────────────┘
```

### Zone Visualization

Draw zones directly on the chart:
- **Fresh demand zones:** Blue rectangles
- **Fresh supply zones:** Red rectangles
- **FLIP zones:** Yellow rectangles with "FLIP" label
- **MISS zones:** Dashed border
- **Unfresh zones:** Grayed out (optional, off by default)
- **HTF rejection zone:** Thick border highlight

---

## OnTick Flow (Pseudocode)

```
OnTick():
  if not IsNewBar(EntryTF): return           // Only evaluate on new candle close
  if not InTradingSession(): return          // Session filter
  if IsNewsBlackout(): return                // News filter
  if OpenPositions >= MaxOpenTrades: return   // Position limit
  if InCooldown(): return                    // Cooldown check

  // Load candle data
  ltf_bars = CopyRates(Symbol, EntryTF, 0, 100)
  htf_bars = CopyRates(Symbol, StorylineTF, 0, 100)

  // === LAYER 2: HTF Storyline ===
  storyline = DetectStoryline(htf_bars, ltf_bars)
  if storyline == NULL: return
  bias = storyline.bias

  // Swing points + BOS on LTF
  swing_high, swing_low = FindSwingPoints(ltf_bars)
  bull_bos, bear_bos, bull_sweep, bear_sweep = DetectBOS(ltf_bars, swing_high, swing_low)

  // H4 Gatekeeper
  if bias == BULL and not bull_bos: return
  if bias == BEAR and not bear_bos: return

  // Determine direction
  direction = (bias == BULL) ? "BUY" : "SELL"
  zone_direction = (bias == BULL) ? "demand" : "supply"

  // === LAYER 1: Fresh Zone ===
  zones = GetFreshZones(ltf_bars, zone_direction)
  if zones.empty(): return
  zone = zones[0]  // Highest priority (FLIP > MISS > recent)

  entry_price = (direction == BUY) ? zone.top : zone.bottom

  // === LAYER 3: Arrival Physics ===
  if not AnalyzeArrival(ltf_bars, entry_price): return

  // === LAYER 4: Roadblock RR ===
  all_fresh = GetAllFreshZones(ltf_bars)
  risk_distance = (direction == BUY) ? entry_price - zone.bottom : zone.top - entry_price
  if not CheckRoadblocks(entry_price, direction, all_fresh, risk_distance): return

  // === LAYER 5: Trigger ===
  if not CurrentCandleTouchesZone(ltf_bars, zone): return
  engulfing = DetectEngulfing(ltf_bars, zone)
  if engulfing == NULL: return

  induce = DetectInducementSwept(ltf_bars, swing_high, swing_low, direction)
  confidence = ComputeConfidence(induce.swept, engulfing)

  // === EXECUTE ===
  sl = (induce.swept) ? induce.wick_level : zone boundary
  sl = ClampSL(entry_price, sl, MaxRiskPips)
  tp = storyline.tp_target

  lots = CalculateLotSize(RiskPercent, entry_price, sl)

  if ExecMode == LIMIT:
    PlacePendingOrder(direction, entry_price, sl, tp, lots, LimitExpiryBars)
  else:
    ExecuteMarketOrder(direction, sl, tp, lots)

  StartCooldown()
  UpdateDashboard()
```

---

## Key Differences from Indicator-Based EAs

| Aspect | Typical EA | MSNR Sniper EA |
|--------|-----------|----------------|
| Signal logic | Indicator crossovers (MA, RSI, MACD) | Institutional zone invalidation |
| Entry frequency | High (every crossover) | Low (only survivors of 5 filters) |
| SL placement | Fixed pips or ATR multiple | Sweep wick level (precision) |
| TP placement | Fixed pips or RR ratio | Nearest opposing fresh zone |
| Trend filter | Moving average slope | HTF rejection off fresh zone |
| Confirmation | Indicator agreement | Engulfing at zone + inducement sweep |
| Adaptability | Lagging (indicators are derivatives of price) | Leading (zones form before price returns) |

---

## Backtesting Notes

- **Use OHLC on M1** or **Every tick based on real ticks** for accurate wick data. Zone freshness depends on exact wick levels — "Open prices only" will produce garbage results.
- **Expect low trade count.** 5 filters on top of each other means the EA will be quiet. On M15 XAUUSD, expect roughly 2–5 trades per week during active sessions. This is by design.
- **Optimization targets:** Maximize profit factor and Sharpe ratio, not total trades. Optimize `ZoneLookback`, `MarubozuMultiple`, `MinRR`, and `SwingLookback`.
- **Walk-forward validation required.** SMC zones are structural — they should generalize across market conditions. If they don't survive walk-forward, the lookback parameters are overfit.

---

## Symbol Compatibility

The EA works on any liquid instrument with clean OHLC data:
- **Forex majors/crosses** — XAUUSD, EURUSD, GBPUSD, USDJPY, etc.
- **Crypto CFDs** — BTCUSD, ETHUSD (if broker supports)
- **Synthetic indices** — V75, Boom/Crash (Deriv MT5)
- **Indices** — US30, NAS100, GER40 (if broker supports)

Pip value calculation adjusts automatically per symbol using `SymbolInfoDouble(SYMBOL_TRADE_TICK_VALUE)` and `SYMBOL_TRADE_TICK_SIZE`.
