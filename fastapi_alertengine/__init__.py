# fastapi_alertengine/__init__.py
"""
fastapi_alertengine
===================

Production-grade request metrics middleware and SLO alert engine for
FastAPI services.  Backed by Redis Streams.

Quick start::

    from fastapi import FastAPI
    from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine
    import redis

    app = FastAPI()
    rdb = redis.Redis.from_url("redis://localhost:6379", decode_responses=True)

    app.add_middleware(RequestMetricsMiddleware, redis=rdb)

    @app.get("/health/alerts")
    def alert_status():
        engine = get_alert_engine(redis_client=rdb)
        event  = engine.evaluate()
        return {"status": event.status, "metrics": event.metrics}
"""

from .client     import get_alert_engine
from .config     import AlertConfig
from .engine     import AlertDeduplicator, AlertEngine
from .middleware  import RequestMetricsMiddleware
from .schemas    import AlertEvent, AlertMetrics, AlertThresholds, RequestMetricEvent
from .storage    import aggregate, read_metrics, write_metric

__all__ = [
    # Primary surface
    "RequestMetricsMiddleware",
    "AlertEngine",
    "AlertDeduplicator",
    "get_alert_engine",
    # Config + schemas
    "AlertConfig",
    "AlertEvent",
    "AlertMetrics",
    "AlertThresholds",
    "RequestMetricEvent",
    # Storage helpers
    "aggregate",
    "read_metrics",
    "write_metric",
]

__version__ = "1.1.0"
