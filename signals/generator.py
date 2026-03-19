from datetime import datetime
from strategy.detectors import detect_kill_zone
from strategy.scoring import score_setup

async def generate_signal(pair: str, direction: str, trade_levels: dict, context: dict):
    """Assemble final signal dictionary if all hard-gate conditions pass."""
    in_kz, kz_name = await detect_kill_zone(datetime.utcnow())
    hard_gates = [context['storyline_established'], context['poi_fresh'], context['idm_swept'], in_kz and kz_name in {'London','New York'}, context['ltf_shift_confirmed']]
    if not all(hard_gates):
        return None, 'Hard gate failed'
    score = await score_setup(context['storyline_established'], context['poi_fresh'], context['liquidity_swept'], in_kz, context['fvg_confirmed'])
    signal = {'pair': pair, 'direction': direction, 'kill_zone': kz_name, 'score': score, **trade_levels, **context}
    return signal, None
