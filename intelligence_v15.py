# fastapi_alertengine/intelligence.py
"""
v1.5 — Adaptive Intelligence Layer
Three independent, composable subsystems — all pure functions, no I/O.
"""
from __future__ import annotations
import math
import statistics
import time
from typing import List, Optional
from .config import AlertConfig
from .schemas import (
    AdaptiveThresholds, BaselineSummary, EnrichedAlert,
    HealthScore, RateOfChangeEvent,
)


def _confidence_level(n: int) -> str:
    if n >= 60: return "high"
    if n >= 10: return "medium"
    return "low"


def compute_baseline_summary(snapshots: List[dict], service: str) -> Optional[BaselineSummary]:
    if not snapshots: return None
    p95_values   = [s["p95_ms"]     for s in snapshots if s.get("p95_ms", 0) > 0]
    error_values = [s["error_rate"] for s in snapshots if "error_rate" in s]
    if not p95_values: return None
    sorted_p95 = sorted(p95_values)
    n = len(sorted_p95)
    return BaselineSummary(
        service           = service,
        snapshot_count    = n,
        median_p95_ms     = statistics.median(sorted_p95),
        p95_of_p95_ms     = sorted_p95[min(int(math.ceil(n * 0.95)) - 1, n - 1)],
        mean_p95_ms       = statistics.mean(sorted_p95),
        std_p95_ms        = statistics.stdev(sorted_p95) if n > 1 else 0.0,
        median_error_rate = statistics.median(error_values) if error_values else 0.0,
        confidence        = _confidence_level(n),
        computed_at       = time.time(),
    )


def calibrate_thresholds(summary: BaselineSummary, config: AlertConfig) -> AdaptiveThresholds:
    return AdaptiveThresholds(
        warning_ms      = summary.median_p95_ms * config.baseline_warning_multiplier,
        critical_ms     = summary.median_p95_ms * config.baseline_critical_multiplier,
        median_p95_ms   = summary.median_p95_ms,
        calibrated_from = summary.snapshot_count,
        confidence      = summary.confidence,
        active          = (summary.snapshot_count >= config.baseline_min_snapshots
                          and summary.confidence in ("medium", "high")),
        computed_at     = time.time(),
    )


def _score_latency(p95_ms: float, warning_ms: float, critical_ms: float) -> float:
    if p95_ms <= warning_ms: return 100.0
    if p95_ms >= critical_ms * 3: return 0.0
    if p95_ms <= critical_ms:
        return 100.0 - ((p95_ms - warning_ms) / (critical_ms - warning_ms)) * 50.0
    return 50.0 - (min((p95_ms - critical_ms) / (critical_ms * 2), 1.0)) * 50.0


def _score_errors(error_rate: float, warning_pct: float, critical_pct: float) -> float:
    err_pct = error_rate * 100
    if err_pct <= warning_pct: return 100.0
    if err_pct >= critical_pct * 5: return 0.0
    if err_pct <= critical_pct:
        return 100.0 - ((err_pct - warning_pct) / (critical_pct - warning_pct)) * 50.0
    return 50.0 - (min((err_pct - critical_pct) / (critical_pct * 4), 1.0)) * 50.0


def _score_anomaly(anomaly_score: float) -> float:
    if anomaly_score < 0.5: return 100.0
    if anomaly_score >= 4.0: return 0.0
    if anomaly_score < 2.0:
        return 100.0 - ((anomaly_score - 0.5) / 1.5) * 50.0
    return 50.0 - (min((anomaly_score - 2.0) / 2.0, 1.0)) * 50.0


def _determine_trend(score_history: List[float]) -> str:
    n = len(score_history)
    if n < 3: return "stable"
    xs  = list(range(n))
    x_m = sum(xs) / n
    y_m = sum(score_history) / n
    num = sum((x - x_m) * (y - y_m) for x, y in zip(xs, score_history))
    den = sum((x - x_m) ** 2 for x in xs)
    if den == 0: return "stable"
    slope = num / den
    if slope > 2.0:  return "improving"
    if slope < -2.0: return "degrading"
    return "stable"


def compute_health_score(
    p95_ms: float, error_rate: float, anomaly_score: float,
    config: AlertConfig, score_history: List[float],
    adaptive: Optional[AdaptiveThresholds] = None,
) -> HealthScore:
    if adaptive and adaptive.active:
        warn_ms, crit_ms = adaptive.warning_ms, adaptive.critical_ms
    else:
        warn_ms, crit_ms = config.p95_warning_ms, config.p95_critical_ms
    lat_s = _score_latency(p95_ms, warn_ms, crit_ms)
    err_s = _score_errors(error_rate, config.error_rate_warning_pct, config.error_rate_critical_pct)
    ano_s = _score_anomaly(anomaly_score)
    composite = max(0.0, min(100.0,
        lat_s * config.health_weight_latency +
        err_s * config.health_weight_errors  +
        ano_s * config.health_weight_anomaly
    ))
    if composite >= config.health_degraded_threshold: status = "healthy"
    elif composite >= config.health_critical_threshold: status = "degraded"
    else: status = "critical"
    return HealthScore(
        score=composite, status=status,
        latency_score=lat_s, error_score=err_s, anomaly_score=ano_s,
        trend=_determine_trend(score_history),
    )


def detect_rate_of_change(
    current_p95_ms: float, previous_p95_ms: float,
    current_error_rate: float, previous_error_rate: float,
    config: AlertConfig, window_s: int = 60,
) -> List[RateOfChangeEvent]:
    now, events = time.time(), []
    if (config.roc_latency_spike_pct > 0
            and previous_p95_ms >= config.roc_min_prior_latency_ms
            and previous_p95_ms > 0):
        delta = ((current_p95_ms - previous_p95_ms) / previous_p95_ms) * 100
        if delta >= config.roc_latency_spike_pct:
            events.append(RateOfChangeEvent(
                metric="p95_latency_ms", previous_value=previous_p95_ms,
                current_value=current_p95_ms, delta_pct=delta,
                window_s=window_s, timestamp=now,
            ))
    if (config.roc_error_rate_spike_pct > 0
            and previous_error_rate >= config.roc_min_prior_error_rate
            and previous_error_rate > 0):
        delta = ((current_error_rate - previous_error_rate) / previous_error_rate) * 100
        if delta >= config.roc_error_rate_spike_pct:
            events.append(RateOfChangeEvent(
                metric="error_rate", previous_value=previous_error_rate,
                current_value=current_error_rate, delta_pct=delta,
                window_s=window_s, timestamp=now,
            ))
    return events


def enrich_alert(
    alert_type: str, severity: str, message: str,
    current_value: float, threshold: float,
    baseline_value: Optional[float], trend: str, triggered_by: str,
) -> EnrichedAlert:
    if triggered_by == "rate_of_change":
        delta = (((current_value - baseline_value) / baseline_value * 100)
                 if baseline_value and baseline_value > 0 else 0.0)
        reason = f"Sudden {delta:.0f}% spike detected relative to the prior evaluation window."
    elif triggered_by == "adaptive_threshold":
        dev = (((current_value - threshold) / threshold * 100) if threshold > 0 else 0.0)
        reason = (f"Value exceeds adaptive threshold ({threshold:.0f}) derived from "
                  f"baseline learning — {dev:.0f}% above learned normal.")
    else:
        dev = (((current_value - threshold) / threshold * 100) if threshold > 0 else 0.0)
        reason = f"Value ({current_value:.1f}) exceeds static threshold ({threshold:.0f}) by {dev:.0f}%."
    comparison = None
    if baseline_value is not None and baseline_value > 0:
        comparison = {
            "baseline_value": round(baseline_value, 2),
            "current_value":  round(current_value, 2),
            "deviation_pct":  round(((current_value - baseline_value) / baseline_value) * 100, 1),
        }
    return EnrichedAlert(
        type=alert_type, message=message, severity=severity,
        reason_for_trigger=reason, trend_direction=trend,
        triggered_by=triggered_by, baseline_comparison=comparison,
    )
