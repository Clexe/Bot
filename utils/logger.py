import logging

def get_logger(name: str) -> logging.Logger:
    """Return standardized logger for all services."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return logging.getLogger(name)
