from decimal import Decimal


def calculate_rr(entry: float, sl: float, tp: float) -> float:
    """Calculate risk:reward multiple from entry, stop loss, and target."""
    risk = abs(Decimal(str(entry)) - Decimal(str(sl)))
    reward = abs(Decimal(str(tp)) - Decimal(str(entry)))
    return float(round(reward / risk, 2)) if risk else 0.0


def in_round_number(price: float, pip_size: float = 0.0001) -> bool:
    """Return True when price sits on a rounded 10-pip grid."""
    grid = pip_size * 10
    if grid == 0:
        return False
    return abs(price % grid) < pip_size * 0.1


def get_pip_value(pair: str) -> float:
    """Return pip size for a given pair."""
    if pair in ("USDJPY",):
        return 0.01
    if pair in ("XAUUSD",):
        return 0.1
    if pair in ("XAGUSD",):
        return 0.01
    if pair in ("BTCUSDT",):
        return 1.0
    if pair in ("ETHUSDT",):
        return 0.1
    return 0.0001


def calculate_pips(entry: float, exit_price: float, pair: str) -> float:
    """Calculate pips between two prices."""
    pip_size = get_pip_value(pair)
    if pip_size == 0:
        return 0.0
    return round(abs(exit_price - entry) / pip_size, 1)


def timeframe_to_label(tf: str) -> str:
    """Map timeframe code to display label."""
    labels = {
        "M": "Monthly", "W": "Weekly", "D": "Daily",
        "H4": "H4", "H1": "H1", "M15": "M15", "M5": "M5",
    }
    return labels.get(tf, tf)


async def check_duplicate_signal(db, pair: str, hours: int) -> bool:
    """Check if an identical pair signal was sent within the prevention window."""
    result = await db.fetchrow(
        "SELECT id FROM signals WHERE pair=%s AND sent_at > NOW() - make_interval(hours => %s) ORDER BY sent_at DESC LIMIT 1",
        (pair, hours),
    )
    return result is not None
