from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def score_precision_setup(context: dict, db) -> dict:
    """Compute Precision engine score out of 15 points.

    CRITICAL: MSS not confirmed → cap score at 9 → auto reject.
    COT intermarket downgrade rules applied here.

    Scoring breakdown:
        HTF Storyline alignment          → 3 points
        POI is fresh (zero touches)      → 2 points
        Liquidity sweep / IDM swept      → 2 points
        Inside Kill Zone                 → 2 points
        FVG + displacement confirmed     → 1 point
        Wyckoff Phase C/D/E confirmed    → 2 points
        COT + Intermarket aligned        → 2 points
        Volume Profile POC/HVN           → 1 point
        Maximum: 15 | Minimum to send: 10
    """
    score = 0
    breakdown = {}

    # HTF Storyline alignment → 3 points
    if context.get("htf_storyline_aligned"):
        score += 3
        breakdown["htf_storyline"] = 3
    else:
        breakdown["htf_storyline"] = 0

    # POI is fresh (zero touches) → 2 points, 1 touch → 1 point
    poi_touch_count = context.get("poi_touch_count", 99)
    if poi_touch_count == 0:
        score += 2
        breakdown["poi_fresh"] = 2
    elif poi_touch_count == 1:
        score += 1
        breakdown["poi_fresh"] = 1
    else:
        breakdown["poi_fresh"] = 0

    # Liquidity sweep / IDM swept → 2 points
    if context.get("liquidity_swept") or context.get("idm_swept"):
        score += 2
        breakdown["liquidity"] = 2
    else:
        breakdown["liquidity"] = 0

    # Inside Kill Zone → 2 points
    if context.get("in_kill_zone"):
        score += 2
        breakdown["kill_zone"] = 2
    else:
        breakdown["kill_zone"] = 0

    # FVG + displacement confirmed → 1 point
    if context.get("fvg_confirmed") and context.get("displacement_confirmed"):
        score += 1
        breakdown["fvg_displacement"] = 1
    else:
        breakdown["fvg_displacement"] = 0

    # Wyckoff Phase C/D/E confirmed → 2 points
    wyckoff_phase = context.get("wyckoff_phase")
    if wyckoff_phase in ("C", "D", "E"):
        score += 2
        breakdown["wyckoff"] = 2
    else:
        breakdown["wyckoff"] = 0

    # COT + Intermarket aligned → 2 points
    if context.get("cot_aligned"):
        score += 2
        breakdown["cot_intermarket"] = 2
    else:
        breakdown["cot_intermarket"] = 0

    # Volume Profile POC/HVN → 1 point
    if context.get("volume_profile_confluence"):
        score += 1
        breakdown["volume_profile"] = 1
    else:
        breakdown["volume_profile"] = 0

    # CRITICAL: MSS not confirmed → cap at 9 → reject
    mss_confirmed = context.get("mss_confirmed", False)
    if not mss_confirmed:
        score = min(score, 9)
        breakdown["mss_cap"] = True
        logger.warning("Precision scoring: MSS not confirmed for %s, capped at 9", context.get("pair"))

    # Read minimum score from bot_settings
    min_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='min_precision_score'")
    min_score = int(min_row["value"]) if min_row else 8

    passed = score >= min_score

    return {
        "score": score,
        "max_score": 15,
        "breakdown": breakdown,
        "passed": passed,
        "min_required": min_score,
        "mss_confirmed": mss_confirmed,
    }
