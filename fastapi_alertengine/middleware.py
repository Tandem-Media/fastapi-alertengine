# fastapi_alertengine/middleware.py
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import AlertConfig
from .storage import write_metric

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = AlertConfig()


def _classify(path: str) -> str:
    return "webhook" if "webhook" in path else "api"


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    Starlette/FastAPI middleware that measures request latency and writes
    one event per request to a Redis Stream for later aggregation.

    Failures are swallowed — metrics never affect the request path.

    Args:
        app:    The ASGI application.
        redis:  Synchronous ``redis.Redis`` client.  Pass ``None`` to disable
                (useful in unit tests that don't need a Redis instance).
        config: :class:`~fastapi_alertengine.config.AlertConfig` instance.
                Defaults to library defaults if omitted.

    Usage::

        from fastapi_alertengine import RequestMetricsMiddleware
        import redis

        rdb = redis.Redis.from_url("redis://localhost:6379")
        app.add_middleware(RequestMetricsMiddleware, redis=rdb)
    """

    def __init__(self, app, redis=None, config: AlertConfig = _DEFAULT_CONFIG):
        super().__init__(app)
        self._rdb    = redis
        self._config = config

    async def dispatch(self, request: Request, call_next) -> Response:
        t0       = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - t0) * 1_000

        if self._rdb is not None:
            write_metric(
                rdb        = self._rdb,
                config     = self._config,
                path       = request.url.path,
                method     = request.method,
                status_code= response.status_code,
                latency_ms = latency_ms,
            )

        return response
