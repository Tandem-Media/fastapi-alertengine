# fastapi_alertengine/middleware.py

import time
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .engine import AlertEngine


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware that times every request and enqueues the
    metric for asynchronous persistence to Redis Streams via ``engine.drain()``.

    The enqueue is always non-blocking: ``engine.enqueue_metric`` uses
    ``Queue.put_nowait`` and silently drops the metric when the queue is full.
    """

    def __init__(self, app, alert_engine: AlertEngine) -> None:
        super().__init__(app)
        self.alert_engine = alert_engine

    async def dispatch(self, request: Request, call_next: Callable):
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000

        # service_name and instance_id are added by enqueue_metric from config.
        self.alert_engine.enqueue_metric({
            "path":        request.url.path,
            "method":      request.method,
            "status_code": response.status_code,
            "latency_ms":  latency_ms,
        })

        return response
