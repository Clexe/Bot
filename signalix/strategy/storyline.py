from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def build_storyline(pair: str, price: float, htf_levels: list, db) -> dict:
    """Build HTF Storyline with Monthly → Weekly → Daily OC level bias.

    DOL = next fresh HTF level.
    Dealing range: Premium above 50%, Discount below 50%, OTE 62–79%.
    """
    monthly = [l for l in htf_levels if l["timeframe"] in ("M", "Monthly") and l.get("freshness_status") == "fresh"]
    weekly = [l for l in htf_levels if l["timeframe"] in ("W", "Weekly") and l.get("freshness_status") == "fresh"]
    daily = [l for l in htf_levels if l["timeframe"] in ("D", "Daily") and l.get("freshness_status") == "fresh"]

    all_fresh = monthly + weekly + daily
    if not all_fresh:
        return _neutral(pair)

    above = sorted([l for l in all_fresh if float(l["level_price"]) > price], key=lambda x: float(x["level_price"]))
    below = sorted([l for l in all_fresh if float(l["level_price"]) < price], key=lambda x: float(x["level_price"]), reverse=True)

    if not above and not below:
        return _neutral(pair)

    if not above or not below:
        if above:
            return {
                "pair": pair, "direction": "BEARISH",
                "origin_level": price, "target_level": float(above[0]["level_price"]),
                "dol_target": float(above[0]["level_price"]), "dealing_range_position": None,
            }
        return {
            "pair": pair, "direction": "BULLISH",
            "origin_level": float(below[0]["level_price"]), "target_level": price,
            "dol_target": float(below[0]["level_price"]), "dealing_range_position": None,
        }

    nearest_support = float(below[0]["level_price"])
    nearest_resistance = float(above[0]["level_price"])
    range_size = nearest_resistance - nearest_support
    if range_size == 0:
        return _neutral(pair)

    position_pct = (price - nearest_support) / range_size * 100

    if position_pct <= 50:
        zone = "discount"
    elif 62 <= position_pct <= 79:
        zone = "OTE"
    else:
        zone = "premium"

    if position_pct < 50:
        direction = "BULLISH"
        dol = nearest_resistance
    elif position_pct > 50:
        direction = "BEARISH"
        dol = nearest_support
    else:
        direction = "NEUTRAL"
        dol = None

    return {
        "pair": pair, "direction": direction,
        "origin_level": nearest_support if direction == "BULLISH" else nearest_resistance,
        "target_level": nearest_resistance if direction == "BULLISH" else nearest_support,
        "dol_target": dol, "dealing_range_position": round(position_pct, 1), "zone": zone,
    }


async def check_storyline_alignment(storyline: dict, cot_result: dict, wyckoff_result: dict) -> bool:
    """Verify storyline aligns with COT bias and Wyckoff phase."""
    if storyline["direction"] == "NEUTRAL":
        return False

    if cot_result and cot_result.get("bias"):
        cot_bias = cot_result["bias"]
        if cot_bias != "NEUTRAL" and cot_bias != storyline["direction"]:
            return False

    if wyckoff_result and wyckoff_result.get("phase") in ("C", "D", "E"):
        wy_dir = wyckoff_result.get("direction")
        if wy_dir and wy_dir != storyline["direction"]:
            return False

    return True


async def store_storyline(db, pair: str, storyline: dict, wyckoff_phase: str = None, cot_bias: str = None):
    """Store or update active storyline in database."""
    try:
        await db.execute("UPDATE storylines SET is_active=false WHERE pair=%s AND is_active=true", (pair,))
        await db.execute(
            """INSERT INTO storylines (pair, direction, origin_level, target_level, dol_target, wyckoff_phase, cot_bias)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (pair, storyline["direction"], storyline.get("origin_level"),
             storyline.get("target_level"), storyline.get("dol_target"),
             wyckoff_phase, cot_bias),
        )
    except Exception as e:
        logger.error("Failed to store storyline for %s: %s", pair, e)


def _neutral(pair: str) -> dict:
    return {
        "pair": pair, "direction": "NEUTRAL",
        "origin_level": None, "target_level": None,
        "dol_target": None, "dealing_range_position": None,
    }
