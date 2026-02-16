import asyncio
import time
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from datetime import datetime, timezone, timedelta
from config import (
    SCAN_LOOP_INTERVAL, SCAN_ERROR_INTERVAL, DEFAULT_SETTINGS, KNOWN_SYMBOLS,
    ADAPTIVE_SCAN_INTERVALS, PAIR_THROTTLE_SECONDS,
    USE_CORRELATION_FILTER, MAX_CURRENCY_EXPOSURE, MAX_CORR_GROUP_SAME_DIR,
    AUTO_DISABLE_PAIR_LOSSES, SIGNAL_MAX_AGE_HOURS, logger,
)
from database import (
    load_users, get_user, deactivate_user, load_sent_signals,
    persist_sent_signal, cleanup_old_sent_signals,
    record_signal, get_open_signals, update_signal_outcome,
    update_signal_tp_stage, get_pair_consecutive_losses,
)
from fetchers import fetch_data, fetch_data_parallel, fetch_current_price
from filters import is_in_session, is_market_open, is_news_blackout, check_spread_vs_risk
from strategy import get_smc_signal, get_pip_value
from signals import format_signal_msg, should_send_signal, cleanup_old_signals
from rate_limiter import rate_limiter
from correlation import check_correlation
from drawdown import (
    check_circuit_breaker, record_trade_result, set_open_trade_count,
    configure as configure_drawdown,
)

# Global scanner state
LAST_SCAN_TIME = 0
IS_SCANNING = False

# In-memory sent signals (loaded from DB on startup)
SENT_SIGNALS = {}

# Per-pair alert throttle: {pair: last_alert_timestamp}
_pair_throttle = {}

# Pairs flagged by journal intelligence (consecutive losses)
_flagged_pairs = set()


def _build_open_positions(open_signals):
    """Build a list of open positions for correlation checking."""
    return [
        {"pair": s["pair"], "direction": s["direction"]}
        for s in open_signals
    ]


async def check_signal_outcomes():
    """Check open signals against current prices to determine WIN/LOSS.

    Trail stop logic:
      - After TP1 hit: move SL to breakeven (entry price)
      - After TP2 hit: trail SL to TP1 level
      - TP3 or auto_win_pips: full close as WIN

    This runs each scan cycle and updates the signal_history table.
    Also feeds results into the drawdown circuit breaker.
    """
    open_signals = get_open_signals()

    # Auto-expire trades older than SIGNAL_MAX_AGE_HOURS
    now_utc = datetime.now(timezone.utc)
    max_age = timedelta(hours=SIGNAL_MAX_AGE_HOURS)
    active_signals = []
    for sig in open_signals:
        created = sig.get('created_at')
        if created is not None:
            # Handle naive datetime from DB (assume UTC)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if now_utc - created > max_age:
                update_signal_outcome(sig['id'], "EXPIRED", 0, 0)
                logger.info(
                    "Signal #%d %s %s auto-expired after %dh",
                    sig['id'], sig['direction'], sig['pair'], SIGNAL_MAX_AGE_HOURS
                )
                continue
        active_signals.append(sig)

    open_signals = active_signals
    set_open_trade_count(len(open_signals))

    if not open_signals:
        return

    # Get unique pairs to fetch prices for
    pairs = list({s['pair'] for s in open_signals})

    for pair in pairs:
        price = await fetch_current_price(pair)
        if price is None:
            continue

        pip_val = get_pip_value(pair)

        for sig in open_signals:
            if sig['pair'] != pair:
                continue

            entry = sig['entry_price']
            tp = sig['tp_price']   # TP2 (zone target, primary TP)
            sl = sig['sl_price']
            direction = sig['direction']
            tp_stage = sig.get('tp_stage', 0)  # 0=none, 1=TP1 hit, 2=TP2 hit

            outcome = None
            pnl_pips = 0
            auto_win_pips = 100

            # TP levels from risk distance
            risk_dist = abs(entry - sl) if tp_stage == 0 else sig.get('original_risk', abs(entry - sl))

            if direction == "BUY":
                tp1 = entry + risk_dist
                tp3 = entry + risk_dist * 3  # fallback TP3

                # Trail stop: adjust SL based on stage
                effective_sl = sl
                if tp_stage >= 2:
                    effective_sl = tp1       # trail to TP1 after TP2 hit
                elif tp_stage >= 1:
                    effective_sl = entry     # breakeven after TP1 hit

                current_pips = (price - entry) * pip_val

                # Check TP progression
                if price >= tp3 or current_pips >= auto_win_pips:
                    outcome = "WIN"
                    pnl_pips = current_pips
                elif price >= tp and tp_stage < 2:
                    # TP2 hit — trail SL to TP1, keep running for TP3
                    update_signal_tp_stage(sig['id'], 2)
                    logger.info("Signal #%d %s %s TP2 hit — trailing SL to TP1",
                                sig['id'], direction, pair)
                    continue
                elif price >= tp1 and tp_stage < 1:
                    # TP1 hit — move SL to breakeven
                    update_signal_tp_stage(sig['id'], 1)
                    logger.info("Signal #%d %s %s TP1 hit — SL moved to breakeven",
                                sig['id'], direction, pair)
                    continue
                elif price <= effective_sl:
                    outcome = "LOSS" if tp_stage == 0 else "WIN"
                    pnl_pips = (effective_sl - entry) * pip_val

            elif direction == "SELL":
                tp1 = entry - risk_dist
                tp3 = entry - risk_dist * 3

                effective_sl = sl
                if tp_stage >= 2:
                    effective_sl = tp1
                elif tp_stage >= 1:
                    effective_sl = entry

                current_pips = (entry - price) * pip_val

                if price <= tp3 or current_pips >= auto_win_pips:
                    outcome = "WIN"
                    pnl_pips = current_pips
                elif price <= tp and tp_stage < 2:
                    update_signal_tp_stage(sig['id'], 2)
                    logger.info("Signal #%d %s %s TP2 hit — trailing SL to TP1",
                                sig['id'], direction, pair)
                    continue
                elif price <= tp1 and tp_stage < 1:
                    update_signal_tp_stage(sig['id'], 1)
                    logger.info("Signal #%d %s %s TP1 hit — SL moved to breakeven",
                                sig['id'], direction, pair)
                    continue
                elif price >= effective_sl:
                    outcome = "LOSS" if tp_stage == 0 else "WIN"
                    pnl_pips = (entry - effective_sl) * pip_val

            if outcome:
                update_signal_outcome(sig['id'], outcome, price, round(pnl_pips, 1))
                logger.info(
                    "Signal #%d %s %s closed: %s (%.1f pips) [stage=%d]",
                    sig['id'], direction, pair, outcome, pnl_pips, tp_stage
                )
                # Feed into drawdown circuit breaker
                record_trade_result(pnl_pips, outcome == "WIN")


async def scanner_loop(app):
    """Main scanning loop that checks for signals and sends them."""
    global LAST_SCAN_TIME, IS_SCANNING, SENT_SIGNALS

    # Load persisted sent signals state from database
    SENT_SIGNALS = load_sent_signals()

    # Configure drawdown thresholds from config
    from config import (
        MAX_DAILY_LOSS_PIPS, MAX_WEEKLY_LOSS_PIPS, MAX_CONSECUTIVE_LOSSES,
        LOSS_STREAK_PAUSE_HOURS, MAX_OPEN_TRADES,
    )
    configure_drawdown(
        daily_loss=MAX_DAILY_LOSS_PIPS, weekly_loss=MAX_WEEKLY_LOSS_PIPS,
        max_streak=MAX_CONSECUTIVE_LOSSES, pause_hours=LOSS_STREAK_PAUSE_HOURS,
        max_open=MAX_OPEN_TRADES,
    )

    while True:
        try:
            IS_SCANNING = True
            LAST_SCAN_TIME = time.time()
            users = load_users()

            # Periodic cleanup
            cleanup_old_signals(SENT_SIGNALS)
            cleanup_old_sent_signals()

            # Check outcomes of open signals
            await check_signal_outcomes()

            # --- Circuit Breaker Check ---
            cb_allowed, cb_reason, size_multiplier = check_circuit_breaker()
            if not cb_allowed:
                logger.info("Circuit breaker active: %s — skipping scan", cb_reason)
                IS_SCANNING = False
                await asyncio.sleep(SCAN_LOOP_INTERVAL)
                continue

            # Build pair -> recipients map
            pair_map = {}
            for uid, settings in users.items():
                if not is_in_session(settings["session"]):
                    continue
                for pair in settings["pairs"]:
                    clean_p = pair.replace("\n", "").replace("\r", "").strip().upper()
                    if not clean_p or clean_p not in KNOWN_SYMBOLS:
                        continue
                    if clean_p not in pair_map:
                        pair_map[clean_p] = []
                    pair_map[clean_p].append(uid)

            if pair_map:
                logger.info("Scanning %d unique pairs for %d users", len(pair_map), len(users))

            # Filter pairs by market hours and news (is_news_blackout is now async)
            active_pairs = []
            for p in pair_map:
                if is_market_open(p) and not await is_news_blackout(p):
                    active_pairs.append(p)

            if not active_pairs:
                IS_SCANNING = False
                await asyncio.sleep(SCAN_LOOP_INTERVAL)
                continue

            # --- Journal Intelligence: refresh flagged pairs ---
            _flagged_pairs.clear()
            for p in active_pairs:
                streak = get_pair_consecutive_losses(p)
                if streak >= AUTO_DISABLE_PAIR_LOSSES:
                    _flagged_pairs.add(p)
                    logger.info("Pair %s flagged: %d consecutive losses", p, streak)

            # Get open positions for correlation checking
            open_signals = get_open_signals()
            open_positions = _build_open_positions(open_signals)

            # Fetch all timeframes needed
            # Collect unique timeframe combinations
            tf_sets = {}
            for pair in active_pairs:
                # Get the first user's settings to determine timeframes
                # (we'll check per-user settings when sending)
                uid = pair_map[pair][0]
                user_conf = get_user(users, uid)
                ltf = user_conf.get("timeframe", DEFAULT_SETTINGS["timeframe"])
                htf = user_conf.get("higher_tf", DEFAULT_SETTINGS["higher_tf"])
                if ltf not in tf_sets:
                    tf_sets[ltf] = []
                tf_sets[ltf].append(pair)
                if htf not in tf_sets:
                    tf_sets[htf] = []
                tf_sets[htf].append(pair)

            # Parallel fetch per timeframe
            all_data = {}
            for tf, tf_pairs in tf_sets.items():
                unique_pairs = list(set(tf_pairs))
                results = await fetch_data_parallel(unique_pairs, tf)
                for pair, df in results.items():
                    all_data[(pair, tf)] = df

            # Generate and send signals
            for pair in active_pairs:
                recipients = pair_map[pair]
                current_time = time.time()

                # Per-pair alert throttle: skip if same pair alerted too recently
                last_pair_alert = _pair_throttle.get(pair, 0)
                if current_time - last_pair_alert < PAIR_THROTTLE_SECONDS:
                    continue

                # --- Journal Intelligence: skip flagged pairs ---
                if pair in _flagged_pairs:
                    logger.info("Skipping %s — flagged for consecutive losses", pair)
                    continue

                # Group by timeframe + touch_trade settings
                tf_groups = {}
                for uid in recipients:
                    user_conf = get_user(users, uid)
                    ltf = user_conf.get("timeframe", DEFAULT_SETTINGS["timeframe"])
                    htf = user_conf.get("higher_tf", DEFAULT_SETTINGS["higher_tf"])
                    tt = bool(user_conf.get("touch_trade", False))
                    key = (ltf, htf, tt)
                    if key not in tf_groups:
                        tf_groups[key] = []
                    tf_groups[key].append((uid, user_conf))

                for (ltf, htf, tt), user_list in tf_groups.items():
                    df_l = all_data.get((pair, ltf), None)
                    df_h = all_data.get((pair, htf), None)

                    if df_l is None or df_h is None:
                        # Fallback to individual fetch
                        if df_l is None:
                            df_l = await fetch_data(pair, ltf)
                        if df_h is None:
                            df_h = await fetch_data(pair, htf)

                    # Use the first user's risk_pips (they share timeframes)
                    risk_pips = user_list[0][1].get("risk_pips", DEFAULT_SETTINGS["risk_pips"])
                    sig = get_smc_signal(
                        df_l, df_h, pair, risk_pips=risk_pips, touch_trade=tt
                    )

                    if not sig:
                        continue

                    # --- Spread Filter ---
                    if df_l is not None and not df_l.empty:
                        entry_price = sig['limit_e'] if user_list[0][1].get("mode") == "LIMIT" else sig['market_e']
                        sl_price = sig['limit_sl'] if user_list[0][1].get("mode") == "LIMIT" else sig['market_sl']
                        spread_ok, est_spread = check_spread_vs_risk(df_l, entry_price, sl_price)
                        if not spread_ok:
                            logger.info("Skipping %s %s — spread too wide (%.6f)",
                                        sig['act'], pair, est_spread)
                            continue

                    # --- Correlation Filter ---
                    if USE_CORRELATION_FILTER:
                        corr_ok, corr_reason = check_correlation(
                            pair, sig['act'], open_positions,
                            max_currency_exposure=MAX_CURRENCY_EXPOSURE,
                            max_group_same_dir=MAX_CORR_GROUP_SAME_DIR,
                        )
                        if not corr_ok:
                            logger.info("Skipping %s %s — correlation: %s",
                                        sig['act'], pair, corr_reason)
                            continue

                    # Record signal once for tracking
                    signal_recorded = False

                    for uid, user_conf in user_list:
                        signal_key = f"{uid}_{pair}"
                        cooldown_sec = user_conf['cooldown'] * 60

                        if not should_send_signal(SENT_SIGNALS, signal_key, sig, cooldown_sec):
                            continue

                        mode = user_conf.get("mode", "MARKET")
                        balance = user_conf.get("balance", 0)
                        risk_pct = user_conf.get("risk_pct", 1)
                        pip_val = get_pip_value(pair)
                        msg = format_signal_msg(
                            sig, pair, mode,
                            balance=balance, risk_pct=risk_pct, pip_value=pip_val,
                            size_multiplier=size_multiplier,
                        )
                        entry_price = sig['limit_e'] if mode == "LIMIT" else sig['market_e']
                        sl_price = sig['limit_sl'] if mode == "LIMIT" else sig['market_sl']

                        try:
                            await rate_limiter.send_message(
                                app.bot, uid, msg, parse_mode=ParseMode.MARKDOWN
                            )
                            SENT_SIGNALS[signal_key] = {
                                'price': entry_price,
                                'time': current_time,
                                'direction': sig['act'],
                            }
                            persist_sent_signal(signal_key, entry_price, sig['act'])

                            # Record to signal history (once per pair/direction combo)
                            if not signal_recorded:
                                record_signal(
                                    pair, sig['act'], mode,
                                    entry_price, sig['tp'], sl_price,
                                    zone_type=sig.get('zone_type', ''),
                                    regime=sig.get('regime', ''),
                                    confidence=sig.get('confidence', 'medium'),
                                )
                                signal_recorded = True
                                _pair_throttle[pair] = current_time

                                # Add to open positions for next pair's correlation check
                                open_positions.append({
                                    "pair": pair, "direction": sig['act'],
                                })

                            logger.info("Sent %s %s (%s) to %s [regime=%s]",
                                        sig['act'], pair, mode, uid,
                                        sig.get('regime', 'N/A'))
                        except Forbidden:
                            logger.info("User %s blocked bot, deactivating", uid)
                            deactivate_user(uid)
                        except BadRequest as e:
                            logger.warning("Bad request sending to %s: %s", uid, e)
                        except Exception as e:
                            logger.error("Failed to send signal to %s: %s", uid, e)

            IS_SCANNING = False

            # Adaptive scan interval: use the shortest timeframe across active users
            min_tf = "M15"
            for uid, settings in users.items():
                tf = settings.get("timeframe", "M15")
                tf_secs = ADAPTIVE_SCAN_INTERVALS.get(tf, 60)
                if tf_secs < ADAPTIVE_SCAN_INTERVALS.get(min_tf, 60):
                    min_tf = tf
            scan_sleep = ADAPTIVE_SCAN_INTERVALS.get(min_tf, SCAN_LOOP_INTERVAL)
            await asyncio.sleep(scan_sleep)
        except Exception as e:
            logger.error("Scanner loop error: %s", e, exc_info=True)
            IS_SCANNING = False
            await asyncio.sleep(SCAN_ERROR_INTERVAL)
