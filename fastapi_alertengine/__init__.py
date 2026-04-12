# fastapi_alertengine/__init__.py

import asyncio
from typing import Optional

import redis as redis_lib
from fastapi import FastAPI

from .config import AlertConfig
from .engine import AlertEngine
from .middleware import RequestMetricsMiddleware
from .client import get_alert_engine
from .storage import aggregate, write_batch

__all__ = [
    "AlertEngine",
    "RequestMetricsMiddleware",
    "get_alert_engine",
    "AlertConfig",
    "aggregate",
    "write_batch",
    "instrument",
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

    Wires the request metrics middleware, background drain + alert delivery
    tasks, and four observability endpoints automatically.

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

    app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

    async def _start_background_tasks() -> None:
        asyncio.create_task(engine.drain())
        asyncio.create_task(engine.alert_delivery_loop())

    async def _shutdown() -> None:
        await engine.flush_all_aggregates()

    app.router.on_startup.append(_start_background_tasks)
    app.router.on_shutdown.append(_shutdown)

    # ── GET /health/alerts ────────────────────────────────────────────────────
    @app.get(health_path, include_in_schema=False)
    def _health_alerts():
        return engine.evaluate()

    # ── POST /alerts/evaluate ─────────────────────────────────────────────────
    @app.post("/alerts/evaluate", include_in_schema=False)
    def _alerts_evaluate():
        """Evaluate and enqueue alert for background Slack delivery."""
        result = engine.evaluate()
        engine.enqueue_alert(result)  # non-blocking; processed by alert_delivery_loop
        return result

    # ── GET /metrics/history ──────────────────────────────────────────────────
    @app.get("/metrics/history", include_in_schema=False)
    def _metrics_history(service: Optional[str] = None, last_n_buckets: int = 10):
        """Aggregated per-minute metrics filtered by service."""
        return {"metrics": engine.aggregated_history(service=service, last_n_buckets=last_n_buckets)}

    # ── GET /metrics/ingestion ────────────────────────────────────────────────
    @app.get("/metrics/ingestion", include_in_schema=False)
    def _metrics_ingestion():
        """Ingestion counters: enqueued, dropped, last_drain_at."""
        return engine.get_ingestion_stats()

    return engine

