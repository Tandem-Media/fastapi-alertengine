# fastapi_alertengine/middleware.py
import time
import asyncio
from typing import Callable
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from .engine import AlertEngine


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    Truly non-blocking ASGI middleware.

    - Captures request metrics (latency, status_code, path, method)
    - Enqueues into in-memory deque -- zero Redis I/O on the hot path
    - Returns response immediately
    - Background drain() coroutine handles Redis writes asynchronously

    Wire the background task at app startup::

        @app.on_event('startup')
        async def start_drain():
            asyncio.create_task(engine.drain())
    """

    def __init__(self, app, alert_engine: AlertEngine) -> None:
        super().__init__(app)
        self._engine = alert_engine

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start       = time.perf_counter()
        status_code = 500  # default -- overwritten on success
        try:
            response    = await call_next(request)
            status_code = response.status_code
        except Exception:
            # Re-raise so FastAPI exception handlers still run;
            # status_code stays 500 and is captured in finally.
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1_000
            try:
                self._engine.enqueue_metric({
                    "path":        request.url.path,
                    "method":      request.method,
                    "status_code": status_code,
                    "latency_ms":  elapsed,
                })
            except Exception:
                # Never break the request path
                pass
        return response
