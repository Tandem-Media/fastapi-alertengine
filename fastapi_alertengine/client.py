# fastapi_alertengine/client.py

from functools import lru_cache
from typing import Optional

from .config import AlertConfig
from .engine import AlertEngine


@lru_cache(maxsize=1)
def get_alert_engine(config: Optional[AlertConfig] = None, redis_client: Optional[object] = None) -> AlertEngine:
    """
    Return a singleton AlertEngine instance.

    For now, you must pass a redis_client; later you can build it from config.redis_url.
    """
    if redis_client is None:
        raise ValueError("redis_client is required for get_alert_engine()")

    if config is None:
        config = AlertConfig()

    # AlertEngine currently just takes a redis client; you can ignore config for now or
    # later wire in config.stream_key, etc.
    return AlertEngine(redis=redis_client)