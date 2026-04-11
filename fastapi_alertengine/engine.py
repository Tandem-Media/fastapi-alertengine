# fastapi_alertengine/engine.py
"""
AlertEngine: rolling-window SLO evaluation over Redis Stream data.

Usage::

    from fastapi_alertengine import get_alert_engine

    engine = get_alert_engine()               # zero-config, reads env vars
    result = engine.evaluate(window_size=200) # returns AlertEvent
    print(result.status)                      # "ok" | "warning" | "critical"
    print(result.as_dict())                   # matches advertised JSON schema
"""

import math
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .config import AlertConfig
from .schemas import AlertEvent, AlertItem, AlertMetrics, AlertThresholds, RequestMetricEvent
from .storage import read_metrics

__version__ = "1.1.3"


class AlertEngine:
    """
    Real-time SLO / latency alert engine.

    evaluate() returns an AlertEvent whose as_dict() matches the advertised
    JSON output exactly:

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

    def __init__(self, config: AlertConfig, redis) -> None:
        self.config = config
        self.redis  = redis

    # ── Public API ────────────────────────────────────────────────────────

    def evaluate(self, window_size: int = 200) -> AlertEvent:
        """
        Evaluate the last *window_size* requests and return a typed AlertEvent.

        Call .as_dict() on the result for a JSON-serialisable plain dict
        that matches the advertised /health/alerts schema exactly.
        """
        events = read_metrics(self.redis, self.config, last_n=window_size)
        ts     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not events:
            return AlertEvent(
                status         = "ok",
                system_health  = 100.0,
                metrics        = AlertMetrics(0.0, 0.0, 0.0, 0),
                alerts         = [],
                timestamp      = ts,
                engine_version = __version__,
                reason         = "no_data",
            )

        # ── Latency percentiles ───────────────────────────────────────────
        all_lat = [e.latency_ms for e in events]
        p95     = self._percentile(all_lat, 95)
        p50     = self._percentile(all_lat, 50)

        # ── Error rate (5xx only) as a percentage ─────────────────────────
        error_count       = sum(1 for e in events if e.status_code >= 500)
        error_rate_frac   = error_count / len(events)
        error_rate_pct    = round(error_rate_frac * 100, 2)

        # ── Build alerts list ─────────────────────────────────────────────
        cfg    = self.config
        alerts: List[AlertItem] = []
        status = "ok"

        # Latency alert
        if p95 > cfg.p95_critical_ms:
            alerts.append(AlertItem(
                type     = "latency_spike",
                message  = (
                    f"P95 latency ({p95:.0f}ms) exceeds threshold "
                    f"({cfg.p95_critical_ms:.0f}ms)"
                ),
                severity = "critical",
            ))
            status = "critical"
        elif p95 > cfg.p95_warning_ms:
            alerts.append(AlertItem(
                type     = "latency_spike",
                message  = (
                    f"P95 latency ({p95:.0f}ms) exceeds threshold "
                    f"({cfg.p95_warning_ms:.0f}ms)"
                ),
                severity = "warning",
            ))
            if status != "critical":
                status = "warning"

        # Error-rate alert — includes baseline context in the message
        baseline_pct = cfg.error_rate_baseline_pct
        if error_rate_pct > cfg.error_rate_critical_pct:
            alerts.append(AlertItem(
                type     = "error_anomaly",
                message  = (
                    f"Error rate elevated: {error_rate_pct}% "
                    f"(Baseline: {baseline_pct}%)"
                ),
                severity = "critical",
            ))
            status = "critical"
        elif error_rate_pct > cfg.error_rate_warning_pct:
            alerts.append(AlertItem(
                type     = "error_anomaly",
                message  = (
                    f"Error rate elevated: {error_rate_pct}% "
                    f"(Baseline: {baseline_pct}%)"
                ),
                severity = "warning",
            ))
            if status != "critical":
                status = "warning"

        # ── system_health score (0–100) ───────────────────────────────────
        system_health = self._health_score(p95, error_rate_pct, cfg)

        return AlertEvent(
            status         = status,
            system_health  = system_health,
            metrics        = AlertMetrics(
                p95_latency_ms     = round(p95, 1),
                p50_latency_ms     = round(p50, 1),
                error_rate_percent = error_rate_pct,
                request_count_1m   = len(events),
            ),
            alerts         = alerts,
            timestamp      = ts,
            engine_version = __version__,
        )

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _percentile(values: List[float], pct: int) -> float:
        """Return the given percentile from a list of floats. Returns 0.0 if empty."""
        if not values:
            return 0.0
        s   = sorted(values)
        idx = min(int(math.ceil(len(s) * pct / 100)) - 1, len(s) - 1)
        return s[max(idx, 0)]

    @staticmethod
    def _health_score(p95_ms: float, error_rate_pct: float,
                      cfg: AlertConfig) -> float:
        """
        Composite health score from 0.0 (worst) to 100.0 (perfect).

        Degrades linearly:
        - Latency component: starts degrading at p95_warning_ms,
          reaches 0 at 2× p95_critical_ms.
        - Error component:   starts degrading at error_rate_warning_pct,
          reaches 0 at 2× error_rate_critical_pct.
        Both components are averaged (50/50 weight).
        """
        # Latency health (0–100)
        if p95_ms <= cfg.p95_warning_ms:
            lat_health = 100.0
        else:
            worst_lat  = cfg.p95_critical_ms * 2
            lat_health = max(
                0.0,
                100.0 * (1 - (p95_ms - cfg.p95_warning_ms)
                         / (worst_lat - cfg.p95_warning_ms))
            )

        # Error-rate health (0–100)
        if error_rate_pct <= cfg.error_rate_warning_pct:
            err_health = 100.0
        else:
            worst_err  = cfg.error_rate_critical_pct * 2
            err_health = max(
                0.0,
                100.0 * (1 - (error_rate_pct - cfg.error_rate_warning_pct)
                         / (worst_err - cfg.error_rate_warning_pct))
            )

        return round((lat_health + err_health) / 2, 1)
