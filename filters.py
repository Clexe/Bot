import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from config import (
    USE_NEWS_FILTER, NEWS_IMPACT, NEWS_CACHE_TTL, NEWS_BLACKOUT_MINUTES,
    ALWAYS_OPEN_KEYS, logger,
)

# Module-level state
_NEWS_CACHE = []
_LAST_NEWS_FETCH = 0


def fetch_forex_news():
    """Fetch forex news events from calendar. Cached for NEWS_CACHE_TTL seconds."""
    global _NEWS_CACHE, _LAST_NEWS_FETCH
    if time.time() - _LAST_NEWS_FETCH < NEWS_CACHE_TTL:
        return

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        events = []
        for event in root.findall('event'):
            impact = event.find('impact').text
            if impact not in NEWS_IMPACT:
                continue

            date = event.find('date').text
            time_str = event.find('time').text
            currency = event.find('country').text

            if "am" in time_str or "pm" in time_str:
                dt_str = f"{date} {time_str}"
                dt_obj = None
                for fmt in ("%m-%d-%Y %I:%M%p", "%Y-%m-%d %I:%M%p"):
                    try:
                        dt_obj = datetime.strptime(dt_str, fmt)
                        break
                    except ValueError:
                        continue
                if dt_obj is None:
                    logger.warning("Unparseable news date: %s", dt_str)
                    continue
                events.append({"currency": currency, "time": dt_obj})

        _NEWS_CACHE = events
        _LAST_NEWS_FETCH = time.time()
        logger.info("Fetched %d news events", len(events))
    except Exception as e:
        logger.error("News fetch error: %s", e)


def is_news_blackout(pair):
    """Check if a pair is within a news blackout window."""
    if not USE_NEWS_FILTER:
        return False
    fetch_forex_news()
    currencies = set()
    for code in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"):
        if code in pair:
            currencies.add(code)
    if "XAU" in pair:
        currencies.add("USD")

    now = datetime.utcnow()
    for event in _NEWS_CACHE:
        if event['currency'] in currencies:
            diff = (event['time'] - now).total_seconds() / 60
            if -NEWS_BLACKOUT_MINUTES <= diff <= NEWS_BLACKOUT_MINUTES:
                return True
    return False


def is_in_session(session_type):
    """Check if current time is within the specified trading session."""
    now_hour = datetime.utcnow().hour
    if session_type == "LONDON":
        return 8 <= now_hour <= 16
    if session_type == "NY":
        return 13 <= now_hour <= 21
    return True  # BOTH


def is_market_open(pair):
    """Check if the market for a given pair is currently open."""
    clean = pair.upper()
    if any(k in clean for k in ALWAYS_OPEN_KEYS):
        return True

    now = datetime.utcnow()
    weekday = now.weekday()
    hour = now.hour
    # Friday after 21:00 UTC
    if weekday == 4 and hour >= 21:
        return False
    # All Saturday
    if weekday == 5:
        return False
    # Sunday before 21:00 UTC
    if weekday == 6 and hour < 21:
        return False
    return True
