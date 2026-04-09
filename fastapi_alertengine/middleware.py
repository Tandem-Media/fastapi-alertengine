# fastapi_alertengine/middleware.py

from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .engine import AlertEngine


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    Minimal middleware that forwards request info to AlertEngine.

    You can expand this later to track real latency and custom dimensions.
    """

    def __init__(self, app, alert_engine: AlertEngine) -> None:
        super().__init__(app)
        self.alert_engine = alert_engine

    async def dispatch(self, request: Request, call_next: Callable):
        # TODO: add real timing; for now, just call the downstream app
        response = await call_next(request)

        # Example hook point for future: record request metrics in Redis stream, etc.
        # self.alert_engine.record_request(...)

        return response