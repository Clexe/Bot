"""Top-level scan loop — full port of old scanner_loop with all features.

Features:
- Kill zone + session + market hours + news blackout filtering
- Circuit breaker check
- Per-user timeframe grouping
- Correlation filter
- Adaptive scan interval
- Signal outcome checking with trailing stops
- Signal expiry
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from strategy.detectors import detect_kill_zone
from engine.pipeline import run_pair_pipeline, fetch_current_price
from database.users import load_users_async, DEFAULT_SETTINGS
from database.signal_queries import get_open_signals_async
from filters import is_in_session, is_market_open, is_news_blackout
from correlation import check_correlation
from drawdown import check_circuit_breaker, record_trade_result, set_open_trade_count
from config import (
    settings, KNOWN_SYMBOLS, FOREX_BASES, ADAPTIVE_SCAN_INTERVALS,
    SCAN_LOOP_INTERVAL, SCAN_ERROR_INTERVAL, SIGNAL_MAX_AGE_HOURS,
    AUTO_WIN_PIPS, BE_BUFFER_PIPS, get_pip_value,
)
from utils.logger import get_logger

logger = get_logger(__name__)


async def check_signal_outcomes(db, bybit, deriv):
    """Check open signals against current prices — trailing stop logic.

    TP progression:
      TP1 hit → move SL to breakeven
      TP2 hit → trail SL to TP1
      TP3 or AUTO_WIN_PIPS → full close as WIN
    """
    open_signals = await get_open_signals_async(db)

    # Auto-expire old signals
    now_utc = datetime.now(timezone.utc)
    max_age = timedelta(hours=SIGNAL_MAX_AGE_HOURS)
    active_signals = []
    for sig in open_signals:
        created = sig.get('created_at')
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if now_utc - created > max_age:
                await db.execute(
                    "UPDATE signal_history SET outcome='EXPIRED', pnl_pips=0, closed_at=NOW() WHERE id=%s",
                    (sig['id'],),
                )
                logger.info("Signal #%d %s %s auto-expired after %dh",
                            sig['id'], sig['direction'], sig['pair'], SIGNAL_MAX_AGE_HOURS)
                continue
        active_signals.append(sig)

    set_open_trade_count(len(active_signals))
    if not active_signals:
        return

    # Get unique pairs
    pairs = list({s['pair'] for s in active_signals})

    for pair in pairs:
        price = await fetch_current_price(pair, bybit, deriv)
        if price is None:
            continue

        pip_val = get_pip_value(pair)

        for sig in active_signals:
            if sig['pair'] != pair:
                continue

            entry = float(sig['entry_price'])
            tp = float(sig['tp_price'])
            sl = float(sig['sl_price'])
            direction = sig['direction']
            tp_stage = sig.get('tp_stage', 0)

            outcome = None
            pnl_pips = 0
            risk_dist = abs(entry - sl)

            if direction in ("BUY", "LONG"):
                tp1 = entry + risk_dist
                tp3 = entry + risk_dist * 3
                be_buffer = BE_BUFFER_PIPS / pip_val if pip_val > 0 else 0

                effective_sl = sl
                if tp_stage >= 2:
                    effective_sl = tp1
                elif tp_stage >= 1:
                    effective_sl = entry + be_buffer

                current_pips = (price - entry) * pip_val

                if price >= tp3 or current_pips >= AUTO_WIN_PIPS:
                    outcome = "WIN"
                    pnl_pips = current_pips
                elif price >= tp and tp_stage < 2:
                    await db.execute(
                        "UPDATE signal_history SET tp_stage=2 WHERE id=%s", (sig['id'],))
                    logger.info("Signal #%d %s %s TP2 hit — trailing SL to TP1",
                                sig['id'], direction, pair)
                    continue
                elif price >= tp1 and tp_stage < 1:
                    await db.execute(
                        "UPDATE signal_history SET tp_stage=1 WHERE id=%s", (sig['id'],))
                    logger.info("Signal #%d %s %s TP1 hit — SL moved to breakeven",
                                sig['id'], direction, pair)
                    continue
                elif price <= effective_sl:
                    outcome = "LOSS" if tp_stage == 0 else "WIN"
                    pnl_pips = (effective_sl - entry) * pip_val

            elif direction in ("SELL", "SHORT"):
                tp1 = entry - risk_dist
                tp3 = entry - risk_dist * 3
                be_buffer = BE_BUFFER_PIPS / pip_val if pip_val > 0 else 0

                effective_sl = sl
                if tp_stage >= 2:
                    effective_sl = tp1
                elif tp_stage >= 1:
                    effective_sl = entry - be_buffer

                current_pips = (entry - price) * pip_val

                if price <= tp3 or current_pips >= AUTO_WIN_PIPS:
                    outcome = "WIN"
                    pnl_pips = current_pips
                elif price <= tp and tp_stage < 2:
                    await db.execute(
                        "UPDATE signal_history SET tp_stage=2 WHERE id=%s", (sig['id'],))
                    logger.info("Signal #%d %s %s TP2 hit — trailing SL to TP1",
                                sig['id'], direction, pair)
                    continue
                elif price <= tp1 and tp_stage < 1:
                    await db.execute(
                        "UPDATE signal_history SET tp_stage=1 WHERE id=%s", (sig['id'],))
                    logger.info("Signal #%d %s %s TP1 hit — SL moved to breakeven",
                                sig['id'], direction, pair)
                    continue
                elif price >= effective_sl:
                    outcome = "LOSS" if tp_stage == 0 else "WIN"
                    pnl_pips = (entry - effective_sl) * pip_val

            if outcome:
                await db.execute(
                    "UPDATE signal_history SET outcome=%s, pnl_pips=%s, close_price=%s, closed_at=NOW() WHERE id=%s",
                    (outcome, round(pnl_pips, 1), price, sig['id']),
                )
                logger.info("Signal #%d %s %s closed: %s (%.1f pips) [stage=%d]",
                            sig['id'], direction, pair, outcome, pnl_pips, tp_stage)
                record_trade_result(pnl_pips, outcome == "WIN")


async def run_scan_cycle(db, telegram, bybit, deriv):
    """Main scan entry point — full port of old scanner_loop iteration.

    1. Check bot_paused
    2. Check circuit breaker
    3. Check kill zone
    4. Check signal outcomes (trailing stops)
    5. Collect user pairs with session/market/news filtering
    6. Group by timeframe
    7. Run pipeline per pair per TF group
    8. Adaptive sleep
    """
    try:
        # ── Check bot paused ──
        paused_row = await db.fetchrow(
            "SELECT value FROM bot_settings WHERE key = 'bot_paused'"
        )
        if paused_row and paused_row["value"] == "true":
            logger.debug("Bot is paused — skipping scan")
            return

        # ── Check circuit breaker ──
        cb_allowed, cb_reason, size_mult = check_circuit_breaker()
        if not cb_allowed:
            logger.info("Circuit breaker active: %s — skipping scan", cb_reason)
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

        # ── Check signal outcomes (trailing stops) ──
        await check_signal_outcomes(db, bybit, deriv)

        # ── Load users and build pair→recipients map with session filtering ──
        users = await load_users_async(db)
        pair_map = {}  # pair -> [uid, ...]
        for uid, user_settings in users.items():
            if not is_in_session(user_settings.get("session", "BOTH")):
                continue
            for pair in user_settings.get("pairs", []):
                clean_p = pair.strip().upper()
                if not clean_p or clean_p in paused_pairs:
                    continue
                if clean_p not in KNOWN_SYMBOLS and not clean_p.endswith("USDT"):
                    continue
                if clean_p.endswith("USDT") and clean_p[:-4] in FOREX_BASES:
                    continue
                if clean_p not in pair_map:
                    pair_map[clean_p] = []
                pair_map[clean_p].append(uid)

        if not pair_map:
            logger.debug("No pairs to scan after session filtering")
            return

        # ── Filter by market hours and news ──
        active_pairs = []
        for p in pair_map:
            if is_market_open(p) and not await is_news_blackout(p):
                active_pairs.append(p)

        if not active_pairs:
            logger.debug("No pairs active after market/news filtering")
            return

        logger.info("Scan cycle started [%s] — %d pairs for %d users",
                     kz_name, len(active_pairs), len(users))

        # ── Group users by timeframe settings per pair ──
        signals_fired = 0
        for pair in sorted(active_pairs):
            recipients = pair_map[pair]

            # Group by (LTF, HTF, touch_trade)
            tf_groups = {}
            for uid in recipients:
                user_conf = users[uid]
                ltf = user_conf.get("timeframe", DEFAULT_SETTINGS["timeframe"])
                htf = user_conf.get("higher_tf", DEFAULT_SETTINGS["higher_tf"])
                tt = bool(user_conf.get("touch_trade", False))
                key = (ltf, htf, tt)
                if key not in tf_groups:
                    tf_groups[key] = []
                tf_groups[key].append((uid, user_conf))

            # Run pipeline for each TF group
            for (ltf, htf, tt), user_list in tf_groups.items():
                try:
                    # Correlation check against open signals
                    # (we check before running the pipeline to save API calls)
                    open_sigs = await get_open_signals_async(db)
                    open_positions = [
                        {"pair": s["pair"], "direction": s["direction"]}
                        for s in open_sigs
                    ]

                    result = await run_pair_pipeline(
                        pair, db, telegram, bybit, deriv,
                        ltf=ltf, htf=htf, touch_trade=tt,
                        user_list=user_list,
                        open_positions=open_positions,
                        size_multiplier=size_mult,
                    )
                    if result:
                        signals_fired += 1
                except Exception as e:
                    logger.error("Scan failed for %s (%s/%s): %s", pair, ltf, htf, e)

        logger.info("Scan cycle complete — %d signal(s) fired from %d pairs",
                     signals_fired, len(active_pairs))

    except Exception as e:
        logger.error("Scan cycle error: %s", e, exc_info=True)
