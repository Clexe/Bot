import asyncio
import time
from config import RATE_LIMIT_MESSAGES_PER_SECOND, logger


class RateLimiter:
    """Token bucket rate limiter for Telegram API calls.

    Telegram allows ~30 messages per second globally, and ~1 message
    per second per chat. This limiter handles the global limit.
    """

    def __init__(self, rate=RATE_LIMIT_MESSAGES_PER_SECOND):
        self._rate = rate
        self._tokens = rate
        self._max_tokens = rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until a token is available, then consume it.

        The lock is released before sleeping so other coroutines aren't
        blocked while we wait for token replenishment.
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._max_tokens, self._tokens + elapsed * self._rate)
                self._last_refill = now

                if self._tokens >= 1:
                    self._tokens -= 1
                    return  # token acquired

                wait_time = (1 - self._tokens) / self._rate

            # Sleep OUTSIDE the lock so other coroutines aren't serialized
            logger.debug("Rate limiter: waiting %.2fs", wait_time)
            await asyncio.sleep(wait_time)

    async def send_message(self, bot, chat_id, text, **kwargs):
        """Send a message with rate limiting applied.

        Args:
            bot: Telegram bot instance
            chat_id: Target chat ID
            text: Message text
            **kwargs: Additional send_message kwargs

        Returns:
            The sent Message object
        """
        await self.acquire()
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)


# Global rate limiter instance
rate_limiter = RateLimiter()
