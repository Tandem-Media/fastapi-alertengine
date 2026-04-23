# fastapi_alertengine/schemas.py
"""
v1.5 — Adaptive Intelligence Layer schemas

New in v1.5:
- AdaptiveThresholds: derived from baseline learning
- HealthScore: composite 0-100 score with component breakdown
- EnrichedAlert: extended alert with reason, baseline_comparison, trend_direction
- RateOfChangeEvent: fired when delta spike detected regardless of absolute threshold
- BaselineSummary: statistical summary of collected snapshots for calibration

All v1.4 dataclasses preserved unchanged — fully backward compatible.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── v1.4 schemas — unchanged ──────────────────────────────────────────────────

@dataclass
class RequestMetricEvent:
    path:           str
    method:         str
    status_code:    int
    latency_ms:     float
    type:           str
    route_template: Optional[str]  = None
    trace_id:       Optional[str]  = None
    meta:           Dict[str, Any] = field(default_factory=dict)


@dataclass
class BaselineSnapshot:
    timestamp:     float
    service:       str
    instance_id:   str
    sample_size:   int
    p95_ms:        float
    p50_ms:        float
    mean_ms:       float
    error_rate:    float
    anomaly_score: float
    status:        str

    def as_dict(self) -> dict:
        return {
            "timestamp":     self.timestamp,
            "service":       self.service,
            "instance_id":   self.instance_id,
            "sample_size":   self.sample_size,
            "p95_ms":        round(self.p95_ms, 1),
            "p50_ms":        round(self.p50_ms, 1),
            "mean_ms":       round(self.mean_ms, 1),
            "error_rate":    round(self.error_rate, 4),
            "anomaly_score": round(self.anomaly_score, 3),
            "status":        self.status,
        }


@dataclass
class AlertItem:
    type:     str
    message:  str
    severity: str


@dataclass
class AlertMetrics:
    p95_latency_ms:     float
    p50_latency_ms:     float
    error_rate_percent: float
    request_count_1m:   int


@dataclass
class AlertThresholds:
    p95_warning_ms:      float
    p95_critical_ms:     float
    error_rate_warning:  float
    error_rate_critical: float


@dataclass
class AlertEvent:
    status:         str
    system_health:  float
    metrics:        AlertMetrics
    alerts:         List[AlertItem]
    timestamp:      str
    engine_version: str
    reason:         Optional[str] = None

    def as_dict(self) -> dict:
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


# ── v1.5 schemas ──────────────────────────────────────────────────────────────

@dataclass
class AdaptiveThresholds:
    """
    Thresholds derived from baseline learning.

    Computed from median P95 across collected BaselineSnapshots:
        warning_ms  = median_p95 * warning_multiplier  (default 1.5)
        critical_ms = median_p95 * critical_multiplier (default 2.0)

    confidence: "low" (<10 snapshots), "medium" (10-60), "high" (>60)
    active:     False until enough snapshots exist to trust the baseline
    """
    warning_ms:      float
    critical_ms:     float
    median_p95_ms:   float
    calibrated_from: int
    confidence:      str   # "low" | "medium" | "high"
    active:          bool
    computed_at:     float

    def as_dict(self) -> dict:
        return {
            "warning_ms":      round(self.warning_ms, 1),
            "critical_ms":     round(self.critical_ms, 1),
            "median_p95_ms":   round(self.median_p95_ms, 1),
            "calibrated_from": self.calibrated_from,
            "confidence":      self.confidence,
            "active":          self.active,
            "computed_at":     self.computed_at,
        }


@dataclass
class HealthScore:
    """
    Composite system health score (0-100) with component breakdown.

    score:  100 = perfect, 0 = completely degraded
    status: "healthy" | "degraded" | "critical"
    trend:  "stable" | "improving" | "degrading"
    """
    score:         float
    status:        str
    latency_score: float
    error_score:   float
    anomaly_score: float
    trend:         str

    def as_dict(self) -> dict:
        return {
            "score":  round(self.score, 1),
            "status": self.status,
            "components": {
                "latency":   round(self.latency_score, 1),
                "errors":    round(self.error_score, 1),
                "anomalies": round(self.anomaly_score, 1),
            },
            "trend": self.trend,
        }


@dataclass
class EnrichedAlert:
    """
    Extended alert payload — superset of AlertItem.

    triggered_by: "absolute_threshold" | "rate_of_change" | "adaptive_threshold"
    trend_direction: "increasing" | "decreasing" | "stable"
    baseline_comparison: {"baseline_value": float, "current_value": float, "deviation_pct": float}
    """
    type:                str
    message:             str
    severity:            str
    reason_for_trigger:  str
    trend_direction:     str
    triggered_by:        str
    baseline_comparison: Optional[Dict[str, Any]] = None

    def as_alert_item(self) -> AlertItem:
        """Downcast for backward-compatible consumers."""
        return AlertItem(type=self.type, message=self.message, severity=self.severity)

    def as_dict(self) -> dict:
        d = {
            "type":               self.type,
            "message":            self.message,
            "severity":           self.severity,
            "reason_for_trigger": self.reason_for_trigger,
            "trend_direction":    self.trend_direction,
            "triggered_by":       self.triggered_by,
        }
        if self.baseline_comparison:
            d["baseline_comparison"] = self.baseline_comparison
        return d


@dataclass
class RateOfChangeEvent:
    """
    Fired when a sudden spike is detected even if absolute thresholds
    have not been crossed.

    metric:  "p95_latency_ms" | "error_rate"
    """
    metric:         str
    previous_value: float
    current_value:  float
    delta_pct:      float
    window_s:       int
    timestamp:      float

    def as_dict(self) -> dict:
        return {
            "metric":         self.metric,
            "previous_value": round(self.previous_value, 2),
            "current_value":  round(self.current_value, 2),
            "delta_pct":      round(self.delta_pct, 1),
            "window_s":       self.window_s,
            "timestamp":      self.timestamp,
        }


@dataclass
class BaselineSummary:
    """Statistical summary used to derive AdaptiveThresholds."""
    service:           str
    snapshot_count:    int
    median_p95_ms:     float
    p95_of_p95_ms:     float
    mean_p95_ms:       float
    std_p95_ms:        float
    median_error_rate: float
    confidence:        str
    computed_at:       float

    def as_dict(self) -> dict:
        return {
            "service":           self.service,
            "snapshot_count":    self.snapshot_count,
            "median_p95_ms":     round(self.median_p95_ms, 1),
            "p95_of_p95_ms":     round(self.p95_of_p95_ms, 1),
            "mean_p95_ms":       round(self.mean_p95_ms, 1),
            "std_p95_ms":        round(self.std_p95_ms, 1),
            "median_error_rate": round(self.median_error_rate, 4),
            "confidence":        self.confidence,
            "computed_at":       self.computed_at,
        }
