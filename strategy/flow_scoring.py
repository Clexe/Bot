from utils.logger import get_logger

logger = get_logger(__name__)


async def score_flow_setup(context: dict, db) -> dict:
    """Compute Flow engine score out of 8 points.

    No COT, no Wyckoff, no Volume Profile.
    POI with 1 touch scores 1 point instead of 2.

    Scoring breakdown:
        HTF Daily bias alignment         → 2 points
        POI reasonably fresh (≤1 touch)  → 2 points (1 if 1 touch)
        Inside Kill Zone                 → 2 points
        FVG + CHoCH confirmed            → 2 points
        Maximum: 8 | Minimum to send: 6
    """
    score = 0
    breakdown = {}

    # HTF Daily bias alignment → 2 points
    if context.get("daily_bias_aligned"):
        score += 2
        breakdown["daily_bias"] = 2
    else:
        breakdown["daily_bias"] = 0

    # POI reasonably fresh → 2 points (0 touches), 1 point (1 touch)
    poi_touch_count = context.get("poi_touch_count", 99)
    if poi_touch_count == 0:
        score += 2
        breakdown["poi_fresh"] = 2
    elif poi_touch_count == 1:
        score += 1
        breakdown["poi_fresh"] = 1
    else:
        breakdown["poi_fresh"] = 0

    # Inside Kill Zone → 2 points
    if context.get("in_kill_zone"):
        score += 2
        breakdown["kill_zone"] = 2
    else:
        breakdown["kill_zone"] = 0

    # FVG + CHoCH confirmed → 2 points
    if context.get("fvg_confirmed") and context.get("choch_confirmed"):
        score += 2
        breakdown["fvg_choch"] = 2
    else:
        breakdown["fvg_choch"] = 0

    # Read minimum score from bot_settings
    min_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='min_flow_score'")
    min_score = int(min_row["value"]) if min_row else 5

    passed = score >= min_score

    return {
        "score": score,
        "max_score": 8,
        "breakdown": breakdown,
        "passed": passed,
        "min_required": min_score,
    }
