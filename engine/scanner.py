"""Top-level scan loop — called by APScheduler every 2 minutes."""

from datetime import datetime
from strategy.detectors import detect_kill_zone
from engine.pipeline import run_pair_pipeline
from database.users import load_users_async
from utils.logger import get_logger

logger = get_logger(__name__)


async def run_scan_cycle(db, telegram, bybit, deriv):
    """
    Main scan entry point.
    1. Check bot_paused setting
    2. Check kill zone — skip if outside
    3. Collect union of all user watchlists
    4. Run pipeline per pair
    """
    try:
        # ── Check bot paused ──
        paused_row = await db.fetchrow(
            "SELECT value FROM bot_settings WHERE key = 'bot_paused'"
        )
        if paused_row and paused_row["value"] == "true":
            logger.debug("Bot is paused — skipping scan")
            return

        # ── Check kill zone ──
        now_utc = datetime.utcnow()
        in_kz, kz_name = await detect_kill_zone(now_utc)
        if not in_kz or kz_name not in {"London", "New York"}:
            logger.debug("Outside kill zone (%s) — skipping scan", kz_name or "none")
            return

        # ── Check paused pairs ──
        paused_pairs_row = await db.fetchrow(
            "SELECT value FROM bot_settings WHERE key = 'paused_pairs'"
        )
        paused_pairs = set()
        if paused_pairs_row and paused_pairs_row["value"]:
            paused_pairs = {p.strip().upper() for p in paused_pairs_row["value"].split(",") if p.strip()}

        # ── Check min score setting ──
        min_score_row = await db.fetchrow(
            "SELECT value FROM bot_settings WHERE key = 'min_signal_score'"
        )
        min_score = int(min_score_row["value"]) if min_score_row else 7

        # ── Collect all pairs from active users ──
        users = await load_users_async(db)
        all_pairs = set()
        for settings in users.values():
            for pair in settings.get("pairs", []):
                if pair not in paused_pairs:
                    all_pairs.add(pair)

        if not all_pairs:
            logger.debug("No pairs to scan")
            return

        logger.info("Scan cycle started [%s] — %d pairs to check: %s",
                     kz_name, len(all_pairs), ", ".join(sorted(all_pairs)))

        # ── Run pipeline per pair ──
        signals_fired = 0
        for pair in sorted(all_pairs):
            try:
                result = await run_pair_pipeline(pair, db, telegram, bybit, deriv)
                if result:
                    signals_fired += 1
            except Exception as e:
                logger.error("Scan failed for %s: %s", pair, e)

        logger.info("Scan cycle complete — %d signal(s) fired from %d pairs",
                     signals_fired, len(all_pairs))

    except Exception as e:
        logger.error("Scan cycle error: %s", e, exc_info=True)
