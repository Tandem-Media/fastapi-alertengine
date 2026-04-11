# fastapi_alertengine/schemas.py
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RequestMetricEvent:
    """One recorded HTTP request."""
    path:        str
    method:      str
    status_code: int
    latency_ms:  float
    type:        str    # "webhook" | "api"


@dataclass
class AlertItem:
    """
    A single fired alert.

    Matches the advertised JSON shape::

        {
          "type":     "latency_spike" | "error_anomaly",
          "message":  "P95 latency (1240ms) exceeds threshold (800ms)",
          "severity": "critical" | "warning"
        }
    """
    type:     str   # "latency_spike" | "error_anomaly"
    message:  str
    severity: str   # "warning" | "critical"


@dataclass
class AlertMetrics:
    """
    Computed metrics — field names match the advertised JSON output exactly.

        p95_latency_ms     – 95th-percentile wall-clock latency
        p50_latency_ms     – median (50th-percentile) latency
        error_rate_percent – error rate as a percentage (0–100)
        request_count_1m   – requests evaluated in the rolling window
    """
    p95_latency_ms:     float
    p50_latency_ms:     float
    error_rate_percent: float
    request_count_1m:   int


@dataclass
class AlertThresholds:
    """Thresholds in effect during an evaluation (for audit trails)."""
    p95_warning_ms:      float
    p95_critical_ms:     float
    error_rate_warning:  float   # percent
    error_rate_critical: float   # percent


@dataclass
class AlertEvent:
    """
    Full result of one AlertEngine.evaluate() call.

    Matches the advertised JSON output exactly::

        {
          "status":         "ok" | "warning" | "critical",
          "system_health":  82.4,
          "metrics": {
            "p95_latency_ms":     1240.5,
            "p50_latency_ms":     185.2,
            "error_rate_percent": 4.8,
            "request_count_1m":   840
          },
          "alerts": [
            {"type": "latency_spike",  "message": "...", "severity": "critical"},
            {"type": "error_anomaly",  "message": "...", "severity": "warning"}
          ],
          "timestamp":      "2026-04-10T14:38:21Z",
          "engine_version": "1.1.3"
        }
    """
    status:         str
    system_health:  float          # 0.0 – 100.0
    metrics:        AlertMetrics
    alerts:         List[AlertItem]
    timestamp:      str            # ISO-8601 UTC
    engine_version: str
    reason:         Optional[str] = None  # only present when no data

    def as_dict(self) -> dict:
        """Plain dict safe for JSON serialisation."""
        out = {
            "status":        self.status,
            "system_health": round(self.system_health, 1),
            "metrics": {
                "p95_latency_ms":     self.metrics.p95_latency_ms,
                "p50_latency_ms":     self.metrics.p50_latency_ms,
                "error_rate_percent": self.metrics.error_rate_percent,
                "request_count_1m":   self.metrics.request_count_1m,
            },
            "alerts": [
                {"type": a.type, "message": a.message, "severity": a.severity}
                for a in self.alerts
            ],
            "timestamp":      self.timestamp,
            "engine_version": self.engine_version,
        }
        if self.reason:
            out["reason"] = self.reason
        return out
