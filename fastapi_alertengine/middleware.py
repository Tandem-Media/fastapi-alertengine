# fastapi_alertengine/middleware.py
import time
from typing import Callable
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from .engine import AlertEngine


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware that times every request and enqueues
    the metric for async persistence to Redis Streams via engine.drain().
    """

    def __init__(self, app, alert_engine: AlertEngine) -> None:
        super().__init__(app)
        self._engine = alert_engine

    async def dispatch(self, request: Request, call_next: Callable):
        start    = time.perf_counter()
        status_code = 500  # safe default
        try:
            response    = await call_next(request)
            status_code = response.status_code
        except Exception:
            raise
        finally:
            latency_ms = (time.perf_counter() - start) * 1_000
            try:
                self._engine.enqueue_metric({
                    "path":        request.url.path,
                    "method":      request.method,
                    "status_code": status_code,
                    "latency_ms":  latency_ms,
                })
            except Exception:
                pass
        return response
