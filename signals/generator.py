from utils.logger import get_logger

logger = get_logger(__name__)


async def create_signal(trade: dict, db) -> dict | None:
    """Persist signal to database and return enriched trade dict."""
    try:
        signal_id = await db.fetchval(
            """INSERT INTO signals
               (pair, direction, setup_type, entry_price, sl_price, tp_price,
                rr_ratio, bias, session, confluences, status)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'open')
               RETURNING id""",
            trade["pair"], trade["direction"], trade["setup_type"],
            trade["entry"], trade["sl"], trade["tp"], trade["rr"],
            trade.get("bias", ""), trade.get("session", ""),
            "\n".join(trade.get("confluences", [])),
        )
        trade["id"] = signal_id
        return trade
    except Exception as e:
        logger.error("Failed to create signal: %s", e)
        return None


def format_signal(trade: dict) -> str:
    """Format signal as Telegram HTML message."""
    emoji = "\U0001f7e2" if trade["direction"] == "LONG" else "\U0001f534"
    pair = trade.get("display_pair", trade["pair"])
    setup = trade["setup_type"].title()

    lines = [
        f"{emoji} <b>{pair} {trade['direction']}</b>",
        f"Setup: {setup}",
        "",
        f"Entry: {trade['entry']}",
        f"SL: {trade['sl']} ({trade['sl_pips']} pips)",
        f"TP: {trade['tp']} ({trade['tp_pips']} pips)",
        f"R:R: 1:{trade['rr']}",
    ]

    if trade.get("confluences"):
        lines.append("")
        for c in trade["confluences"]:
            lines.append(f"  ✅ {c}")

    if trade.get("session"):
        lines.append(f"\nSession: {trade['session']}")

    return "\n".join(lines)
