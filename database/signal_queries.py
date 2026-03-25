from utils.logger import get_logger

logger = get_logger(__name__)


async def get_signal_stats_async(db, pair=None, days=30):
    """Get signal performance statistics."""
    try:
        if pair:
            rows = await db.fetch("""
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
            rows = await db.fetch("""
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
        if not rows:
            return None
        r = rows[0]
        wins = r["wins"]
        losses = r["losses"]
        closed = wins + losses
        return {
            "total": r["total"],
            "wins": wins,
            "losses": losses,
            "open": r["open_count"],
            "win_rate": (wins / closed * 100) if closed > 0 else 0,
            "total_pips": round(float(r["total_pips"]), 1),
            "avg_pips": round(float(r["avg_pips"]), 1),
        }
    except Exception as e:
        logger.error("Failed to get signal stats: %s", e)
        return None


async def get_recent_signals_async(db, limit=10):
    """Get the most recent signals with outcomes."""
    try:
        rows = await db.fetch("""
            SELECT pair, direction, mode, entry_price, tp_price, sl_price,
                   outcome, pnl_pips, created_at
            FROM signal_history
            ORDER BY created_at DESC
            LIMIT %s;
        """, (limit,))
        result = []
        for r in rows:
            created = r["created_at"]
            result.append({
                "pair": r["pair"], "direction": r["direction"],
                "outcome": r["outcome"], "pnl_pips": r["pnl_pips"],
                "created_at": created.strftime("%m/%d %H:%M") if created else "",
            })
        return result
    except Exception as e:
        logger.error("Failed to get recent signals: %s", e)
        return []


async def get_pair_breakdown_async(db, days=30):
    """Get win rate and P&L breakdown per pair."""
    try:
        rows = await db.fetch("""
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
        result = []
        for r in rows:
            closed = r["wins"] + r["losses"]
            result.append({
                "pair": r["pair"], "total": r["total"],
                "wins": r["wins"], "losses": r["losses"],
                "win_rate": round(r["wins"] / closed * 100, 1) if closed else 0,
                "total_pips": round(float(r["total_pips"]), 1),
            })
        return result
    except Exception as e:
        logger.error("Failed to get pair breakdown: %s", e)
        return []


async def get_session_breakdown_async(db, days=30):
    """Get performance breakdown by trading session."""
    try:
        rows = await db.fetch("""
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
        result = []
        for r in rows:
            closed = r["wins"] + r["losses"]
            result.append({
                "session": r["session"], "total": r["total"],
                "wins": r["wins"], "losses": r["losses"],
                "win_rate": round(r["wins"] / closed * 100, 1) if closed else 0,
                "total_pips": round(float(r["total_pips"]), 1),
            })
        return result
    except Exception as e:
        logger.error("Failed to get session breakdown: %s", e)
        return []


async def get_zone_type_stats_async(db, days=30):
    """Get win rate breakdown by zone type."""
    try:
        rows = await db.fetch("""
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
        result = []
        for r in rows:
            closed = r["wins"] + r["losses"]
            result.append({
                "zone_type": r["zone_type"], "total": r["total"],
                "wins": r["wins"], "losses": r["losses"],
                "win_rate": round(r["wins"] / closed * 100, 1) if closed else 0,
                "total_pips": round(float(r["total_pips"]), 1),
            })
        return result
    except Exception as e:
        logger.error("Failed to get zone type stats: %s", e)
        return []


async def get_regime_stats_async(db, days=30):
    """Get win rate breakdown by market regime."""
    try:
        rows = await db.fetch("""
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
        result = []
        for r in rows:
            closed = r["wins"] + r["losses"]
            result.append({
                "regime": r["regime"], "total": r["total"],
                "wins": r["wins"], "losses": r["losses"],
                "win_rate": round(r["wins"] / closed * 100, 1) if closed else 0,
                "total_pips": round(float(r["total_pips"]), 1),
            })
        return result
    except Exception as e:
        logger.error("Failed to get regime stats: %s", e)
        return []


async def get_open_signals_async(db):
    """Get all signals that are still OPEN."""
    try:
        rows = await db.fetch("""
            SELECT id, pair, direction, entry_price, tp_price, sl_price, mode,
                   COALESCE(tp_stage, 0) as tp_stage, created_at
            FROM signal_history
            WHERE outcome = 'OPEN'
            AND created_at > CURRENT_TIMESTAMP - INTERVAL '48 hours'
            ORDER BY created_at DESC;
        """)
        return [
            {
                "id": r["id"], "pair": r["pair"], "direction": r["direction"],
                "entry_price": r["entry_price"], "tp_price": r["tp_price"],
                "sl_price": r["sl_price"], "mode": r["mode"],
                "tp_stage": r["tp_stage"],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error("Failed to get open signals: %s", e)
        return []
