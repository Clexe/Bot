"""Per-pair analysis pipeline — runs the full HTF→LTF chain for one pair.

Ported with: per-user cooldown, correlation filter, lot sizing,
rate limiter, AI rationale, and full signal delivery.
"""

import time
from datetime import datetime
from config import (
    FOREX_PAIRS, CRYPTO_PAIRS, TF_MAP_BYBIT, DERIV_GRANULARITY,
    DERIV_SYMBOL_MAP, DERIV_KEYWORDS, SIGNAL_TTL,
    CONFIDENCE_SIZE_MULTIPLIERS, get_pip_value,
)
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
from correlation import check_correlation
from rate_limiter import rate_limiter
from utils.logger import get_logger

logger = get_logger(__name__)

# In-memory sent signals for per-user cooldown
SENT_SIGNALS = {}


def _is_deriv(pair: str) -> bool:
    """Check if pair should use Deriv websocket."""
    return pair in DERIV_SYMBOL_MAP or any(k in pair.upper() for k in DERIV_KEYWORDS)


def _normalize_bybit_klines(raw: dict) -> list:
    """Convert Bybit V5 kline response to standard candle dicts."""
    result = raw.get("result", {})
    items = result.get("list", [])
    candles = []
    for item in items:
        candles.append({
            "open": float(item[1]), "high": float(item[2]),
            "low": float(item[3]), "close": float(item[4]),
            "timestamp": int(item[0]),
        })
    candles.reverse()  # Bybit returns newest first
    return candles


def _normalize_deriv_candles(raw: list) -> list:
    """Convert Deriv candle response to standard candle dicts."""
    return [
        {"open": float(c.get("open", 0)), "high": float(c.get("high", 0)),
         "low": float(c.get("low", 0)), "close": float(c.get("close", 0)),
         "timestamp": int(c.get("epoch", 0))}
        for c in raw
    ]


async def _fetch_candles(pair, timeframe, bybit, deriv, limit=200):
    """Fetch candles for a pair+timeframe from the appropriate source."""
    try:
        if _is_deriv(pair):
            deriv_sym = DERIV_SYMBOL_MAP.get(pair, pair)
            gran = DERIV_GRANULARITY.get(timeframe, 900)
            if not deriv.is_connected:
                await deriv.connect()
            raw = await deriv.get_history(deriv_sym, granularity=gran, count=limit)
            return _normalize_deriv_candles(raw)
        else:
            tf_key = timeframe
            # Map 1D→D, 1W→W for Bybit
            if tf_key == "1D":
                tf_key = "D"
            elif tf_key == "1W":
                tf_key = "W"
            if tf_key not in TF_MAP_BYBIT:
                return []
            raw = await bybit.get_kline(pair, tf_key, limit=limit)
            return _normalize_bybit_klines(raw)
    except Exception as e:
        logger.error("Failed to fetch candles for %s %s: %s", pair, timeframe, e)
        return []


async def fetch_current_price(pair, bybit, deriv):
    """Fetch only the latest price for a pair (for outcome checking)."""
    try:
        candles = await _fetch_candles(pair, "M5", bybit, deriv, limit=1)
        if candles:
            return candles[-1]["close"]
    except Exception as e:
        logger.error("Failed to fetch price for %s: %s", pair, e)
    return None


def _compute_session_levels(candles):
    """Compute session high/low from the last ~4h of LTF candles."""
    recent = candles[-16:] if len(candles) >= 16 else candles
    if not recent:
        return {"high": 0, "low": 0}
    return {
        "high": max(c["high"] for c in recent),
        "low": min(c["low"] for c in recent),
    }


def _should_send_signal(signal_key, direction, cooldown_sec):
    """Check per-user cooldown before sending."""
    last_info = SENT_SIGNALS.get(signal_key)
    if last_info is None:
        return True
    if not isinstance(last_info, dict):
        return True
    time_elapsed = (time.time() - last_info.get('time', 0)) > cooldown_sec
    direction_changed = last_info.get('direction') != direction
    return time_elapsed or direction_changed


def _cleanup_old_signals():
    """Remove expired entries from in-memory sent signals."""
    now = time.time()
    expired = [
        k for k, v in SENT_SIGNALS.items()
        if isinstance(v, dict) and (now - v.get('time', 0)) > SIGNAL_TTL
    ]
    for k in expired:
        del SENT_SIGNALS[k]


def _calc_lot_size(risk_pips, pip_value, balance, risk_pct, size_multiplier=1.0):
    """Calculate lot size from account balance and risk percentage."""
    if risk_pips <= 0 or balance <= 0 or risk_pct <= 0:
        return None
    if pip_value >= 10000:
        pip_per_lot = 10.0
    elif pip_value >= 100:
        pip_per_lot = 10.0
    elif pip_value >= 1:
        pip_per_lot = 10.0
    else:
        pip_per_lot = 1.0
    risk_amount = balance * risk_pct / 100
    lot = risk_amount / (risk_pips * pip_per_lot)
    lot *= size_multiplier
    return round(max(lot, 0.01), 2)


async def run_pair_pipeline(pair, db, telegram, bybit, deriv,
                            ltf="M15", htf="1D", touch_trade=False,
                            user_list=None, open_positions=None,
                            size_multiplier=1.0):
    """Execute the full analysis pipeline for one pair and deliver if signal passes."""
    try:
        # ── Periodic cleanup ──
        _cleanup_old_signals()

        # ── 1. Fetch candles ──
        htf_candles = await _fetch_candles(pair, htf, bybit, deriv, limit=100)
        ltf_candles = await _fetch_candles(pair, ltf, bybit, deriv, limit=200)

        if len(htf_candles) < 5 or len(ltf_candles) < 5:
            logger.debug("Insufficient candles for %s (HTF=%d, LTF=%d)",
                         pair, len(htf_candles), len(ltf_candles))
            return None

        current_price = ltf_candles[-1]["close"]

        # ── 2. HTF analysis ──
        htf_tf = "D" if htf in ("1D", "D") else htf
        oc_levels = await detect_oc_levels(htf_candles, htf_tf)
        storyline = await build_storyline(pair, current_price, oc_levels)
        htf_rejection = await detect_htf_rejection(htf_candles, oc_levels)
        storyline_ok = storyline["direction"] != "neutral"

        # ── 3. LTF analysis ──
        session_levels = _compute_session_levels(ltf_candles)
        shift_detected, shift_type = await detect_structure_shift(ltf_candles)
        fvg_detected, fvg_zone = await detect_displacement_and_fvg(ltf_candles)
        liq_swept = await detect_liquidity_sweep(ltf_candles, session_levels)
        order_blocks = await detect_order_blocks(ltf_candles, ltf)

        # ── 4. Determine direction ──
        if storyline["direction"] == "bullish":
            direction = "LONG"
        elif storyline["direction"] == "bearish":
            direction = "SHORT"
        else:
            return None

        # ── 5. Correlation check ──
        if open_positions:
            corr_ok, corr_reason = check_correlation(pair, direction, open_positions)
            if not corr_ok:
                logger.info("BLOCKED %s %s: correlation filter (%s)", direction, pair, corr_reason)
                return None

        # Entry price
        entry = fvg_zone["ce"] if fvg_zone else current_price

        # Build TP candidates
        if direction == "LONG":
            tp_candidates = sorted(
                [l["level_price"] for l in oc_levels
                 if l["level_type"] == "resistance" and l["level_price"] > entry])
        else:
            tp_candidates = sorted(
                [l["level_price"] for l in oc_levels
                 if l["level_type"] == "support" and l["level_price"] < entry],
                reverse=True)

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
            return None

        # ── 6. Compute trade levels ──
        trade_levels = await compute_trade_levels(
            direction, entry, session_levels["low"], session_levels["high"], tp_candidates)

        if trade_levels is None:
            logger.debug("%s: Trade levels rejected (RR < 3)", pair)
            return None

        # POI freshness
        poi_fresh = any(
            abs(l["level_price"] - entry) / max(entry, 0.0001) < 0.005
            for l in oc_levels if l["freshness_status"] == "fresh")
        poi_level = next(
            (l for l in oc_levels
             if abs(l["level_price"] - entry) / max(entry, 0.0001) < 0.005 and l["freshness_status"] == "fresh"),
            None)

        # ── 7. Build context and generate signal ──
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
            "ltf_timeframe": ltf,
        }

        signal, rejection = await generate_signal(pair, direction, trade_levels, context)

        if signal is None:
            logger.debug("%s: Signal rejected — %s", pair, rejection)
            await _log_rejected_setup(db, pair, direction, context, rejection)
            return None

        # Determine confidence
        confidence = "high" if signal["score"] >= 8 else "medium" if signal["score"] >= 6 else "low"

        # ── 8. AI rationale ──
        rationale = await generate_rationale(signal)
        signal["rationale"] = rationale

        # ── 9. Persist signal ──
        await db.execute(
            """INSERT INTO signals (pair, direction, entry, sl, tp1, tp2, tp3,
               rr_tp1, rr_tp2, rr_tp3, score, htf_bias, poi_type, poi_price,
               kill_zone, rationale)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (pair, direction, trade_levels["entry"], trade_levels["sl"],
             trade_levels["tp1"], trade_levels.get("tp2"), trade_levels.get("tp3"),
             trade_levels["rr_tp1"], trade_levels.get("rr_tp2"), trade_levels.get("rr_tp3"),
             signal["score"], storyline["direction"],
             context["poi_type"], context["poi_price"],
             signal["kill_zone"], rationale))

        await db.execute(
            """INSERT INTO signal_history (pair, direction, mode, entry_price, tp_price, sl_price,
               zone_type, confidence)
               VALUES (%s,%s,'AUTO',%s,%s,%s,%s,%s)""",
            (pair, direction, trade_levels["entry"], trade_levels["tp1"], trade_levels["sl"],
             context["poi_type"], confidence))

        # ── 10. Deliver to users with per-user cooldown + lot sizing ──
        if user_list:
            await _deliver_to_user_list(
                db, telegram, signal, trade_levels, user_list,
                pair, direction, confidence, size_multiplier)

        logger.info("✅ SIGNAL FIRED: %s %s [Score %s/10] [%s]",
                     direction, pair, signal["score"], confidence)
        return signal

    except Exception as e:
        logger.error("Pipeline error for %s: %s", pair, e, exc_info=True)
        await _log_error(db, pair, e)
        return None


async def _deliver_to_user_list(db, telegram, signal, trade_levels, user_list,
                                pair, direction, confidence, size_multiplier):
    """Deliver signal to a list of (uid, user_conf) with per-user cooldown and lot sizing."""
    from telegram.error import Forbidden, BadRequest
    from database.users import deactivate_user_async

    current_time = time.time()
    entry_bucket = f"{trade_levels['entry']:.2f}"
    pip_val = get_pip_value(pair)
    risk_pips = abs(trade_levels['entry'] - trade_levels['sl']) * pip_val
    sent_count = 0
    skipped_cooldown = 0

    for uid, user_conf in user_list:
        # Per-user dedup
        signal_key = f"{uid}_{pair}_{direction}_{entry_bucket}"
        cooldown_sec = user_conf.get('cooldown', 60) * 60

        if not _should_send_signal(signal_key, direction, cooldown_sec):
            skipped_cooldown += 1
            continue

        # Session check
        session_pref = user_conf.get("session", "BOTH")
        kz = signal.get("kill_zone", "")
        if session_pref == "LONDON" and kz != "London":
            continue
        if session_pref == "NY" and kz != "New York":
            continue

        # Format message
        tier = user_conf.get("tier", "free")
        message = format_signal(signal, tier)

        # Lot size calculation
        balance = user_conf.get("balance", 0)
        risk_pct = user_conf.get("risk_pct", 1)
        conf_mult = CONFIDENCE_SIZE_MULTIPLIERS.get(confidence, 1.0)
        total_mult = conf_mult * size_multiplier

        if balance > 0 and risk_pips > 0:
            lot = _calc_lot_size(risk_pips, pip_val, balance, risk_pct, total_mult)
            if lot and lot > 0:
                mult_note = f" x{total_mult:.1f}" if total_mult != 1.0 else ""
                lot_line = f"\nLot: {lot} ({risk_pct}% of ${balance:,.0f}{mult_note})"
                message += lot_line

        try:
            await rate_limiter.send_message(
                telegram.bot, int(uid), message)
            SENT_SIGNALS[signal_key] = {
                'price': trade_levels['entry'],
                'time': current_time,
                'direction': direction,
            }
            # Persist to sent_signals table
            await db.execute(
                "INSERT INTO sent_signals (signal_key, price, direction) VALUES (%s,%s,%s) ON CONFLICT (signal_key) DO UPDATE SET created_at=NOW()",
                (signal_key, trade_levels["entry"], direction))

            sent_count += 1
        except Forbidden:
            logger.info("User %s blocked bot, deactivating", uid)
            await deactivate_user_async(db, uid)
        except BadRequest as e:
            logger.warning("Bad request sending to %s: %s", uid, e)
        except Exception as e:
            logger.warning("Failed to send signal to %s: %s", uid, e)

    logger.info("Signal %s %s fan-out: %d sent, %d skipped (cooldown), %d total",
                direction, pair, sent_count, skipped_cooldown, len(user_list))


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
            (pair, direction, score, str(reason)))
    except Exception:
        pass


async def _log_error(db, pair, error):
    """Log an error to the errors table."""
    try:
        await db.execute(
            "INSERT INTO errors (error_type, error_message, pair) VALUES (%s,%s,%s)",
            (type(error).__name__, str(error)[:500], pair))
    except Exception:
        pass
