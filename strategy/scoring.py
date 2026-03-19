async def score_setup(storyline_ok: bool, fresh_poi: bool, liquidity: bool, kill_zone: bool, fvg: bool) -> int:
    """Compute 0-10 setup score from weighted confluence rules."""
    score = 0
    score += 3 if storyline_ok else 0
    score += 2 if fresh_poi else 0
    score += 2 if liquidity else 0
    score += 2 if kill_zone else 0
    score += 1 if fvg else 0
    return score
