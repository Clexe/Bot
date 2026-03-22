import logging
import sys

_configured = False


def get_logger(name: str) -> logging.Logger:
    """Return standardized logger for all Signalix services."""
    global _configured
    if not _configured:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            stream=sys.stdout,
        )
        _configured = True
    return logging.getLogger(name)
