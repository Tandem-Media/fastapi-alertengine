# fastapi_alertengine/schemas.py
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RequestMetricEvent:
    """One recorded HTTP request."""
    path:        str
    method:      str
    status_code: int
    latency_ms:  float
    type:        str   # "webhook" | "api"


@dataclass
class LatencyBucket:
    """p95 summary for one traffic slice."""
    p95_ms: Optional[float]
    count:  int


@dataclass
class AlertMetrics:
    """Computed metrics returned by AlertEngine.evaluate()."""
    overall_p95_ms: float
    webhook_p95_ms: float
    api_p95_ms:     float
    error_rate:     float
    anomaly_score:  float
    sample_size:    int


@dataclass
class AlertThresholds:
    """Thresholds in effect during an evaluation (for audit trails)."""
    p95_warning_ms:      float
    p95_critical_ms:     float
    anomaly_warning:     float
    anomaly_critical:    float
    error_rate_warning:  float
    error_rate_critical: float


@dataclass
class AlertEvent:
    """Full result of one AlertEngine.evaluate() call."""
    status:     str            # "ok" | "warning" | "critical"
    metrics:    AlertMetrics
    thresholds: AlertThresholds
    timestamp:  int            # Unix seconds
    reason:     Optional[str] = None
