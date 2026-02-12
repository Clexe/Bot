import asyncio
import time
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from config import SCAN_LOOP_INTERVAL, SCAN_ERROR_INTERVAL, DEFAULT_SETTINGS, KNOWN_SYMBOLS, logger
from database import (
    load_users, get_user, deactivate_user, load_sent_signals,
    persist_sent_signal, cleanup_old_sent_signals,
    record_signal, get_open_signals, update_signal_outcome,
)
from fetchers import fetch_data, fetch_data_parallel, fetch_current_price
from filters import is_in_session, is_market_open, is_news_blackout
from strategy import get_smc_signal, get_pip_value
from signals import format_signal_msg, should_send_signal, cleanup_old_signals
from rate_limiter import rate_limiter

# Global scanner state
LAST_SCAN_TIME = 0
IS_SCANNING = False

# In-memory sent signals (loaded from DB on startup)
SENT_SIGNALS = {}


async def check_signal_outcomes():
    """Check open signals against current prices to determine WIN/LOSS.

    This runs each scan cycle and updates the signal_history table.
    """
    open_signals = get_open_signals()
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
            tp = sig['tp_price']
            sl = sig['sl_price']
            direction = sig['direction']

            outcome = None
            pnl_pips = 0

            if direction == "BUY":
                if price >= tp:
                    outcome = "WIN"
                    pnl_pips = (tp - entry) * pip_val
                elif price <= sl:
                    outcome = "LOSS"
                    pnl_pips = (sl - entry) * pip_val
            elif direction == "SELL":
                if price <= tp:
                    outcome = "WIN"
                    pnl_pips = (entry - tp) * pip_val
                elif price >= sl:
                    outcome = "LOSS"
                    pnl_pips = (entry - sl) * pip_val

            if outcome:
                update_signal_outcome(sig['id'], outcome, price, round(pnl_pips, 1))
                logger.info(
                    "Signal #%d %s %s closed: %s (%.1f pips)",
                    sig['id'], direction, pair, outcome, pnl_pips
                )


async def scanner_loop(app):
    """Main scanning loop that checks for signals and sends them."""
    global LAST_SCAN_TIME, IS_SCANNING, SENT_SIGNALS

    # Load persisted sent signals state from database
    SENT_SIGNALS = load_sent_signals()

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

            # Filter pairs by market hours and news
            active_pairs = [
                p for p in pair_map
                if is_market_open(p) and not is_news_blackout(p)
            ]

            if not active_pairs:
                IS_SCANNING = False
                await asyncio.sleep(SCAN_LOOP_INTERVAL)
                continue

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

                # We need to check signals per-user since they may have different timeframes
                # Group by timeframe settings
                tf_groups = {}
                for uid in recipients:
                    user_conf = get_user(users, uid)
                    ltf = user_conf.get("timeframe", DEFAULT_SETTINGS["timeframe"])
                    htf = user_conf.get("higher_tf", DEFAULT_SETTINGS["higher_tf"])
                    key = (ltf, htf)
                    if key not in tf_groups:
                        tf_groups[key] = []
                    tf_groups[key].append((uid, user_conf))

                for (ltf, htf), user_list in tf_groups.items():
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
                    sig = get_smc_signal(df_l, df_h, pair, risk_pips=risk_pips)

                    if not sig:
                        continue

                    # Record signal once for tracking
                    signal_recorded = False

                    for uid, user_conf in user_list:
                        signal_key = f"{uid}_{pair}"
                        cooldown_sec = user_conf['cooldown'] * 60

                        if not should_send_signal(SENT_SIGNALS, signal_key, sig, cooldown_sec):
                            continue

                        mode = user_conf.get("mode", "MARKET")
                        msg = format_signal_msg(sig, pair, mode)
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
                                )
                                signal_recorded = True

                            logger.info("Sent %s %s (%s) to %s", sig['act'], pair, mode, uid)
                        except Forbidden:
                            logger.info("User %s blocked bot, deactivating", uid)
                            deactivate_user(uid)
                        except BadRequest as e:
                            logger.warning("Bad request sending to %s: %s", uid, e)
                        except Exception as e:
                            logger.error("Failed to send signal to %s: %s", uid, e)

            IS_SCANNING = False
            await asyncio.sleep(SCAN_LOOP_INTERVAL)
        except Exception as e:
            logger.error("Scanner loop error: %s", e, exc_info=True)
            IS_SCANNING = False
            await asyncio.sleep(SCAN_ERROR_INTERVAL)
