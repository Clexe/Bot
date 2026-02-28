"""Currency exposure tracking and correlation filter.

Prevents the bot from stacking correlated positions (e.g., BUY EURUSD +
BUY GBPUSD + BUY AUDUSD = 3x short USD) by tracking net currency exposure
across all open positions.

Also provides pair-level correlation groupings.
"""

from config import logger

# Currency pairs decomposed into base/quote
_PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"), "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"), "USDCAD": ("USD", "CAD"),
    "USDCHF": ("USD", "CHF"), "EURGBP": ("EUR", "GBP"),
    "EURJPY": ("EUR", "JPY"), "GBPJPY": ("GBP", "JPY"),
    "AUDCAD": ("AUD", "CAD"), "AUDCHF": ("AUD", "CHF"),
    "CADJPY": ("CAD", "JPY"), "CHFJPY": ("CHF", "JPY"),
    "EURAUD": ("EUR", "AUD"), "EURCAD": ("EUR", "CAD"),
    "EURCHF": ("EUR", "CHF"), "EURNZD": ("EUR", "NZD"),
    "GBPAUD": ("GBP", "AUD"), "GBPCAD": ("GBP", "CAD"),
    "GBPCHF": ("GBP", "CHF"), "GBPNZD": ("GBP", "NZD"),
    "NZDCAD": ("NZD", "CAD"), "NZDCHF": ("NZD", "CHF"),
    "NZDJPY": ("NZD", "JPY"), "AUDNZD": ("AUD", "NZD"),
    "AUDJPY": ("AUD", "JPY"),
    # Commodities — treated as commodity/USD
    "XAUUSD": ("XAU", "USD"), "XAGUSD": ("XAG", "USD"),
}

# Correlation groups: pairs that move similarly
CORRELATION_GROUPS = {
    "USD_SHORTS": {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"},
    "USD_LONGS": {"USDJPY", "USDCAD", "USDCHF"},
    "JPY_CROSSES": {"EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY"},
    "EUR_CROSSES": {"EURGBP", "EURAUD", "EURCAD", "EURCHF", "EURNZD"},
    "GBP_CROSSES": {"GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD"},
    "AUD_CROSSES": {"AUDCAD", "AUDCHF", "AUDNZD"},
    "COMMODITY": {"XAUUSD", "XAGUSD", "AUDUSD"},
}

# Max allowed exposure per currency (number of directional positions)
MAX_CURRENCY_EXPOSURE = 2

# Max positions in same correlation group with same direction
MAX_GROUP_SAME_DIRECTION = 2


def get_pair_currencies(pair):
    """Extract base and quote currencies from a pair.

    Returns (base, quote) or (None, None) for synthetics/crypto.
    """
    clean = pair.upper().replace("/", "")
    if clean in _PAIR_CURRENCIES:
        return _PAIR_CURRENCIES[clean]
    # Crypto and synthetics don't have currency correlation
    return None, None


def compute_currency_exposure(open_positions):
    """Compute net directional exposure per currency.

    Args:
        open_positions: list of dicts with 'pair' and 'direction' keys

    Returns:
        dict mapping currency -> net exposure
        (positive = long, negative = short)
    """
    exposure = {}

    for pos in open_positions:
        base, quote = get_pair_currencies(pos["pair"])
        if base is None:
            continue

        if pos["direction"] == "BUY":
            # Buying base, selling quote
            exposure[base] = exposure.get(base, 0) + 1
            exposure[quote] = exposure.get(quote, 0) - 1
        else:
            # Selling base, buying quote
            exposure[base] = exposure.get(base, 0) - 1
            exposure[quote] = exposure.get(quote, 0) + 1

    return exposure


def check_correlation(pair, direction, open_positions,
                      max_currency_exposure=MAX_CURRENCY_EXPOSURE,
                      max_group_same_dir=MAX_GROUP_SAME_DIRECTION):
    """Check if adding a new position would exceed correlation limits.

    Returns (allowed: bool, reason: str).
    """
    base, quote = get_pair_currencies(pair)

    # Crypto/synthetics bypass correlation check
    if base is None:
        return True, ""

    exposure = compute_currency_exposure(open_positions)

    # Check currency exposure
    if direction == "BUY":
        new_base_exp = exposure.get(base, 0) + 1
        new_quote_exp = exposure.get(quote, 0) - 1
    else:
        new_base_exp = exposure.get(base, 0) - 1
        new_quote_exp = exposure.get(quote, 0) + 1

    if abs(new_base_exp) > max_currency_exposure:
        return False, f"{base} exposure would be {new_base_exp} (max {max_currency_exposure})"

    if abs(new_quote_exp) > max_currency_exposure:
        return False, f"{quote} exposure would be {new_quote_exp} (max {max_currency_exposure})"

    # Check correlation group limits
    for group_name, group_pairs in CORRELATION_GROUPS.items():
        if pair not in group_pairs:
            continue

        same_dir_count = sum(
            1 for pos in open_positions
            if pos["pair"] in group_pairs and pos["direction"] == direction
        )

        if same_dir_count >= max_group_same_dir:
            return False, f"{group_name} group already has {same_dir_count} {direction} positions"

    return True, ""


def get_exposure_summary(open_positions):
    """Get a human-readable summary of current currency exposure.

    Returns a formatted string for status display.
    """
    exposure = compute_currency_exposure(open_positions)
    if not exposure:
        return "No currency exposure"

    parts = []
    for ccy, exp in sorted(exposure.items(), key=lambda x: abs(x[1]), reverse=True):
        if exp == 0:
            continue
        direction = "LONG" if exp > 0 else "SHORT"
        parts.append(f"{ccy}: {direction} x{abs(exp)}")

    return " | ".join(parts) if parts else "Neutral"
