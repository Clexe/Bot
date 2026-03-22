from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def generate_signal(pipeline_result: dict, db) -> dict:
    """Assemble final signal dict for both engines.

    Includes signal_type field ('precision' or 'flow'),
    max_score (15 or 8), and all confluence data.
    Persists to signals table and returns signal record.
    """
    if pipeline_result.get("status") != "passed":
        return None

    signal_type = pipeline_result["signal_type"]
    levels = pipeline_result["levels"]

    signal = {
        "signal_type": signal_type,
        "pair": pipeline_result["pair"],
        "direction": pipeline_result["direction"],
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp1": levels["tp1"],
        "tp2": levels.get("tp2"),
        "tp3": levels.get("tp3"),
        "rr_tp1": levels.get("rr_tp1"),
        "rr_tp2": levels.get("rr_tp2"),
        "rr_tp3": levels.get("rr_tp3"),
        "score": pipeline_result["score"],
        "max_score": pipeline_result["max_score"],
        "kill_zone": pipeline_result.get("kill_zone"),
        "htf_bias": pipeline_result.get("htf_bias") or pipeline_result.get("daily_bias"),
        "poi_type": pipeline_result.get("poi_type"),
        "poi_price": pipeline_result.get("poi_price"),
        "poi_touch_count": pipeline_result.get("poi_touch_count", 0),
    }

    # Precision-specific fields
    if signal_type == "precision":
        signal["cot_bias"] = pipeline_result.get("cot_bias")
        signal["cot_percentile"] = pipeline_result.get("cot_percentile")
        signal["wyckoff_phase"] = pipeline_result.get("wyckoff_phase")
        signal["judas_swing"] = pipeline_result.get("judas_swing", False)
        signal["mss_confirmed"] = pipeline_result.get("mss_confirmed", False)
        signal["volume_profile_confluence"] = pipeline_result.get("volume_profile_confluence", False)
    else:
        signal["cot_bias"] = None
        signal["cot_percentile"] = None
        signal["wyckoff_phase"] = None
        signal["judas_swing"] = False
        signal["mss_confirmed"] = False
        signal["volume_profile_confluence"] = False

    # Persist to database
    try:
        await db.execute(
            """INSERT INTO signals
               (signal_type, pair, direction, entry, sl, tp1, tp2, tp3,
                rr_tp1, rr_tp2, rr_tp3, score, max_score,
                cot_bias, cot_percentile, wyckoff_phase, htf_bias,
                poi_type, poi_price, poi_touch_count, judas_swing,
                kill_zone, mss_confirmed, volume_profile_confluence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (signal["signal_type"], signal["pair"], signal["direction"],
             signal["entry"], signal["sl"], signal["tp1"], signal["tp2"], signal["tp3"],
             signal["rr_tp1"], signal["rr_tp2"], signal["rr_tp3"],
             signal["score"], signal["max_score"],
             signal["cot_bias"], signal["cot_percentile"], signal["wyckoff_phase"],
             signal["htf_bias"], signal["poi_type"], signal["poi_price"],
             signal["poi_touch_count"], signal["judas_swing"],
             signal["kill_zone"], signal["mss_confirmed"], signal["volume_profile_confluence"]),
        )

        row = await db.fetchrow(
            "SELECT id, sent_at FROM signals WHERE pair=%s ORDER BY id DESC LIMIT 1",
            (signal["pair"],),
        )
        if row:
            signal["id"] = row["id"]
            signal["sent_at"] = row["sent_at"]
    except Exception as e:
        logger.error("Failed to persist signal for %s: %s", signal["pair"], e)

    return signal
