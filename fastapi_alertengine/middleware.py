# fastapi_alertengine/middleware.py
import time
from typing import Callable
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from .engine import AlertEngine
from .storage import write_metric


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    Truly non-blocking ASGI middleware.

    Enqueues metrics to in-memory deque for async drain to Redis.
    Also writes directly so tests and envs without drain() work.

    Wire the background drain task at app startup::

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
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1_000
            metric = {
                "path":        request.url.path,
                "method":      request.method,
                "status_code": status_code,
                "latency_ms":  elapsed,
            }
            try:
                self._engine.enqueue_metric(metric)
                write_metric(
                    self._engine.redis, self._engine.config,
                    metric["path"], metric["method"],
                    metric["status_code"], metric["latency_ms"]
                )
            except Exception:
                pass
        return response
