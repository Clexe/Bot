import json
import psycopg2
from contextlib import contextmanager
from config import DATABASE_URL, DEFAULT_SETTINGS, logger


@contextmanager
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create all required database tables."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            # Users table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    settings JSONB DEFAULT '{}'::jsonb,
                    is_active BOOLEAN DEFAULT TRUE,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            # Signal history table for performance tracking
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
                    pnl_pips DOUBLE PRECISION DEFAULT 0
                );
            ''')
            # Sent signals state table for persistence across restarts
            cur.execute('''
                CREATE TABLE IF NOT EXISTS sent_signals (
                    signal_key VARCHAR(100) PRIMARY KEY,
                    price DOUBLE PRECISION NOT NULL,
                    direction VARCHAR(4) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            # Index for faster signal history queries
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


def load_users():
    """Load all active users with their settings."""
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
        # Ensure pairs is always a fresh list
        if not isinstance(settings.get("pairs"), list):
            settings["pairs"] = list(DEFAULT_SETTINGS["pairs"])
        users[uid] = settings
    return users


def save_user_settings(chat_id, settings):
    """Save or update user settings."""
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


def deactivate_user(chat_id):
    """Mark a user as inactive (e.g. they blocked the bot)."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = FALSE WHERE user_id = %s", (chat_id,))
            conn.commit()
            cur.close()
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
        return default_settings
    return users[chat_id]


# =====================
# SIGNAL HISTORY (Performance Tracking)
# =====================
def record_signal(pair, direction, mode, entry_price, tp_price, sl_price):
    """Record a new signal to the history table."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO signal_history (pair, direction, mode, entry_price, tp_price, sl_price)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (pair, direction, mode, entry_price, tp_price, sl_price))
            signal_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            return signal_id
    except Exception as e:
        logger.error("Failed to record signal: %s", e)
        return None


def update_signal_outcome(signal_id, outcome, close_price, pnl_pips):
    """Update a signal with its outcome (WIN/LOSS/BREAKEVEN)."""
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
    except Exception as e:
        logger.error("Failed to update signal outcome: %s", e)


def get_open_signals():
    """Get all signals that are still OPEN for outcome checking."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, pair, direction, entry_price, tp_price, sl_price, mode
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
                    "mode": r[6]
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to get open signals: %s", e)
        return []


def get_signal_stats(pair=None, days=30):
    """Get signal performance statistics."""
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
            return {
                "total": total,
                "wins": wins,
                "losses": losses,
                "open": row[3],
                "win_rate": (wins / closed * 100) if closed > 0 else 0,
                "total_pips": round(row[4], 1),
                "avg_pips": round(row[5], 1),
            }
    except Exception as e:
        logger.error("Failed to get signal stats: %s", e)
        return None


def get_recent_signals(limit=10):
    """Get the most recent signals with outcomes."""
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
            return [
                {
                    "pair": r[0], "direction": r[1], "mode": r[2],
                    "entry_price": r[3], "tp_price": r[4], "sl_price": r[5],
                    "outcome": r[6], "pnl_pips": r[7],
                    "created_at": r[8].strftime("%m/%d %H:%M") if r[8] else ""
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to get recent signals: %s", e)
        return []


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
                    "time": 0,  # will be set on next signal
                }
            logger.info("Loaded %d persisted signal states", len(signals))
            return signals
    except Exception as e:
        logger.error("Failed to load sent signals: %s", e)
        return {}


def persist_sent_signal(signal_key, price, direction):
    """Persist a sent signal state to survive restarts."""
    try:
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
