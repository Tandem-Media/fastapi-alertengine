# fastapi_alertengine/client.py

import warnings
from typing import Optional

import redis as redis_lib

from .config import AlertConfig
from .engine import AlertEngine

_engine: Optional[AlertEngine] = None


def get_alert_engine(
    config: Optional[AlertConfig] = None,
    redis_client: Optional[object] = None,
) -> AlertEngine:
    """
    Return (and cache) an ``AlertEngine`` singleton.

    If *redis_client* is omitted a new client is created from
    ``config.redis_url`` with ``decode_responses=True``.

    If a caller supplies their own *redis_client*, a warning is emitted when
    ``decode_responses`` is not enabled, because Redis would then return raw
    bytes and metrics would not be readable.
    """
    global _engine
    if _engine is not None:
        return _engine

    if config is None:
        config = AlertConfig()

    if redis_client is None:
        redis_client = redis_lib.Redis.from_url(
            config.redis_url,
            decode_responses=True,
        )
    else:
        _warn_if_no_decode_responses(redis_client)

    _engine = AlertEngine(redis=redis_client, config=config)
    return _engine


def _warn_if_no_decode_responses(client: object) -> None:
    pool   = getattr(client, "connection_pool", None)
    kwargs = getattr(pool, "connection_kwargs", {}) if pool else {}
    if not kwargs.get("decode_responses", False):
        warnings.warn(
            "fastapi-alertengine: redis_client should be created with "
            "decode_responses=True. Metrics may not be readable.",
            stacklevel=3,
        )


def _reset_engine() -> None:
    """Reset the cached singleton. Intended for use in tests only."""
    global _engine
    _engine = None
