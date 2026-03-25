from aiohttp import web
from payments.paystack_webhook import handle_paystack_webhook
from utils.logger import get_logger

logger = get_logger(__name__)


async def stats_handler(request):
    """Return aggregate signal performance metrics with per-engine breakdown."""
    db = request.app["db"]

    try:
        # ── Precision Engine Stats ──
        p_total = await db.fetchrow(
            "SELECT COUNT(*) AS c FROM signals WHERE signal_type='precision'"
        )
        p_win_tp1 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='precision' AND outcome IS NOT NULL"""
        )
        p_win_tp2 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='precision' AND outcome IS NOT NULL"""
        )
        p_avg_rr = await db.fetchrow(
            """SELECT COALESCE(AVG(final_rr_achieved), 0) AS a
               FROM signals WHERE signal_type='precision' AND outcome IS NOT NULL"""
        )

        # Per-score win rates for Precision
        p_score_10_11 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='precision' AND score BETWEEN 10 AND 11 AND outcome IS NOT NULL"""
        )
        p_score_12_13 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='precision' AND score BETWEEN 12 AND 13 AND outcome IS NOT NULL"""
        )
        p_score_14_15 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='precision' AND score BETWEEN 14 AND 15 AND outcome IS NOT NULL"""
        )

        # ── Flow Engine Stats ──
        f_total = await db.fetchrow(
            "SELECT COUNT(*) AS c FROM signals WHERE signal_type='flow'"
        )
        f_win_tp1 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='flow' AND outcome IS NOT NULL"""
        )
        f_avg_rr = await db.fetchrow(
            """SELECT COALESCE(AVG(final_rr_achieved), 0) AS a
               FROM signals WHERE signal_type='flow' AND outcome IS NOT NULL"""
        )

        # Per-score win rates for Flow
        f_score_6 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='flow' AND score=6 AND outcome IS NOT NULL"""
        )
        f_score_7 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='flow' AND score=7 AND outcome IS NOT NULL"""
        )
        f_score_8 = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE signal_type='flow' AND score=8 AND outcome IS NOT NULL"""
        )

        # ── Combined Stats ──
        total_30d = await db.fetchrow(
            "SELECT COUNT(*) AS c FROM signals WHERE sent_at > NOW() - INTERVAL '30 days'"
        )
        overall_win = await db.fetchrow(
            """SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END), 0) AS w
               FROM signals WHERE outcome IS NOT NULL"""
        )
        best_pair = await db.fetchrow(
            """SELECT pair, COUNT(*)::float AS c FROM signals
               WHERE outcome IN ('TP1','TP2','TP3') GROUP BY pair ORDER BY c DESC LIMIT 1"""
        )
        week_signals = await db.fetchrow(
            "SELECT COUNT(*) AS c FROM signals WHERE sent_at > NOW() - INTERVAL '7 days'"
        )
        week_wins = await db.fetchrow(
            """SELECT COUNT(*) AS c FROM signals
               WHERE sent_at > NOW() - INTERVAL '7 days' AND outcome IN ('TP1','TP2','TP3')"""
        )
        month_signals = await db.fetchrow(
            "SELECT COUNT(*) AS c FROM signals WHERE sent_at > NOW() - INTERVAL '30 days'"
        )
        month_wins = await db.fetchrow(
            """SELECT COUNT(*) AS c FROM signals
               WHERE sent_at > NOW() - INTERVAL '30 days' AND outcome IN ('TP1','TP2','TP3')"""
        )

        return web.json_response({
            "precision": {
                "total_signals": p_total["c"],
                "win_rate_tp1": round(float(p_win_tp1["w"]), 2),
                "win_rate_tp2": round(float(p_win_tp2["w"]), 2),
                "avg_rr_achieved": round(float(p_avg_rr["a"]), 2),
                "score_10_11_win_rate": round(float(p_score_10_11["w"]), 2),
                "score_12_13_win_rate": round(float(p_score_12_13["w"]), 2),
                "score_14_15_win_rate": round(float(p_score_14_15["w"]), 2),
            },
            "flow": {
                "total_signals": f_total["c"],
                "win_rate_tp1": round(float(f_win_tp1["w"]), 2),
                "avg_rr_achieved": round(float(f_avg_rr["a"]), 2),
                "score_6_win_rate": round(float(f_score_6["w"]), 2),
                "score_7_win_rate": round(float(f_score_7["w"]), 2),
                "score_8_win_rate": round(float(f_score_8["w"]), 2),
            },
            "combined": {
                "total_signals_30d": total_30d["c"],
                "overall_win_rate": round(float(overall_win["w"]), 2),
                "best_pair": best_pair["pair"] if best_pair else None,
                "this_week_signals": week_signals["c"],
                "this_week_wins": week_wins["c"],
                "this_month_signals": month_signals["c"],
                "this_month_wins": month_wins["c"],
            },
        })
    except Exception as e:
        logger.error("Stats handler error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


def make_app(db):
    """Build aiohttp app with /stats and /paystack/webhook endpoints."""
    app = web.Application()
    app["db"] = db
    app.router.add_get("/stats", stats_handler)
    app.router.add_post("/paystack/webhook", handle_paystack_webhook)
    return app
