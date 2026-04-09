# fastapi_alertengine/engine.py

import time
from typing import Dict, Any


STREAM_KEY = "anchorflow:request_metrics"


class AlertEngine:
    """
    Real-time SLO / latency alert engine.

    Uses rolling window analysis over Redis Stream data.
    """

    def __init__(self, redis):
        self.redis = redis

    def _fetch_recent(self, last_n: int = 200):
        raw = self.redis.xrevrange(STREAM_KEY, count=last_n)

        events = []
        for _, fields in raw:
            try:
                events.append({
                    "latency_ms": float(fields.get("latency_ms", 0)),
                    "type": fields.get("type", "api"),
                    "status_code": int(fields.get("status_code", 0)),
                    "timestamp": int(fields.get("timestamp", 0)),
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
            return {
                "status": "ok",
                "reason": "no_data"
            }

        all_lat = [e["latency_ms"] for e in events]
        webhook_lat = [e["latency_ms"] for e in events if e["type"] == "webhook"]
        api_lat = [e["latency_ms"] for e in events if e["type"] == "api"]

        def p95(values):
            if not values:
                return 0.0
            values.sort()
            idx = int(len(values) * 0.95)
            return values[min(idx, len(values) - 1)]

        overall_p95 = p95(all_lat)
        webhook_p95 = p95(webhook_lat)
        api_p95 = p95(api_lat)

        # Baseline (simple rolling heuristic)
        baseline = sum(all_lat) / len(all_lat)

        anomaly = self._anomaly_score(overall_p95, baseline)

        # Alert thresholds
        status = "ok"

        if overall_p95 > 3000 or anomaly > 2.0:
            status = "critical"
        elif overall_p95 > 1000 or anomaly > 1.0:
            status = "warning"

        # Failure pressure signal
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
                "api_p95_ms": api_p95,
                "error_rate": error_rate,
                "anomaly_score": anomaly,
                "sample_size": len(events),
            },
            "thresholds": {
                "p95_warning": 1000,
                "p95_critical": 3000,
                "anomaly_warning": 1.0,
                "anomaly_critical": 2.0,
                "error_rate_critical": 0.2,
            },
            "timestamp": int(time.time()),
        }