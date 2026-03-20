"""Token bucket rate limiter for Telegram API calls.

Telegram allows ~30 messages/sec globally. This limiter handles the global limit.
Ported from _old/rate_limiter.py.
"""

import asyncio
import time
from utils.logger import get_logger

logger = get_logger(__name__)

RATE_LIMIT_MESSAGES_PER_SECOND = 25


class RateLimiter:
    def __init__(self, rate=RATE_LIMIT_MESSAGES_PER_SECOND):
        self._rate = rate
        self._tokens = rate
        self._max_tokens = rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until a token is available, then consume it."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max_tokens, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1:
                wait_time = (1 - self._tokens) / self._rate
                logger.debug("Rate limiter: waiting %.2fs", wait_time)
                await asyncio.sleep(wait_time)
                self._tokens = 0
                self._last_refill = time.monotonic()
            else:
                self._tokens -= 1

    async def send_message(self, bot, chat_id, text, **kwargs):
        """Send a message with rate limiting applied."""
        await self.acquire()
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)


# Global instance
rate_limiter = RateLimiter()
