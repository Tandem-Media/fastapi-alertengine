# fastapi_alertengine/engine.py
import asyncio
import math
import time
from collections import deque
from datetime import datetime, timezone
from typing import List

from .config import AlertConfig
from .storage import read_metrics, write_metric

__version__ = "1.1.4"


class AlertEngine:
    """
    Real-time SLO / latency alert engine with non-blocking metric ingestion.

    Metrics are enqueued in-memory by RequestMetricsMiddleware and drained
    to Redis by a background asyncio task started at app startup.

    evaluate() returns a plain dict FastAPI can serialise natively.
    """

    def __init__(self, config: AlertConfig, redis) -> None:
        self.config = config
        self.redis  = redis
        self._queue: deque = deque()

    def enqueue_metric(self, metric: dict) -> None:
        """Enqueue a metric for async Redis write. Never raises."""
        self._queue.append(metric)

    async def drain(self) -> None:
        """
        Background coroutine that drains the in-memory queue to Redis.

        Wire at startup::

            @app.on_event("startup")
            async def start_drain():
                asyncio.create_task(engine.drain())
        """
        while True:
            while self._queue:
                metric = self._queue.popleft()
                try:
                    write_metric(self.redis, self.config, metric)
                except Exception:
                    pass
            await asyncio.sleep(0.05)

    def evaluate(self, window_size: int = 200) -> dict:
        """Read the last *window_size* events and return a health dict."""
        events = read_metrics(self.redis, self.config, last_n=window_size)
        ts     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not events:
            return {
                "status": "ok",
                "system_health": 100.0,
                "metrics": {
                    "p95_latency_ms": 0.0,
                    "p50_latency_ms": 0.0,
                    "error_rate_percent": 0.0,
                    "request_count_1m": 0,
                },
                "alerts": [],
                "timestamp": ts,
                "engine_version": __version__,
                "reason": "no_data",
            }

        all_lat = [e.latency_ms for e in events]
        p95     = self._percentile(all_lat, 95)
        p50     = self._percentile(all_lat, 50)

        error_count    = sum(1 for e in events if e.status_code >= 500)
        error_rate_pct = round(error_count / len(events) * 100, 2)

        cfg    = self.config
        alerts = []
        status = "ok"

        if p95 > cfg.p95_critical_ms:
            alerts.append({"type": "latency_spike", "message": f"P95 latency ({p95:.0f}ms) exceeds threshold ({cfg.p95_critical_ms:.0f}ms)", "severity": "critical"})
            status = "critical"
        elif p95 > cfg.p95_warning_ms:
            alerts.append({"type": "latency_spike", "message": f"P95 latency ({p95:.0f}ms) exceeds threshold ({cfg.p95_warning_ms:.0f}ms)", "severity": "warning"})
            status = "warning"

        if error_rate_pct > cfg.error_rate_critical_pct:
            alerts.append({"type": "error_anomaly", "message": f"Error rate elevated: {error_rate_pct}% (Baseline: {cfg.error_rate_baseline_pct}%)", "severity": "critical"})
            status = "critical"
        elif error_rate_pct > cfg.error_rate_warning_pct:
            alerts.append({"type": "error_anomaly", "message": f"Error rate elevated: {error_rate_pct}% (Baseline: {cfg.error_rate_baseline_pct}%)", "severity": "warning"})
            if status != "critical": status = "warning"

        system_health = self._health_score(p95, error_rate_pct, cfg)

        return {
            "status": status, "system_health": system_health,
            "metrics": {"p95_latency_ms": round(p95, 1), "p50_latency_ms": round(p50, 1), "error_rate_percent": error_rate_pct, "request_count_1m": len(events)},
            "alerts": alerts, "timestamp": ts, "engine_version": __version__,
        }

    @staticmethod
    def _percentile(values: List[float], pct: int) -> float:
        if not values: return 0.0
        s = sorted(values)
        idx = min(int(math.ceil(len(s) * pct / 100)) - 1, len(s) - 1)
        return s[max(idx, 0)]

    @staticmethod
    def _health_score(p95_ms: float, error_rate_pct: float, cfg: AlertConfig) -> float:
        lat_health = 100.0 if p95_ms <= cfg.p95_warning_ms else max(0.0, 100.0 * (1 - (p95_ms - cfg.p95_warning_ms) / (cfg.p95_critical_ms * 2 - cfg.p95_warning_ms)))
        err_health = 100.0 if error_rate_pct <= cfg.error_rate_warning_pct else max(0.0, 100.0 * (1 - (error_rate_pct - cfg.error_rate_warning_pct) / (cfg.error_rate_critical_pct * 2 - cfg.error_rate_warning_pct)))
        return round((lat_health + err_health) / 2, 1)
