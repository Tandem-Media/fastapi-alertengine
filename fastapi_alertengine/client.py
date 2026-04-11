# fastapi_alertengine/client.py
"""
Singleton factory for AlertEngine.

Zero-config usage (reads ALERTENGINE_* env vars):
    engine = get_alert_engine()

Explicit:
    engine = get_alert_engine(
        config       = AlertConfig(redis_url="redis://myhost:6379/0"),
        redis_client = redis.Redis.from_url("redis://myhost:6379/0",
                                            decode_responses=True),
    )

FastAPI dependency:
    @app.get("/health/alerts")
    def health(engine: AlertEngine = Depends(get_alert_engine)):
        return engine.evaluate().as_dict()
"""

from typing import Optional

import redis as redis_lib

from .config import AlertConfig
from .engine import AlertEngine

# Module-level singleton — avoids lru_cache's requirement that all
# arguments be hashable (Pydantic BaseSettings is not hashable).
_instance: Optional[AlertEngine] = None


def get_alert_engine(
    config:       Optional[AlertConfig] = None,
    redis_client: Optional[object]      = None,
) -> AlertEngine:
    """
    Return a process-wide singleton AlertEngine.

    On first call, constructs the engine from the supplied (or default) config.
    Subsequent calls return the same instance regardless of arguments.

    Call clear_alert_engine() to reset the singleton (useful in tests).
    """
    global _instance
    if _instance is not None:
        return _instance

    if config is None:
        config = AlertConfig()

    if redis_client is None:
        redis_client = redis_lib.Redis.from_url(
            config.redis_url,
            decode_responses=True,
        )

    _instance = AlertEngine(config=config, redis=redis_client)
    return _instance


def clear_alert_engine() -> None:
    """Reset the singleton — primarily for use in tests."""
    global _instance
    _instance = None
