# fastapi_alertengine/__init__.py

from typing import Optional

import redis as redis_lib
from fastapi import FastAPI

from .config import AlertConfig
from .engine import AlertEngine
from .middleware import RequestMetricsMiddleware
from .client import get_alert_engine
from .storage import aggregate, write_batch
from .actions.router import router as actions_router

__all__ = [
    "AlertEngine",
    "RequestMetricsMiddleware",
    "get_alert_engine",
    "AlertConfig",
    "aggregate",
    "write_batch",
    "instrument",
    "actions_router",
]

__version__ = "1.3.0"


def instrument(
    app: FastAPI,
    redis_url: Optional[str] = None,
    config: Optional[AlertConfig] = None,
    health_path: str = "/health/alerts",
) -> AlertEngine:
    """
    Instrument a FastAPI app with alertengine in one line.

    Automatically detects whether Redis is available and falls back to
    in-memory mode when it is not.  All background tasks, middleware, and
    observability endpoints are registered without any further action from
    the caller.

    Redis URL resolution order:
    1. The *redis_url* argument.
    2. The ``ALERTENGINE_REDIS_URL`` environment variable.
    3. ``redis://localhost:6379/0`` (built-in default).

    Auto-registered endpoints
    -------------------------
    GET  *health_path*         — evaluate() result (default ``/health/alerts``)
    POST /alerts/evaluate      — evaluate() + enqueue for Slack delivery
    GET  /metrics/history      — aggregated metrics (filter by service)
    GET  /metrics/ingestion    — ingestion counters (enqueued / dropped)

    Returns
    -------
    AlertEngine
        The engine instance (useful for manual calls to ``evaluate()``).
    """
    if config is None:
        config = AlertConfig(redis_url=redis_url) if redis_url else AlertConfig()

    redis_client = redis_lib.Redis.from_url(config.redis_url, decode_responses=True)
    engine = get_alert_engine(config=config, redis_client=redis_client)
    engine.start(app, health_path=health_path)
    return engine

