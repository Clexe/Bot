"""Per-pair analysis pipeline — runs the full HTF→LTF chain for one pair."""

from datetime import datetime
from config import FOREX_PAIRS, CRYPTO_PAIRS, TF_MAP_BYBIT
from strategy.oc_detector import detect_oc_levels
from strategy.storyline import build_storyline
from strategy.detectors import (
    detect_htf_rejection,
    detect_displacement_and_fvg,
    detect_liquidity_sweep,
    detect_structure_shift,
    detect_kill_zone,
)
from strategy.order_blocks import detect_order_blocks
from strategy.calculator import compute_trade_levels
from signals.generator import generate_signal
from signals.formatter import format_signal
from ai.deepseek_client import generate_rationale
from utils.logger import get_logger

logger = get_logger(__name__)

# Deriv granularity in seconds for each timeframe
DERIV_GRANULARITY = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D": 86400, "W": 604800,
}

# Deriv symbol mapping (Signalix pair → Deriv symbol)
DERIV_SYMBOL_MAP = {
    "EURUSD": "frxEURUSD", "GBPUSD": "frxGBPUSD",
    "USDJPY": "frxUSDJPY", "XAUUSD": "frxXAUUSD",
}


def _is_forex(pair: str) -> bool:
    """Check if pair should use Deriv (forex) or Bybit (crypto)."""
    return pair in FOREX_PAIRS or pair in DERIV_SYMBOL_MAP


def _normalize_bybit_klines(raw: dict) -> list:
    """Convert Bybit V5 kline response to standard candle dicts."""
    result = raw.get("result", {})
    items = result.get("list", [])
    candles = []
    for item in items:
        # Bybit returns: [timestamp, open, high, low, close, volume, turnover]
        candles.append({
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "timestamp": int(item[0]),
        })
    # Bybit returns newest first — reverse to chronological order
    candles.reverse()
    return candles


def _normalize_deriv_candles(raw: list) -> list:
    """Convert Deriv candle response to standard candle dicts."""
    return [
        {
            "open": float(c.get("open", 0)),
            "high": float(c.get("high", 0)),
            "low": float(c.get("low", 0)),
            "close": float(c.get("close", 0)),
            "timestamp": int(c.get("epoch", 0)),
        }
        for c in raw
    ]


async def _fetch_candles(pair, timeframe, bybit, deriv, limit=200):
    """Fetch candles for a pair+timeframe from the appropriate source."""
    try:
        if _is_forex(pair):
            deriv_sym = DERIV_SYMBOL_MAP.get(pair, pair)
            gran = DERIV_GRANULARITY.get(timeframe, 900)
            if not deriv.ws or deriv.ws.closed:
                await deriv.connect()
            raw = await deriv.get_history(deriv_sym, granularity=gran, count=limit)
            return _normalize_deriv_candles(raw)
        else:
            if timeframe not in TF_MAP_BYBIT:
                logger.warning("Timeframe %s not in Bybit map, skipping %s", timeframe, pair)
                return []
            raw = await bybit.get_kline(pair, timeframe, limit=limit)
            return _normalize_bybit_klines(raw)
    except Exception as e:
        logger.error("Failed to fetch candles for %s %s: %s", pair, timeframe, e)
        return []


def _compute_session_levels(candles):
    """Compute session high/low from the last ~4h of LTF candles."""
    recent = candles[-16:] if len(candles) >= 16 else candles
    if not recent:
        return {"high": 0, "low": 0}
    return {
        "high": max(c["high"] for c in recent),
        "low": min(c["low"] for c in recent),
    }


async def run_pair_pipeline(pair, db, telegram, bybit, deriv):
    """Execute the full analysis pipeline for one pair and deliver if signal passes."""
    try:
        # ── 1. Fetch candles ──
        htf_candles = await _fetch_candles(pair, "D", bybit, deriv, limit=100)
        ltf_candles = await _fetch_candles(pair, "M15", bybit, deriv, limit=200)

        if len(htf_candles) < 5 or len(ltf_candles) < 5:
            logger.debug("Insufficient candles for %s (HTF=%d, LTF=%d)", pair, len(htf_candles), len(ltf_candles))
            return None

        current_price = ltf_candles[-1]["close"]

        # ── 2. HTF analysis ──
        oc_levels = await detect_oc_levels(htf_candles, "D")
        storyline = await build_storyline(pair, current_price, oc_levels)
        htf_rejection = await detect_htf_rejection(htf_candles, oc_levels)

        storyline_ok = storyline["direction"] != "neutral"

        # ── 3. LTF analysis ──
        session_levels = _compute_session_levels(ltf_candles)
        shift_detected, shift_type = await detect_structure_shift(ltf_candles)
        fvg_detected, fvg_zone = await detect_displacement_and_fvg(ltf_candles)
        liq_swept = await detect_liquidity_sweep(ltf_candles, session_levels)
        order_blocks = await detect_order_blocks(ltf_candles, "M15")

        # ── 4. Determine direction and entry ──
        if storyline["direction"] == "bullish":
            direction = "LONG"
        elif storyline["direction"] == "bearish":
            direction = "SHORT"
        else:
            return None

        # Use FVG CE as entry if available, otherwise current price
        entry = fvg_zone["ce"] if fvg_zone else current_price

        # Build TP candidates from OC levels on the target side
        if direction == "LONG":
            tp_candidates = sorted(
                [l["level_price"] for l in oc_levels if l["level_type"] == "resistance" and l["level_price"] > entry],
            )
        else:
            tp_candidates = sorted(
                [l["level_price"] for l in oc_levels if l["level_type"] == "support" and l["level_price"] < entry],
                reverse=True,
            )

        # Fall back to OB midpoints if no OC targets
        if len(tp_candidates) < 3 and order_blocks:
            ob_targets = [ob["ob_midpoint"] for ob in order_blocks
                          if (direction == "LONG" and ob["ob_midpoint"] > entry) or
                             (direction == "SHORT" and ob["ob_midpoint"] < entry)]
            if direction == "SHORT":
                ob_targets.sort(reverse=True)
            else:
                ob_targets.sort()
            tp_candidates.extend(ob_targets)

        if not tp_candidates:
            logger.debug("%s: No TP candidates found", pair)
            return None

        # ── 5. Compute trade levels ──
        sweep_low = session_levels["low"]
        sweep_high = session_levels["high"]
        trade_levels = await compute_trade_levels(direction, entry, sweep_low, sweep_high, tp_candidates)

        if trade_levels is None:
            logger.debug("%s: Trade levels rejected (RR < 3)", pair)
            return None

        # Determine fresh POI (any fresh OC level near entry)
        poi_fresh = any(
            abs(l["level_price"] - entry) / entry < 0.005
            for l in oc_levels if l["freshness_status"] == "fresh"
        )
        poi_level = next(
            (l for l in oc_levels if abs(l["level_price"] - entry) / entry < 0.005 and l["freshness_status"] == "fresh"),
            None,
        )

        # ── 6. Build context and generate signal ──
        context = {
            "storyline_established": storyline_ok,
            "poi_fresh": poi_fresh,
            "idm_swept": liq_swept,
            "ltf_shift_confirmed": shift_detected,
            "liquidity_swept": liq_swept,
            "fvg_confirmed": fvg_detected,
            "htf_bias": storyline["direction"],
            "poi_type": poi_level["level_type"] if poi_level else "none",
            "poi_price": poi_level["level_price"] if poi_level else 0,
            "structure_shift_type": shift_type or "none",
            "ltf_timeframe": "M15",
        }

        signal, rejection = await generate_signal(pair, direction, trade_levels, context)

        if signal is None:
            logger.debug("%s: Signal rejected — %s", pair, rejection)
            # Log rejected setup
            await _log_rejected_setup(db, pair, direction, context, rejection)
            return None

        # ── 7. Dedup check ──
        signal_key = f"{pair}_{direction}_{signal['kill_zone']}"
        existing = await db.fetchrow(
            "SELECT signal_key FROM sent_signals WHERE signal_key = %s AND created_at > NOW() - INTERVAL '60 minutes'",
            (signal_key,),
        )
        if existing:
            logger.debug("%s: Duplicate signal within cooldown", pair)
            return None

        # ── 8. AI rationale ──
        rationale = await generate_rationale(signal)
        signal["rationale"] = rationale

        # ── 9. Persist signal ──
        await db.execute(
            """INSERT INTO signals (pair, direction, entry, sl, tp1, tp2, tp3, rr_tp1, rr_tp2, rr_tp3, score, htf_bias, poi_type, poi_price, kill_zone, rationale)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (pair, direction, trade_levels["entry"], trade_levels["sl"],
             trade_levels["tp1"], trade_levels.get("tp2"), trade_levels.get("tp3"),
             trade_levels["rr_tp1"], trade_levels.get("rr_tp2"), trade_levels.get("rr_tp3"),
             signal["score"], storyline["direction"],
             context["poi_type"], context["poi_price"],
             signal["kill_zone"], rationale),
        )

        await db.execute(
            """INSERT INTO signal_history (pair, direction, mode, entry_price, tp_price, sl_price, zone_type, confidence)
               VALUES (%s,%s,'AUTO',%s,%s,%s,%s,%s)""",
            (pair, direction, trade_levels["entry"], trade_levels["tp1"], trade_levels["sl"],
             context["poi_type"], "high" if signal["score"] >= 8 else "medium"),
        )

        await db.execute(
            "INSERT INTO sent_signals (signal_key, price, direction) VALUES (%s,%s,%s) ON CONFLICT (signal_key) DO UPDATE SET created_at=NOW()",
            (signal_key, trade_levels["entry"], direction),
        )

        # ── 10. Deliver to users ──
        await _deliver_signal_to_users(db, telegram, signal)

        logger.info("✅ SIGNAL FIRED: %s %s [Score %s/10]", direction, pair, signal["score"])
        return signal

    except Exception as e:
        logger.error("Pipeline error for %s: %s", pair, e, exc_info=True)
        await _log_error(db, pair, e)
        return None


async def _deliver_signal_to_users(db, telegram, signal):
    """Send the signal to all active users who have this pair in their watchlist."""
    from database.users import load_users_async

    users = await load_users_async(db)
    pair = signal["pair"]

    for chat_id, settings in users.items():
        if pair not in settings.get("pairs", []):
            continue

        # Check session preference
        session_pref = settings.get("session", "BOTH")
        kz = signal.get("kill_zone", "")
        if session_pref == "LONDON" and kz != "London":
            continue
        if session_pref == "NY" and kz != "New York":
            continue

        tier = settings.get("tier", "free")
        message = format_signal(signal, tier)

        try:
            await telegram.send_message(int(chat_id), message)
        except Exception as e:
            logger.warning("Failed to send signal to %s: %s", chat_id, e)


async def _log_rejected_setup(db, pair, direction, context, reason):
    """Log a rejected setup for analytics."""
    try:
        score = sum([
            3 if context.get("storyline_established") else 0,
            2 if context.get("poi_fresh") else 0,
            2 if context.get("liquidity_swept") else 0,
            2 if context.get("fvg_confirmed") else 0,
        ])
        await db.execute(
            "INSERT INTO rejected_setups (pair, direction, score, rejection_reason) VALUES (%s,%s,%s,%s)",
            (pair, direction, score, str(reason)),
        )
    except Exception:
        pass


async def _log_error(db, pair, error):
    """Log an error to the errors table."""
    try:
        await db.execute(
            "INSERT INTO errors (error_type, error_message, pair) VALUES (%s,%s,%s)",
            (type(error).__name__, str(error)[:500], pair),
        )
    except Exception:
        pass
