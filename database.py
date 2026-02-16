import json
import time
import asyncio
import threading
import psycopg2
import psycopg2.pool
from contextlib import contextmanager
from config import DATABASE_URL, DEFAULT_SETTINGS, logger


# =====================
# CONNECTION POOL (reuses connections instead of opening/closing each call)
# =====================
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """Lazy-init a threadsafe connection pool (1-5 connections)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    1, 5, DATABASE_URL, options="-c statement_timeout=10000"
                )
                logger.info("Database connection pool created (1-5 conns)")
    return _pool


@contextmanager
def get_db_connection():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


# =====================
# IN-MEMORY USER CACHE (avoids DB round-trip on every command)
# =====================
_user_cache = {}
_user_cache_lock = threading.Lock()
_user_cache_ts = 0
_USER_CACHE_TTL = 30  # seconds — refresh from DB at most every 30s


def _refresh_user_cache_if_stale():
    """Reload users from DB if cache is older than TTL."""
    global _user_cache, _user_cache_ts
    now = time.time()
    if now - _user_cache_ts < _USER_CACHE_TTL and _user_cache:
        return
    with _user_cache_lock:
        if now - _user_cache_ts < _USER_CACHE_TTL and _user_cache:
            return
        _user_cache = _load_users_from_db()
        _user_cache_ts = time.time()


def _invalidate_user_cache_entry(chat_id):
    """Update a single entry in the cache after a write."""
    pass  # The write functions below update the cache directly


def load_users():
    """Load all active users with their settings (cache-backed)."""
    _refresh_user_cache_if_stale()
    return dict(_user_cache)


async def load_users_async():
    """Async wrapper — runs the (now fast, cached) load off the event loop."""
    return await asyncio.to_thread(load_users)


def _load_users_from_db():
    """Raw DB load — only called by cache refresh."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, settings FROM users WHERE is_active = TRUE")
            rows = cur.fetchall()
            cur.close()

        users = {}
        for r in rows:
            uid = str(r[0])
            saved_settings = r[1] if r[1] else {}
            settings = {**DEFAULT_SETTINGS, **saved_settings}
            if not isinstance(settings.get("pairs"), list):
                settings["pairs"] = list(DEFAULT_SETTINGS["pairs"])
            users[uid] = settings
        return users
    except Exception as e:
        logger.error("DB load_users error: %s", e)
        return _user_cache if _user_cache else {}


def save_user_settings(chat_id, settings):
    """Save or update user settings (updates cache immediately)."""
    global _user_cache_ts
    chat_id = str(chat_id)
    with get_db_connection() as conn:
        cur = conn.cursor()
        json_settings = json.dumps(settings)
        cur.execute("""
            INSERT INTO users (user_id, settings, is_active)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (user_id)
            DO UPDATE SET settings = %s, is_active = TRUE;
        """, (chat_id, json_settings, json_settings))
        conn.commit()
        cur.close()
    # Update cache in-place so next read is instant
    with _user_cache_lock:
        _user_cache[chat_id] = {**DEFAULT_SETTINGS, **settings}


async def save_user_settings_async(chat_id, settings):
    """Async wrapper for save_user_settings."""
    return await asyncio.to_thread(save_user_settings, chat_id, settings)


def deactivate_user(chat_id):
    """Mark a user as inactive (e.g. they blocked the bot)."""
    chat_id = str(chat_id)
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = FALSE WHERE user_id = %s", (chat_id,))
            conn.commit()
            cur.close()
        # Remove from cache
        with _user_cache_lock:
            _user_cache.pop(chat_id, None)
        logger.info("Deactivated user %s", chat_id)
    except Exception as e:
        logger.error("Failed to deactivate user %s: %s", chat_id, e)


def get_user(users, chat_id):
    """Get user settings, creating default if not exists."""
    chat_id = str(chat_id)
    if chat_id not in users:
        default_settings = {**DEFAULT_SETTINGS}
        default_settings["pairs"] = list(DEFAULT_SETTINGS["pairs"])
        save_user_settings(chat_id, default_settings)
        users[chat_id] = default_settings
        return default_settings
    return users[chat_id]


async def get_user_async(chat_id):
    """Fast async user lookup — cache-first, no DB round-trip for known users."""
    _refresh_user_cache_if_stale()
    chat_id = str(chat_id)
    if chat_id in _user_cache:
        return _user_cache[chat_id]
    # New user — do the DB insert off the event loop
    default_settings = {**DEFAULT_SETTINGS}
    default_settings["pairs"] = list(DEFAULT_SETTINGS["pairs"])
    await save_user_settings_async(chat_id, default_settings)
    return default_settings


def init_db():
    """Create all required database tables."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    settings JSONB DEFAULT '{}'::jsonb,
                    is_active BOOLEAN DEFAULT TRUE,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS signal_history (
                    id SERIAL PRIMARY KEY,
                    pair VARCHAR(20) NOT NULL,
                    direction VARCHAR(4) NOT NULL,
                    mode VARCHAR(10) NOT NULL,
                    entry_price DOUBLE PRECISION NOT NULL,
                    tp_price DOUBLE PRECISION NOT NULL,
                    sl_price DOUBLE PRECISION NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    outcome VARCHAR(10) DEFAULT 'OPEN',
                    close_price DOUBLE PRECISION,
                    closed_at TIMESTAMP,
                    pnl_pips DOUBLE PRECISION DEFAULT 0,
                    tp_stage INTEGER DEFAULT 0
                );
            ''')
            # Add tp_stage column if missing (existing DB migration)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE signal_history ADD COLUMN tp_stage INTEGER DEFAULT 0;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)
            # Add zone_type and regime columns for journal intelligence
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE signal_history ADD COLUMN zone_type VARCHAR(10) DEFAULT '';
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE signal_history ADD COLUMN regime VARCHAR(20) DEFAULT '';
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE signal_history ADD COLUMN confidence VARCHAR(10) DEFAULT 'medium';
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS sent_signals (
                    signal_key VARCHAR(100) PRIMARY KEY,
                    price DOUBLE PRECISION NOT NULL,
                    direction VARCHAR(4) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            cur.execute('''
                CREATE INDEX IF NOT EXISTS idx_signal_history_pair
                ON signal_history(pair, created_at DESC);
            ''')
            cur.execute('''
                CREATE INDEX IF NOT EXISTS idx_signal_history_outcome
                ON signal_history(outcome);
            ''')
            conn.commit()
            cur.close()
        logger.info("Database tables ready")
    except Exception as e:
        logger.error("DB init error: %s", e)


# =====================
# SIGNAL HISTORY (Performance Tracking) — with TTL cache
# =====================
_stats_cache = {}
_stats_cache_ts = 0
_STATS_CACHE_TTL = 60  # seconds

_history_cache = None
_history_cache_ts = 0
_HISTORY_CACHE_TTL = 60  # seconds


def record_signal(pair, direction, mode, entry_price, tp_price, sl_price,
                  zone_type="", regime="", confidence="medium"):
    """Record a new signal to the history table."""
    global _stats_cache_ts, _history_cache_ts
    try:
        entry_price = float(entry_price)
        tp_price = float(tp_price)
        sl_price = float(sl_price)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO signal_history
                    (pair, direction, mode, entry_price, tp_price, sl_price,
                     zone_type, regime, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (pair, direction, mode, entry_price, tp_price, sl_price,
                  zone_type, regime, confidence))
            signal_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            # Invalidate stats/history caches
            _stats_cache_ts = 0
            _history_cache_ts = 0
            return signal_id
    except Exception as e:
        logger.error("Failed to record signal: %s", e)
        return None


def update_signal_outcome(signal_id, outcome, close_price, pnl_pips):
    """Update a signal with its outcome (WIN/LOSS/BREAKEVEN)."""
    global _stats_cache_ts, _history_cache_ts
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE signal_history
                SET outcome = %s, close_price = %s, pnl_pips = %s, closed_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """, (outcome, close_price, pnl_pips, signal_id))
            conn.commit()
            cur.close()
        # Invalidate stats/history caches
        _stats_cache_ts = 0
        _history_cache_ts = 0
    except Exception as e:
        logger.error("Failed to update signal outcome: %s", e)


def update_signal_tp_stage(signal_id, stage):
    """Update TP stage for trail stop management.

    stage 0 = no TP hit, 1 = TP1 hit (SL→BE), 2 = TP2 hit (SL→TP1).
    """
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE signal_history SET tp_stage = %s WHERE id = %s;
            """, (stage, signal_id))
            conn.commit()
            cur.close()
    except Exception as e:
        logger.error("Failed to update TP stage for signal %s: %s", signal_id, e)


def get_open_signals():
    """Get all signals that are still OPEN for outcome checking."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, pair, direction, entry_price, tp_price, sl_price, mode,
                       COALESCE(tp_stage, 0)
                FROM signal_history
                WHERE outcome = 'OPEN'
                AND created_at > CURRENT_TIMESTAMP - INTERVAL '48 hours'
                ORDER BY created_at DESC;
            """)
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "id": r[0], "pair": r[1], "direction": r[2],
                    "entry_price": r[3], "tp_price": r[4], "sl_price": r[5],
                    "mode": r[6], "tp_stage": r[7],
                    "original_risk": abs(r[3] - r[5]),
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to get open signals: %s", e)
        return []


def expire_stale_signals(max_age_hours=24):
    """Auto-expire OPEN signals older than max_age_hours.

    Signals that stay open beyond the max age are stale (price data
    unavailable, bot restarted, etc.) and should not block the circuit
    breaker indefinitely.  Returns the number of expired signals.
    """
    global _stats_cache_ts, _history_cache_ts
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE signal_history
                SET outcome = 'EXPIRED', closed_at = CURRENT_TIMESTAMP,
                    pnl_pips = 0
                WHERE outcome = 'OPEN'
                AND created_at < CURRENT_TIMESTAMP - INTERVAL '%s hours';
            """, (max_age_hours,))
            count = cur.rowcount
            conn.commit()
            cur.close()
            if count > 0:
                _stats_cache_ts = 0
                _history_cache_ts = 0
                logger.info("Auto-expired %d stale signals (older than %dh)",
                            count, max_age_hours)
            return count
    except Exception as e:
        logger.error("Failed to expire stale signals: %s", e)
        return 0


def get_signal_stats(pair=None, days=30):
    """Get signal performance statistics (cached for 60s)."""
    global _stats_cache, _stats_cache_ts
    cache_key = f"{pair}_{days}"
    now = time.time()
    if now - _stats_cache_ts < _STATS_CACHE_TTL and cache_key in _stats_cache:
        return _stats_cache[cache_key]

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            if pair:
                cur.execute("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
                        COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
                        COUNT(*) FILTER (WHERE outcome = 'OPEN') as open_count,
                        COALESCE(SUM(pnl_pips) FILTER (WHERE outcome != 'OPEN'), 0) as total_pips,
                        COALESCE(AVG(pnl_pips) FILTER (WHERE outcome != 'OPEN'), 0) as avg_pips
                    FROM signal_history
                    WHERE pair = %s AND created_at > CURRENT_TIMESTAMP - INTERVAL '%s days';
                """, (pair, days))
            else:
                cur.execute("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
                        COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
                        COUNT(*) FILTER (WHERE outcome = 'OPEN') as open_count,
                        COALESCE(SUM(pnl_pips) FILTER (WHERE outcome != 'OPEN'), 0) as total_pips,
                        COALESCE(AVG(pnl_pips) FILTER (WHERE outcome != 'OPEN'), 0) as avg_pips
                    FROM signal_history
                    WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '%s days';
                """, (days,))
            row = cur.fetchone()
            cur.close()
            if not row:
                return None
            total = row[0]
            wins = row[1]
            losses = row[2]
            closed = wins + losses
            result = {
                "total": total,
                "wins": wins,
                "losses": losses,
                "open": row[3],
                "win_rate": (wins / closed * 100) if closed > 0 else 0,
                "total_pips": round(row[4], 1),
                "avg_pips": round(row[5], 1),
            }
            _stats_cache[cache_key] = result
            _stats_cache_ts = now
            return result
    except Exception as e:
        logger.error("Failed to get signal stats: %s", e)
        return None


async def get_signal_stats_async(pair=None, days=30):
    """Async wrapper for get_signal_stats."""
    return await asyncio.to_thread(get_signal_stats, pair, days)


def get_pair_breakdown(days=30):
    """Get win rate and P&L breakdown per pair for analytics dashboard."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT pair,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
                    COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
                    COALESCE(SUM(pnl_pips) FILTER (WHERE outcome != 'OPEN'), 0) as total_pips
                FROM signal_history
                WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '%s days'
                GROUP BY pair
                ORDER BY total_pips DESC;
            """, (days,))
            rows = cur.fetchall()
            cur.close()
            result = []
            for r in rows:
                closed = r[2] + r[3]
                result.append({
                    "pair": r[0], "total": r[1], "wins": r[2], "losses": r[3],
                    "win_rate": round(r[2] / closed * 100, 1) if closed else 0,
                    "total_pips": round(r[4], 1),
                })
            return result
    except Exception as e:
        logger.error("Failed to get pair breakdown: %s", e)
        return []


async def get_pair_breakdown_async(days=30):
    """Async wrapper for get_pair_breakdown."""
    return await asyncio.to_thread(get_pair_breakdown, days)


def get_session_breakdown(days=30):
    """Get performance breakdown by trading session (London/NY/Overlap)."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    CASE
                        WHEN EXTRACT(HOUR FROM created_at) BETWEEN 8 AND 12 THEN 'London'
                        WHEN EXTRACT(HOUR FROM created_at) BETWEEN 13 AND 16 THEN 'Overlap'
                        WHEN EXTRACT(HOUR FROM created_at) BETWEEN 17 AND 21 THEN 'New York'
                        ELSE 'Off-Hours'
                    END as session,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
                    COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
                    COALESCE(SUM(pnl_pips) FILTER (WHERE outcome != 'OPEN'), 0) as total_pips
                FROM signal_history
                WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '%s days'
                GROUP BY session
                ORDER BY total DESC;
            """, (days,))
            rows = cur.fetchall()
            cur.close()
            result = []
            for r in rows:
                closed = r[2] + r[3]
                result.append({
                    "session": r[0], "total": r[1], "wins": r[2], "losses": r[3],
                    "win_rate": round(r[2] / closed * 100, 1) if closed else 0,
                    "total_pips": round(r[4], 1),
                })
            return result
    except Exception as e:
        logger.error("Failed to get session breakdown: %s", e)
        return []


async def get_session_breakdown_async(days=30):
    """Async wrapper for get_session_breakdown."""
    return await asyncio.to_thread(get_session_breakdown, days)


def get_recent_signals(limit=10):
    """Get the most recent signals with outcomes (cached for 60s)."""
    global _history_cache, _history_cache_ts
    now = time.time()
    if now - _history_cache_ts < _HISTORY_CACHE_TTL and _history_cache is not None:
        return _history_cache[:limit]

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT pair, direction, mode, entry_price, tp_price, sl_price,
                       outcome, pnl_pips, created_at
                FROM signal_history
                ORDER BY created_at DESC
                LIMIT %s;
            """, (limit,))
            rows = cur.fetchall()
            cur.close()
            result = [
                {
                    "pair": r[0], "direction": r[1], "mode": r[2],
                    "entry_price": r[3], "tp_price": r[4], "sl_price": r[5],
                    "outcome": r[6], "pnl_pips": r[7],
                    "created_at": r[8].strftime("%m/%d %H:%M") if r[8] else ""
                }
                for r in rows
            ]
            _history_cache = result
            _history_cache_ts = now
            return result
    except Exception as e:
        logger.error("Failed to get recent signals: %s", e)
        return []


async def get_recent_signals_async(limit=10):
    """Async wrapper for get_recent_signals."""
    return await asyncio.to_thread(get_recent_signals, limit)


def get_pair_consecutive_losses(pair, limit=10):
    """Count consecutive recent losses for a pair (for auto-disable)."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT outcome FROM signal_history
                WHERE pair = %s AND outcome IN ('WIN', 'LOSS')
                ORDER BY created_at DESC
                LIMIT %s;
            """, (pair, limit))
            rows = cur.fetchall()
            cur.close()
            streak = 0
            for r in rows:
                if r[0] == 'LOSS':
                    streak += 1
                else:
                    break
            return streak
    except Exception as e:
        logger.error("Failed to get pair loss streak for %s: %s", pair, e)
        return 0


def get_zone_type_stats(days=30):
    """Get win rate breakdown by zone type for journal intelligence."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT zone_type,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
                    COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
                    COALESCE(SUM(pnl_pips) FILTER (WHERE outcome != 'OPEN'), 0) as total_pips
                FROM signal_history
                WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '%s days'
                  AND zone_type IS NOT NULL AND zone_type != ''
                GROUP BY zone_type
                ORDER BY total DESC;
            """, (days,))
            rows = cur.fetchall()
            cur.close()
            result = []
            for r in rows:
                closed = r[2] + r[3]
                result.append({
                    "zone_type": r[0], "total": r[1], "wins": r[2], "losses": r[3],
                    "win_rate": round(r[2] / closed * 100, 1) if closed else 0,
                    "total_pips": round(r[4], 1),
                })
            return result
    except Exception as e:
        logger.error("Failed to get zone type stats: %s", e)
        return []


async def get_zone_type_stats_async(days=30):
    """Async wrapper for get_zone_type_stats."""
    return await asyncio.to_thread(get_zone_type_stats, days)


def get_regime_stats(days=30):
    """Get win rate breakdown by market regime."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT regime,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE outcome = 'WIN') as wins,
                    COUNT(*) FILTER (WHERE outcome = 'LOSS') as losses,
                    COALESCE(SUM(pnl_pips) FILTER (WHERE outcome != 'OPEN'), 0) as total_pips
                FROM signal_history
                WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '%s days'
                  AND regime IS NOT NULL AND regime != ''
                GROUP BY regime
                ORDER BY total DESC;
            """, (days,))
            rows = cur.fetchall()
            cur.close()
            result = []
            for r in rows:
                closed = r[2] + r[3]
                result.append({
                    "regime": r[0], "total": r[1], "wins": r[2], "losses": r[3],
                    "win_rate": round(r[2] / closed * 100, 1) if closed else 0,
                    "total_pips": round(r[4], 1),
                })
            return result
    except Exception as e:
        logger.error("Failed to get regime stats: %s", e)
        return []


async def get_regime_stats_async(days=30):
    """Async wrapper for get_regime_stats."""
    return await asyncio.to_thread(get_regime_stats, days)


# =====================
# SENT SIGNALS PERSISTENCE
# =====================
def load_sent_signals():
    """Load persisted sent signals state from database."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT signal_key, price, direction
                FROM sent_signals
                WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '4 hours';
            """)
            rows = cur.fetchall()
            cur.close()
            signals = {}
            for r in rows:
                signals[r[0]] = {
                    "price": r[1],
                    "direction": r[2],
                    "time": 0,
                }
            logger.info("Loaded %d persisted signal states", len(signals))
            return signals
    except Exception as e:
        logger.error("Failed to load sent signals: %s", e)
        return {}


def persist_sent_signal(signal_key, price, direction):
    """Persist a sent signal state to survive restarts."""
    try:
        price = float(price)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sent_signals (signal_key, price, direction)
                VALUES (%s, %s, %s)
                ON CONFLICT (signal_key)
                DO UPDATE SET price = %s, direction = %s, created_at = CURRENT_TIMESTAMP;
            """, (signal_key, price, direction, price, direction))
            conn.commit()
            cur.close()
    except Exception as e:
        logger.error("Failed to persist sent signal: %s", e)


def cleanup_old_sent_signals():
    """Remove expired sent signal entries from database."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM sent_signals
                WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '4 hours';
            """)
            deleted = cur.rowcount
            conn.commit()
            cur.close()
            if deleted:
                logger.info("Cleaned up %d expired sent signal entries", deleted)
    except Exception as e:
        logger.error("Failed to cleanup sent signals: %s", e)
