async def refresh_markets(force=False):
    """
    Cached for 6 hours.
    Commodities + indices are optional (often slow).
    NEVER throws — it logs and returns.
    """
    if MARKETS["loading"]:
        return
    if (not force) and MARKETS["last"] and (now() - MARKETS["last"]) < timedelta(hours=6):
        return

    MARKETS["loading"] = True

    def _fetch():
        fx = twelve_get("forex_pairs").get("data", [])
        cr = twelve_get("cryptocurrencies").get("data", [])

        # optional endpoints
        try:
            cm = twelve_get("commodities").get("data", [])
        except Exception:
            cm = []
        try:
            ix = twelve_get("indices").get("data", [])
        except Exception:
            ix = []

        return (
            {x.get("symbol", "").upper() for x in fx if x.get("symbol")},
            {x.get("symbol", "").upper() for x in cr if x.get("symbol")},
            {x.get("symbol", "").upper() for x in cm if x.get("symbol")},
            {x.get("symbol", "").upper() for x in ix if x.get("symbol")},
        )

    try:
        fx, cr, cm, ix = await asyncio.to_thread(_fetch)
        MARKETS["forex"] = fx
        MARKETS["crypto"] = cr
        MARKETS["commodities"] = cm
        MARKETS["indices"] = ix
        MARKETS["last"] = now()
        log("Markets refreshed:", len(fx), len(cr), len(cm), len(ix))
    except Exception as e:
        log("refresh_markets failed:", e)
    finally:
        MARKETS["loading"] = False