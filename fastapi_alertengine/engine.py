# fastapi_alertengine/engine.py

import asyncio
import collections
import logging
import time
from typing import Any, Dict

from .config import AlertConfig
from .storage import write_metric

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 10_000


class AlertEngine:
    """
    Real-time SLO / latency alert engine.

    Uses a bounded in-memory queue (drained to Redis Streams) and rolling
    window analysis to produce ok / warning / critical signals.
    """

    def __init__(self, redis, config: AlertConfig) -> None:
        self.redis = redis
        self.config = config
        self._queue: collections.deque = collections.deque()

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def enqueue_metric(self, metric: dict) -> None:
        """
        Push one metric dict onto the in-memory queue.

        If the queue is full the oldest entry is dropped so memory stays
        bounded at MAX_QUEUE_SIZE items.

        Expected keys: path, method, status_code, latency_ms.
        """
        if len(self._queue) >= MAX_QUEUE_SIZE:
            self._queue.popleft()
        self._queue.append(metric)

    # ── Background drain ──────────────────────────────────────────────────────

    async def drain(self) -> None:
        """
        Continuously flush the in-memory queue to Redis Streams.

        Designed to be run as a long-lived background task via
        ``asyncio.create_task(engine.drain())``.  Shuts down cleanly on
        ``CancelledError`` and recovers from unexpected exceptions instead of
        dying permanently.
        """
        while True:
            try:
                while self._queue:
                    metric = self._queue.popleft()
                    try:
                        write_metric(self.redis, self.config, metric)
                    except Exception:
                        # Per-metric failure must not kill the loop.
                        pass
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break  # clean shutdown
            except Exception:
                logger.exception("drain() loop encountered an unexpected error; recovering")
                await asyncio.sleep(1.0)

    # ── Analysis ──────────────────────────────────────────────────────────────

    def _fetch_recent(self, last_n: int = 200):
        try:
            raw = self.redis.xrevrange(self.config.stream_key, count=last_n)
        except Exception:
            return []

        events = []
        for _, fields in raw:
            try:
                events.append({
                    "latency_ms":  float(fields.get("latency_ms", 0)),
                    "type":        fields.get("type", "api"),
                    # Written as "status" by storage.write_metric
                    "status_code": int(fields.get("status", 0)),
                })
            except Exception:
                continue

        return events

    def _p95(self, values):
        if not values:
            return 0.0
        values.sort()
        idx = int(len(values) * 0.95)
        return values[min(idx, len(values) - 1)]

    def _anomaly_score(self, current, baseline):
        if baseline == 0:
            return 0
        return abs(current - baseline) / baseline

    def evaluate(self, window_size: int = 200) -> Dict[str, Any]:
        events = self._fetch_recent(window_size)

        if not events:
            return {"status": "ok", "reason": "no_data"}

        all_lat     = [e["latency_ms"] for e in events]
        webhook_lat = [e["latency_ms"] for e in events if e["type"] == "webhook"]
        api_lat     = [e["latency_ms"] for e in events if e["type"] == "api"]

        def p95(values):
            if not values:
                return 0.0
            values.sort()
            idx = int(len(values) * 0.95)
            return values[min(idx, len(values) - 1)]

        overall_p95 = p95(all_lat)
        webhook_p95 = p95(webhook_lat)
        api_p95     = p95(api_lat)

        baseline = sum(all_lat) / len(all_lat)
        anomaly  = self._anomaly_score(overall_p95, baseline)

        status = "ok"
        if overall_p95 > 3000 or anomaly > 2.0:
            status = "critical"
        elif overall_p95 > 1000 or anomaly > 1.0:
            status = "warning"

        error_rate = sum(1 for e in events if e["status_code"] >= 500) / len(events)
        if error_rate > 0.2:
            status = "critical"
        elif error_rate > 0.1 and status != "critical":
            status = "warning"

        return {
            "status": status,
            "metrics": {
                "overall_p95_ms": overall_p95,
                "webhook_p95_ms": webhook_p95,
                "api_p95_ms":     api_p95,
                "error_rate":     error_rate,
                "anomaly_score":  anomaly,
                "sample_size":    len(events),
            },
            "thresholds": {
                "p95_warning_ms":      1000,
                "p95_critical_ms":     3000,
                "anomaly_warning":     1.0,
                "anomaly_critical":    2.0,
                "error_rate_warning":  0.1,
                "error_rate_critical": 0.2,
            },
            "timestamp": int(time.time()),
        }
