# fastapi_alertengine/client.py
"""
get_alert_engine() — process-level singleton factory.

Lazily constructs one AlertEngine per (redis_url, config) pair and
caches it for the lifetime of the process.  Safe to call from anywhere
without worrying about duplicate Redis connections.

Usage::

    from fastapi_alertengine import get_alert_engine

    engine = get_alert_engine(redis_url="redis://localhost:6379")
    event  = engine.evaluate()
"""

import threading
from typing import Optional

import redis as _redis_lib

from .config import AlertConfig
from .engine import AlertEngine

_lock     = threading.Lock()
_registry: dict = {}


def get_alert_engine(
    redis_url: str                  = "redis://localhost:6379",
    redis_client                    = None,
    config:    Optional[AlertConfig] = None,
) -> AlertEngine:
    """
    Return a cached :class:`~fastapi_alertengine.engine.AlertEngine`.

    Pass either ``redis_url`` (a connection string) or an already-constructed
    ``redis_client`` instance — not both.  If ``redis_client`` is supplied it
    is used directly and ``redis_url`` is ignored.

    Args:
        redis_url:    Redis connection string (used if ``redis_client`` is None).
        redis_client: Pre-built ``redis.Redis`` instance (optional).
        config:       :class:`~fastapi_alertengine.config.AlertConfig`.
                      Defaults to library defaults.

    Returns:
        A shared :class:`~fastapi_alertengine.engine.AlertEngine` instance.
    """
    cfg = config or AlertConfig()
    key = id(redis_client) if redis_client is not None else redis_url

    if key not in _registry:
        with _lock:
            if key not in _registry:
                rdb = redis_client or _redis_lib.Redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                    retry_on_timeout=True,
                )
                _registry[key] = AlertEngine(redis=rdb, config=cfg)

    return _registry[key]
