from decimal import Decimal

def calculate_rr(entry: float, sl: float, tp: float) -> float:
    """Calculate risk:reward multiple from entry, stop loss, and target."""
    risk = abs(Decimal(str(entry)) - Decimal(str(sl)))
    reward = abs(Decimal(str(tp)) - Decimal(str(entry)))
    return float(round(reward / risk, 2)) if risk else 0.0

def in_round_number(price: float, pip_size: float = 0.0001) -> bool:
    """Return True when price sits on a rounded 10-pip grid."""
    return int(price / (pip_size * 10)) == price / (pip_size * 10)
