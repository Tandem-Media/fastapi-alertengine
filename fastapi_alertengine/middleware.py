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

    On the very first request it also prints a one-time "first signal
    detected" summary so developers see immediate feedback that the engine
    is active, along with a hint to enable incident actions if they are not
    already configured.
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
            is_first = self._engine._first_request_at is None
            if is_first:
                self._engine._first_request_at = time.time()
            try:
                self._engine.enqueue_metric({
                    "path":        request.url.path,
                    "method":      request.method,
                    "status_code": status_code,
                    "latency_ms":  latency_ms,
                })
            except Exception:
                pass
            if is_first:
                print(f"📡 First request detected")
                print(f"  Service: {self._engine.config.service_name}")
                print(f"  Path:    {request.url.path}")
                print(f"  Latency: {latency_ms:.1f}ms")
                print(f"  Status:  {status_code}")
                # Progressive hint if actions router is not mounted
                import os as _os
                if not _os.getenv("ACTION_SECRET_KEY"):
                    print(f"\n💡 Tip: Enable incident actions:")
                    print(f"   from fastapi_alertengine import actions_router")
                    print(f"   app.include_router(actions_router)")
                print()
        return response

