from datetime import datetime
from config import KILL_ZONES, PIP_SIZE


def in_kill_zone(utc_now: datetime = None) -> dict:
    if utc_now is None:
        utc_now = datetime.utcnow()
    t = utc_now.time() if hasattr(utc_now, "time") else utc_now
    for kz in KILL_ZONES:
        if kz["start"] <= t <= kz["end"]:
            return {"active": True, "session": kz["name"]}
    return {"active": False, "session": "Off-Hours"}


def price_to_pips(pair: str, price_diff: float) -> float:
    pip = PIP_SIZE.get(pair, 0.0001)
    return abs(price_diff) / pip


def pips_to_price(pair: str, pips: float) -> float:
    pip = PIP_SIZE.get(pair, 0.0001)
    return pips * pip
