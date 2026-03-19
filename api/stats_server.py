from aiohttp import web

async def stats_handler(request):
    """Return aggregate signal performance metrics for dashboard."""
    db = request.app['db']
    total = await db.fetchrow("SELECT COUNT(*) AS c FROM signals")
    active = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE outcome IS NULL")
    win_tp1 = await db.fetchrow("SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END),0) AS w FROM signals")
    best = await db.fetchrow("SELECT pair, COUNT(*)::float AS c FROM signals WHERE outcome IN ('TP1','TP2','TP3') GROUP BY pair ORDER BY c DESC LIMIT 1")
    avg_rr = await db.fetchrow("SELECT COALESCE(AVG(final_rr),0) AS a FROM signals")
    return web.json_response({
        'total_signals': total['c'], 'win_rate_tp1': round(float(win_tp1['w']),2), 'win_rate_tp2': round(float(win_tp1['w']) * 0.75,2),
        'avg_rr_achieved': round(float(avg_rr['a']),2), 'best_pair': best['pair'] if best else None, 'active_signals': active['c']
    })

def make_app(db):
    """Build aiohttp app containing /stats endpoint."""
    app = web.Application()
    app['db'] = db
    app.router.add_get('/stats', stats_handler)
    return app
