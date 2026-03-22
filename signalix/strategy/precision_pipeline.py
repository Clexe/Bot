from datetime import datetime
from signalix.config import PAIR_CONFIG
from signalix.strategy.cot_filter import get_cot_bias, apply_intermarket_downgrade
from signalix.strategy.wyckoff import detect_wyckoff_phase
from signalix.strategy.storyline import build_storyline, check_storyline_alignment, store_storyline
from signalix.strategy.oc_detector import detect_oc_levels, store_oc_levels
from signalix.strategy.order_blocks import detect_order_blocks, store_order_blocks
from signalix.strategy.detectors import (
    detect_htf_rejection, detect_displacement_and_fvg,
    detect_liquidity_sweep, detect_structure_shift,
    detect_kill_zone, get_asian_session_levels,
)
from signalix.strategy.volume_profile import compute_volume_profile, check_volume_confluence
from signalix.strategy.precision_scoring import score_precision_setup
from signalix.strategy.calculator import compute_precision_levels
from signalix.utils.helpers import check_duplicate_signal
from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def run_precision_pipeline(pair: str, candles: dict, db) -> dict:
    """Full 7-gate Precision engine orchestrator.

    Sequential gates with early rejection.
    Logs gate_failed number and reason to rejected_setups table.
    Reads all settings from bot_settings table on every cycle.
    """
    current_time = datetime.utcnow()
    config = PAIR_CONFIG.get(pair, {})
    requires_cot = config.get("cot", False)

    # Check if engine is paused
    paused_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='precision_signals_enabled'")
    if paused_row and paused_row["value"] == "false":
        return {"status": "skipped", "reason": "Precision engine paused"}

    # Check if bot is paused
    bot_paused = await db.fetchrow("SELECT value FROM bot_settings WHERE key='bot_paused'")
    if bot_paused and bot_paused["value"] == "true":
        return {"status": "skipped", "reason": "Bot paused"}

    # Check paused pairs
    paused_pairs_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='paused_pairs'")
    if paused_pairs_row and pair in paused_pairs_row["value"].split(","):
        return {"status": "skipped", "reason": f"{pair} is paused"}

    # Check duplicate prevention
    dup_hours_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='duplicate_prevention_hours'")
    dup_hours = int(dup_hours_row["value"]) if dup_hours_row else 4
    is_dup = await check_duplicate_signal(db, pair, dup_hours)
    if is_dup:
        return {"status": "skipped", "reason": "Duplicate signal within prevention window"}

    # ── GATE 1: COT extreme positioning (XAUUSD/XAGUSD only) ──
    cot_result = {"passed": True, "bias": None, "percentile": None}
    if requires_cot:
        cot_result = await get_cot_bias(pair, db)
        if not cot_result["passed"]:
            await _log_rejection(db, pair, None, 0, 1, cot_result.get("reject_reason", "COT not extreme"))
            return {"status": "rejected", "gate": 1, "reason": cot_result.get("reject_reason")}
    # EURUSD, GBPUSD, USDJPY, BTCUSDT, ETHUSDT skip Gate 1

    # ── GATE 2: Daily MSS confirmed with displacement ──
    daily_candles = candles.get("Daily", candles.get("D", []))
    if len(daily_candles) < 5:
        await _log_rejection(db, pair, None, 0, 2, "Insufficient Daily data")
        return {"status": "rejected", "gate": 2, "reason": "Insufficient Daily data"}

    daily_shift = await detect_structure_shift(daily_candles)
    if not daily_shift["confirmed"]:
        await _log_rejection(db, pair, None, 0, 2, "No Daily MSS/CHoCH confirmed")
        return {"status": "rejected", "gate": 2, "reason": "No Daily MSS confirmed"}

    direction = daily_shift["direction"]

    # ── GATE 3: Fresh H4 POI (OB or FVG, zero wick touches) ──
    h4_candles = candles.get("H4", [])
    h4_obs = await detect_order_blocks(h4_candles, "H4")
    await store_order_blocks(db, pair, h4_obs)

    fresh_poi = None
    for ob in h4_obs:
        if ob["validity_status"] == "valid" and ob["touch_count"] == 0:
            if (direction == "LONG" and ob["ob_type"] == "bullish") or \
               (direction == "SHORT" and ob["ob_type"] == "bearish"):
                fresh_poi = ob
                break

    if not fresh_poi:
        h4_fvg = await detect_displacement_and_fvg(h4_candles)
        if h4_fvg.get("found"):
            fresh_poi = {
                "ob_type": "FVG", "ob_midpoint": h4_fvg["ce"],
                "ob_high": h4_fvg["high"], "ob_low": h4_fvg["low"],
                "touch_count": 0,
            }

    if not fresh_poi:
        await _log_rejection(db, pair, direction, 0, 3, "No fresh H4 POI with zero touches")
        return {"status": "rejected", "gate": 3, "reason": "No fresh H4 POI"}

    # ── GATE 4: Kill Zone active ──
    kz = await detect_kill_zone(current_time)
    if not kz["precision_active"]:
        await _log_rejection(db, pair, direction, 0, 4, "Outside Precision Kill Zone")
        return {"status": "rejected", "gate": 4, "reason": "Outside Kill Zone"}

    # ── GATE 5: Judas Swing detected ──
    h1_candles = candles.get("H1", [])
    asian_levels = await get_asian_session_levels(h1_candles)
    m15_candles = candles.get("M15", [])

    judas = await detect_liquidity_sweep(m15_candles, asian_levels)
    if not judas["detected"] or not judas.get("fvg_created"):
        await _log_rejection(db, pair, direction, 0, 5, "No Judas Swing with FVG")
        return {"status": "rejected", "gate": 5, "reason": "No Judas Swing detected"}

    # ── GATE 6: M15 CHoCH with displacement confirmed ──
    m15_shift = await detect_structure_shift(m15_candles)
    if not m15_shift["confirmed"]:
        await _log_rejection(db, pair, direction, 0, 6, "No M15 CHoCH/BOS confirmed")
        return {"status": "rejected", "gate": 6, "reason": "No M15 CHoCH confirmed"}

    m15_fvg = await detect_displacement_and_fvg(m15_candles)

    # ── GATE 7: R:R >= 1:3 to TP1 ──
    entry_price = m15_fvg["ce"] if m15_fvg.get("found") else fresh_poi["ob_midpoint"]
    sweep_wick = judas.get("sweep_wick", fresh_poi["ob_low"] if direction == "LONG" else fresh_poi["ob_high"])

    # Build TP candidates from MSNR levels and ERL
    htf_levels_db = await db.fetch(
        "SELECT * FROM oc_levels WHERE pair=%s AND freshness_status='fresh' ORDER BY level_price", (pair,)
    )
    tp_candidates = _build_tp_candidates(direction, entry_price, htf_levels_db)

    levels = await compute_precision_levels(pair, direction, entry_price, sweep_wick, tp_candidates, db)
    if not levels:
        await _log_rejection(db, pair, direction, 0, 7, "R:R below minimum for Precision")
        return {"status": "rejected", "gate": 7, "reason": "R:R below 1:3"}

    # ── ALL 7 GATES PASSED — Score the setup ──
    # Build HTF data for scoring
    all_oc_levels = []
    for tf in ("M", "W", "D", "H4"):
        tf_candles = candles.get(tf, candles.get({"M": "Monthly", "W": "Weekly", "D": "Daily"}.get(tf, tf), []))
        if tf_candles:
            oc = await detect_oc_levels(tf_candles, tf)
            all_oc_levels.extend(oc)
            await store_oc_levels(db, pair, oc)

    storyline = await build_storyline(pair, entry_price, all_oc_levels, db)
    wyckoff = await detect_wyckoff_phase(daily_candles, pair, db)
    storyline_aligned = await check_storyline_alignment(storyline, cot_result, wyckoff)
    await store_storyline(db, pair, storyline, wyckoff.get("phase"), cot_result.get("bias"))

    # Volume profile
    vp = await compute_volume_profile(h4_candles)
    vp_confluence = await check_volume_confluence(entry_price, vp)

    scoring_context = {
        "pair": pair,
        "htf_storyline_aligned": storyline_aligned,
        "poi_touch_count": fresh_poi["touch_count"],
        "liquidity_swept": judas["detected"],
        "idm_swept": judas["detected"],
        "in_kill_zone": True,
        "fvg_confirmed": m15_fvg.get("found", False),
        "displacement_confirmed": m15_fvg.get("displacement", False),
        "wyckoff_phase": wyckoff.get("phase"),
        "cot_aligned": cot_result.get("bias") not in (None, "NEUTRAL") if requires_cot else False,
        "volume_profile_confluence": vp_confluence.get("confluence", False),
        "mss_confirmed": daily_shift["confirmed"],
    }

    score_result = await score_precision_setup(scoring_context, db)

    # Apply intermarket downgrade
    if requires_cot:
        score_result["score"] = await apply_intermarket_downgrade(
            pair, direction, score_result["score"], db
        )

    if not score_result["passed"]:
        await _log_rejection(db, pair, direction, score_result["score"], 0,
                             f"Score {score_result['score']}/15 below minimum {score_result['min_required']}")
        return {"status": "rejected", "gate": 0, "reason": f"Score {score_result['score']}/15 below minimum",
                "score": score_result["score"]}

    return {
        "status": "passed",
        "signal_type": "precision",
        "pair": pair,
        "direction": direction,
        "levels": levels,
        "score": score_result["score"],
        "max_score": 15,
        "kill_zone": kz["session"],
        "cot_bias": cot_result.get("bias"),
        "cot_percentile": cot_result.get("percentile"),
        "wyckoff_phase": wyckoff.get("phase"),
        "htf_bias": storyline["direction"],
        "poi_type": fresh_poi["ob_type"],
        "poi_price": fresh_poi["ob_midpoint"],
        "poi_touch_count": fresh_poi["touch_count"],
        "judas_swing": True,
        "mss_confirmed": True,
        "volume_profile_confluence": vp_confluence.get("confluence", False),
        "storyline": storyline,
        "fvg": m15_fvg,
        "structure_shift": m15_shift,
    }


def _build_tp_candidates(direction: str, entry: float, htf_levels: list) -> list:
    """Build TP candidates from fresh HTF levels."""
    candidates = []
    for lvl in htf_levels:
        lp = float(lvl["level_price"])
        if direction == "LONG" and lp > entry:
            candidates.append(lp)
        elif direction == "SHORT" and lp < entry:
            candidates.append(lp)

    if direction == "LONG":
        candidates.sort()
    else:
        candidates.sort(reverse=True)

    if not candidates:
        risk = abs(entry * 0.003)
        if direction == "LONG":
            candidates = [entry + risk * 3, entry + risk * 5, entry + risk * 8]
        else:
            candidates = [entry - risk * 3, entry - risk * 5, entry - risk * 8]

    return candidates[:3]


async def _log_rejection(db, pair: str, direction: str, score: int, gate: int, reason: str):
    """Log rejected setup to rejected_setups table."""
    try:
        await db.execute(
            """INSERT INTO rejected_setups (engine_type, pair, direction, score, gate_failed, rejection_reason)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            ("precision", pair, direction, score, gate, reason),
        )
    except Exception as e:
        logger.error("Failed to log precision rejection for %s: %s", pair, e)
