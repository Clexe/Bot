from datetime import datetime
from strategy.detectors import (
    detect_kill_zone, get_daily_bias, identify_poi_relaxed,
    detect_structure_shift, detect_displacement_and_fvg,
)
from strategy.calculator import compute_flow_levels
from strategy.flow_scoring import score_flow_setup
from utils.helpers import check_duplicate_signal
from utils.logger import get_logger

logger = get_logger(__name__)


async def run_flow_pipeline(pair: str, candles: dict, db) -> dict:
    """5-gate Flow engine orchestrator.

    Skips COT and Wyckoff entirely.
    Uses relaxed POI rules (up to 2 wick touches).
    Checks duplicate prevention: skip if Precision signal already sent this Kill Zone session.
    Reads all settings from bot_settings table on every cycle.
    """
    current_time = datetime.utcnow()

    # Check if engine is enabled
    flow_enabled = await db.fetchrow("SELECT value FROM bot_settings WHERE key='flow_signals_enabled'")
    if flow_enabled and flow_enabled["value"] == "false":
        return {"status": "skipped", "reason": "Flow engine paused"}

    # Check if bot is paused
    bot_paused = await db.fetchrow("SELECT value FROM bot_settings WHERE key='bot_paused'")
    if bot_paused and bot_paused["value"] == "true":
        return {"status": "skipped", "reason": "Bot paused"}

    # Check paused pairs
    paused_pairs_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='paused_pairs'")
    if paused_pairs_row and pair in paused_pairs_row["value"].split(","):
        return {"status": "skipped", "reason": f"{pair} is paused"}

    # Check duplicate prevention (general)
    dup_hours_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='duplicate_prevention_hours'")
    dup_hours = int(dup_hours_row["value"]) if dup_hours_row else 4
    is_dup = await check_duplicate_signal(db, pair, dup_hours)
    if is_dup:
        return {"status": "skipped", "reason": "Duplicate signal within prevention window"}

    # Check if Precision signal already sent this Kill Zone session for same pair
    precision_this_session = await db.fetchrow(
        """SELECT id FROM signals
           WHERE pair=%s AND signal_type='precision'
           AND sent_at > NOW() - INTERVAL '4 hours'
           ORDER BY sent_at DESC LIMIT 1""",
        (pair,),
    )
    if precision_this_session:
        return {"status": "skipped", "reason": "Precision signal already sent this session for same pair"}

    # ── GATE 1: HTF Daily Bias ──
    daily_candles = candles.get("Daily", candles.get("D", []))
    bias = await get_daily_bias(daily_candles)
    if bias == "NEUTRAL":
        await _log_rejection(db, pair, None, 0, 1, "No clear Daily bias")
        return {"status": "rejected", "gate": 1, "reason": "No clear Daily bias"}

    direction = "LONG" if bias == "BULLISH" else "SHORT"

    # ── GATE 2: H4 POI (accepts up to 2 wick touches) ──
    h4_candles = candles.get("H4", [])
    poi = await identify_poi_relaxed(h4_candles)
    if not poi["found"] or poi.get("touch_count", 0) > 2:
        await _log_rejection(db, pair, direction, 0, 2, "No valid H4 POI or POI too tested")
        return {"status": "rejected", "gate": 2, "reason": "No valid H4 POI"}

    # ── GATE 3: Kill Zone Active ──
    kz = await detect_kill_zone(current_time)
    if not kz["flow_active"]:
        await _log_rejection(db, pair, direction, 0, 3, "Outside Flow Kill Zone")
        return {"status": "rejected", "gate": 3, "reason": "Outside Kill Zone"}

    # ── GATE 4: M15 CHoCH with FVG ──
    m15_candles = candles.get("M15", [])
    choch = await detect_structure_shift(m15_candles)
    fvg = await detect_displacement_and_fvg(m15_candles)
    if not choch["confirmed"] or not fvg.get("found"):
        await _log_rejection(db, pair, direction, 0, 4, "No M15 CHoCH or FVG")
        return {"status": "rejected", "gate": 4, "reason": "No M15 CHoCH or FVG"}

    # ── GATE 5: R:R >= 1:2 ──
    entry_price = fvg["ce"] if fvg.get("found") else poi["price"]
    sweep_wick = poi.get("low", poi["price"]) if direction == "LONG" else poi.get("high", poi["price"])

    # Build TP candidates from session levels
    h1_candles = candles.get("H1", [])
    tp_candidates = _build_flow_tp_candidates(direction, entry_price, h1_candles)

    levels = await compute_flow_levels(pair, direction, entry_price, sweep_wick, tp_candidates, db)
    if not levels:
        await _log_rejection(db, pair, direction, 0, 5, "R:R below 1:2")
        return {"status": "rejected", "gate": 5, "reason": "R:R below 1:2"}

    # ── ALL 5 GATES PASSED — Score the setup ──
    scoring_context = {
        "daily_bias_aligned": True,
        "poi_touch_count": poi.get("touch_count", 0),
        "in_kill_zone": True,
        "fvg_confirmed": fvg.get("found", False),
        "choch_confirmed": choch["confirmed"],
    }

    score_result = await score_flow_setup(scoring_context, db)

    if not score_result["passed"]:
        await _log_rejection(db, pair, direction, score_result["score"], 0,
                             f"Score {score_result['score']}/8 below minimum {score_result['min_required']}")
        return {"status": "rejected", "gate": 0, "reason": f"Score {score_result['score']}/8 below minimum",
                "score": score_result["score"]}

    return {
        "status": "passed",
        "signal_type": "flow",
        "pair": pair,
        "direction": direction,
        "levels": levels,
        "score": score_result["score"],
        "max_score": 8,
        "kill_zone": kz["session"],
        "daily_bias": bias,
        "poi_type": poi["type"],
        "poi_price": poi["price"],
        "poi_touch_count": poi.get("touch_count", 0),
        "choch_type": choch.get("type"),
        "fvg": fvg,
    }


def _build_flow_tp_candidates(direction: str, entry: float, h1_candles: list) -> list:
    """Build TP candidates from session high/low levels."""
    if not h1_candles:
        risk = abs(entry * 0.002)
        if direction == "LONG":
            return [entry + risk * 2, entry + risk * 4]
        return [entry - risk * 2, entry - risk * 4]

    recent = h1_candles[-24:]
    session_high = max(c["high"] for c in recent)
    session_low = min(c["low"] for c in recent)

    if direction == "LONG":
        candidates = [session_high]
        candidates.append(session_high + (session_high - session_low) * 0.5)
    else:
        candidates = [session_low]
        candidates.append(session_low - (session_high - session_low) * 0.5)

    return candidates[:2]


async def _log_rejection(db, pair: str, direction: str, score: int, gate: int, reason: str):
    """Log rejected setup to rejected_setups table."""
    try:
        await db.execute(
            """INSERT INTO rejected_setups (engine_type, pair, direction, score, gate_failed, rejection_reason)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            ("flow", pair, direction, score, gate, reason),
        )
    except Exception as e:
        logger.error("Failed to log flow rejection for %s: %s", pair, e)
