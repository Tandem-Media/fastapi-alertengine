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

__version__ = "1.2.0"


def instrument(
    app: FastAPI,
    redis_url: Optional[str] = None,
    config: Optional[AlertConfig] = None,
    health_path: str = "/health/alerts",
) -> AlertEngine:
    """
    Instrument a FastAPI app with alertengine in one line.

    Wires the request metrics middleware, a background drain task, and three
    observability endpoints automatically.  Redis URL resolution order:

    1. The *redis_url* argument.
    2. The ``ALERTENGINE_REDIS_URL`` environment variable.
    3. ``redis://localhost:6379/0`` (built-in default).

    Auto-registered endpoints
    -------------------------
    GET  *health_path*         — evaluate() result (default ``/health/alerts``)
    POST /alerts/evaluate      — evaluate() + optional Slack delivery
    GET  /metrics/history      — recent raw metrics from Redis Stream

    Usage::

        from fastapi import FastAPI
        from fastapi_alertengine import instrument

        app = FastAPI()
        instrument(app)

    Parameters
    ----------
    app:
        The FastAPI application to instrument.
    redis_url:
        Optional Redis URL.  Overrides the environment variable and default.
    config:
        A pre-built :class:`AlertConfig`.  When provided, *redis_url* is
        ignored (embed the URL in the config instead).
    health_path:
        Path for the auto-registered health/evaluation endpoint.
        Defaults to ``"/health/alerts"``.

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

    async def _start_drain() -> None:
        asyncio.create_task(engine.drain())

    app.router.on_startup.append(_start_drain)

    # ── GET /health/alerts ────────────────────────────────────────────────────
    @app.get(health_path, include_in_schema=False)
    def _health_alerts():
        return engine.evaluate()

    # ── POST /alerts/evaluate ─────────────────────────────────────────────────
    @app.post("/alerts/evaluate", include_in_schema=False)
    async def _alerts_evaluate():
        """Evaluate + deliver to Slack if configured and not rate-limited."""
        result = engine.evaluate()
        await engine.deliver_alert(result)
        return result

    # ── GET /metrics/history ──────────────────────────────────────────────────
    @app.get("/metrics/history", include_in_schema=False)
    def _metrics_history(last_n: int = 100):
        return {"metrics": engine.history(last_n=last_n)}

    return engine

