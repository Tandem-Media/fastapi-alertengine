import time
from typing import Callable

import redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI()


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_url: str = "redis://localhost:6379/0"):
        super().__init__(app)
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)

    async def dispatch(self, request: Request, call_next: Callable):
        start = time.time()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)

        duration_ms = (time.time() - start) * 1000
        # Write a minimal metric to Redis Stream
        self.redis.xadd(
            "request_metrics",
            {
                "path": request.url.path,
                "status": status_code,
                "duration_ms": f"{duration_ms:.2f}",
            },
            maxlen=1000,
        )
        return response


class AlertEngine:
    """
    AlertEngine Lite:
    - Reads from Redis Stream `request_metrics`
    - Tracks rolling error rate and P95 latency
    - For now: prints alerts to console (no email/slack yet)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        stream_key: str = "request_metrics",
        group: str = "alert_engine",
        consumer: str = "alert_engine_1",
        error_rate_threshold: float = 0.05,
        p95_latency_threshold_ms: float = 500.0,
        window_size: int = 100,
    ):
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self.stream_key = stream_key
        self.group = group
        self.consumer = consumer
        self.error_rate_threshold = error_rate_threshold
        self.p95_latency_threshold_ms = p95_latency_threshold_ms
        self.window_size = window_size

        # Create consumer group if it doesn't exist
        try:
            self.redis.xgroup_create(self.stream_key, self.group, id="$", mkstream=True)
        except redis.ResponseError as e:
            # Group already exists
            if "BUSYGROUP" not in str(e):
                raise

    def _calculate_alerts(self, events: list[dict]) -> None:
        if not events:
            return

        durations = []
        errors = 0

        for e in events:
            fields = e["fields"]
            status = int(fields.get("status", 500))
            duration_ms = float(fields.get("duration_ms", 0.0))
            durations.append(duration_ms)
            if status >= 500:
                errors += 1

        error_rate = errors / len(events)
        p95_latency = self._p95(durations)

        if error_rate >= self.error_rate_threshold:
            print(
                f"[ALERT] High error rate: {error_rate:.2%} "
                f"(threshold {self.error_rate_threshold:.2%})"
            )

        if p95_latency >= self.p95_latency_threshold_ms:
            print(
                f"[ALERT] High P95 latency: {p95_latency:.1f} ms "
                f"(threshold {self.p95_latency_threshold_ms:.1f} ms)"
            )

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        values_sorted = sorted(values)
        idx = int(0.95 * (len(values_sorted) - 1))
        return values_sorted[idx]

    def poll_once(self, count: int = 200, block_ms: int = 1000) -> None:
        """
        Polls Redis Streams once and prints alerts if thresholds are crossed.
        Can be called in a loop or a background task.
        """
        resp = self.redis.xreadgroup(
            groupname=self.group,
            consumername=self.consumer,
            streams={self.stream_key: ">"},
            count=count,
            block=block_ms,
        )

        if not resp:
            return

        # resp is a list of (stream, [ (id, fields), ... ])
        events = []
        ids_to_ack = []
        for stream_name, entries in resp:
            for entry_id, fields in entries:
                events.append({"id": entry_id, "fields": fields})
                ids_to_ack.append(entry_id)

        # Use only the latest window_size events for alert calculations
        events = events[-self.window_size :]
        self._calculate_alerts(events)

        if ids_to_ack:
            self.redis.xack(self.stream_key, self.group, *ids_to_ack)


# Wire up middleware
app.add_middleware(RequestMetricsMiddleware)


@app.get("/health")
async def health():
    return {"status": "ok"}