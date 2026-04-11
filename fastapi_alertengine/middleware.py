# fastapi_alertengine/middleware.py
"""
RequestMetricsMiddleware: drop-in ASGI middleware that captures per-request
latency and HTTP status, then writes a structured event to the Redis Stream.

Usage::

    from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

    engine = get_alert_engine()
    app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

That is the entire integration.  No further wiring required.
"""

import time
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .engine import AlertEngine
from .storage import write_metric


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    Minimal, non-blocking ASGI middleware.

    For every HTTP request it:
    1. Records wall-clock start time using time.perf_counter().
    2. Awaits the downstream response.
    3. Computes elapsed duration in milliseconds.
    4. Calls storage.write_metric() — which fires-and-forgets to Redis and
       never raises, so a Redis outage cannot affect the request path.

    Parameters
    ----------
    app:
        The ASGI application (injected automatically by Starlette/FastAPI).
    alert_engine:
        A fully initialised AlertEngine instance (use get_alert_engine()).
    """

    def __init__(self, app, alert_engine: AlertEngine) -> None:
        super().__init__(app)
        self._engine = alert_engine

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start    = time.perf_counter()
        response = await call_next(request)
        elapsed  = (time.perf_counter() - start) * 1_000  # → milliseconds

        write_metric(
            rdb         = self._engine.redis,
            config      = self._engine.config,
            path        = request.url.path,
            method      = request.method,
            status_code = response.status_code,
            latency_ms  = elapsed,
        )

        return response
