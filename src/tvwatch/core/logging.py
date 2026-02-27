import logging
import sys
from datetime import datetime, timezone
from .config import CONFIG

class ISO8601Formatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, timezone.utc).astimezone()
        return dt.isoformat(timespec="milliseconds")

def setup_logger(name: str, debug_mode: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    env_level = getattr(logging, CONFIG.LOG_LEVEL.upper(), logging.INFO)
    level = logging.DEBUG if debug_mode else env_level
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(ISO8601Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(handler)
    return logger