# fastapi_alertengine/middleware.py
"""
v1.4 — Event Enrichment Layer

Changes from v1.3:
- Route template capture: /users/{id} instead of /users/123
- HTTP method already present but now sourced from scope for reliability
- Optional trace_id / request_id passthrough (X-Request-ID, X-Trace-ID headers)
- Optional metadata injection hook: metadata_extractor callable
- All new fields are opt-in / safely default to None
- No measurable latency increase — all extraction is synchronous O(1)
"""
import os
import time
from typing import Callable, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from .engine import AlertEngine


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that captures per-request metrics and enqueues them
    for async persistence via AlertEngine.drain().

    Parameters
    ----------
    app:
        The ASGI application.
    alert_engine:
        An AlertEngine instance (required).
    metadata_extractor:
        Optional callable(request) -> dict that injects extra key/value pairs
        into the metric event. Must never raise — exceptions are swallowed.
        Keys are namespaced under "meta" in the stored event.
        Example::

            def extract(request):
                return {"user_id": request.headers.get("X-User-ID")}

            app.add_middleware(
                RequestMetricsMiddleware,
                alert_engine=engine,
                metadata_extractor=extract,
            )
    """

    def __init__(
        self,
        app,
        alert_engine: AlertEngine,
        metadata_extractor: Optional[Callable] = None,
    ) -> None:
        super().__init__(app)
        self._engine = alert_engine
        self._metadata_extractor = metadata_extractor

    # ── Route template resolution ─────────────────────────────────────────────
    @staticmethod
    def _resolve_route_template(request: Request) -> str:
        """
        Return the route template path (/users/{id}) rather than the raw URL
        path (/users/123).

        Falls back to request.url.path if no matching route is found —
        preserving behaviour for unmatched paths (404s etc.).

        This is the single most important fix in v1.4: without it, every
        parameterised URL appears as a unique endpoint in analytics, making
        endpoint aggregation useless.
        """
        # Starlette stores the matched route in scope["route"] after routing.
        # We prefer that over iterating routes manually — O(1) vs O(n).
        route = request.scope.get("route")
        if route is not None and hasattr(route, "path"):
            return route.path  # e.g. "/users/{id}"

        # Fallback: iterate the router's routes and find the first match.
        # This path is taken when middleware fires before routing completes.
        app = request.app
        routes = getattr(getattr(app, "router", None), "routes", [])
        for route in routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                if hasattr(route, "path"):
                    return route.path

        # Final fallback: raw URL path (v1.3 behaviour).
        return request.url.path

    # ── Trace / request ID extraction ─────────────────────────────────────────
    @staticmethod
    def _extract_trace_id(request: Request) -> Optional[str]:
        """
        Extract a trace/request ID from common headers.

        Checks in order:
          X-Request-ID  (most common in load balancers / API gateways)
          X-Trace-ID    (common in distributed tracing setups)
          X-Correlation-ID

        Returns None if none of the headers are present.
        All fields are opt-in — no ID is generated if none is provided.
        """
        for header in ("x-request-id", "x-trace-id", "x-correlation-id"):
            value = request.headers.get(header)
            if value:
                return value[:128]  # cap length — defensive
        return None

    # ── Metadata extraction ───────────────────────────────────────────────────
    def _extract_metadata(self, request: Request) -> Optional[dict]:
        """
        Call the user-supplied metadata_extractor, swallowing any exception.
        Returns None if no extractor is configured or if it raises.
        """
        if self._metadata_extractor is None:
            return None
        try:
            result = self._metadata_extractor(request)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return None

    # ── Main dispatch ─────────────────────────────────────────────────────────
    async def dispatch(self, request: Request, call_next: Callable):
        start       = time.perf_counter()
        status_code = 500  # safe default

        # Extract trace_id before calling the route handler so it's always
        # available even if an exception occurs downstream.
        trace_id = self._extract_trace_id(request)
        metadata = self._extract_metadata(request)

        try:
            response    = await call_next(request)
            status_code = response.status_code
        except Exception:
            raise
        finally:
            latency_ms = (time.perf_counter() - start) * 1_000

            # Route template — resolved after routing so scope["route"] is set.
            route_path = self._resolve_route_template(request)

            is_first = self._engine._first_request_at is None
            if is_first:
                self._engine._first_request_at = time.time()

            try:
                metric = {
                    "path":        route_path,          # ← route template (v1.4)
                    "method":      request.method,
                    "status_code": status_code,
                    "latency_ms":  latency_ms,
                }
                # Attach optional enrichment fields — only if present.
                if trace_id is not None:
                    metric["trace_id"] = trace_id
                if metadata:
                    metric["meta"] = metadata

                self._engine.enqueue_metric(metric)
            except Exception:
                pass

            if is_first:
                print(f"⚡ First request detected")
                print(f"  Service:  {self._engine.config.service_name}")
                print(f"  Route:    {route_path}")
                print(f"  Latency:  {latency_ms:.1f}ms")
                print(f"  Status:   {status_code}")
                if trace_id:
                    print(f"  Trace-ID: {trace_id}")
                if not os.getenv("ACTION_SECRET_KEY"):
                    print(f"\n💡 Tip: Enable incident actions:")
                    print(f"   from fastapi_alertengine import actions_router")
                    print(f"   app.include_router(actions_router)")
                print()

        return response
