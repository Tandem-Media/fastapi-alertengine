# fastapi_alertengine/engine.py
import logging
import time
from typing import Any

from .config import AlertConfig
from .schemas import AlertEvent, AlertMetrics, AlertThresholds
from .storage import read_metrics

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = AlertConfig()


class AlertEngine:
    """
    Real-time SLO alert engine backed by a Redis Stream.

    Call :meth:`evaluate` from a background job or health endpoint to get
    the current alert status.  Pass the result to
    :class:`AlertDeduplicator` to suppress repeated notifications.

    Args:
        redis:  Synchronous ``redis.Redis`` client.
        config: :class:`~fastapi_alertengine.config.AlertConfig` instance.

    Usage::

        engine = AlertEngine(redis=rdb)
        event  = engine.evaluate()
        if event.status == "critical":
            ...
    """

    def __init__(self, redis: Any, config: AlertConfig = _DEFAULT_CONFIG) -> None:
        self._rdb    = redis
        self._config = config

    def evaluate(self) -> AlertEvent:
        """
        Read the most recent ``config.window_size`` events from the stream
        and return an :class:`~fastapi_alertengine.schemas.AlertEvent`.

        Never raises — returns ``status="ok"`` with ``reason="no_data"`` when
        the stream is empty or Redis is unavailable.
        """
        c      = self._config
        events = read_metrics(self._rdb, c, c.window_size)

        if not events:
            return AlertEvent(
                status     = "ok",
                metrics    = AlertMetrics(0, 0, 0, 0.0, 0.0, 0),
                thresholds = self._thresholds(),
                timestamp  = int(time.time()),
                reason     = "no_data",
            )

        all_lat     = [e.latency_ms for e in events]
        webhook_lat = [e.latency_ms for e in events if e.type == "webhook"]
        api_lat     = [e.latency_ms for e in events if e.type == "api"]

        overall_p95 = _p95(all_lat)
        webhook_p95 = _p95(webhook_lat)
        api_p95     = _p95(api_lat)

        baseline    = sum(all_lat) / len(all_lat)
        anomaly     = abs(overall_p95 - baseline) / baseline if baseline else 0.0
        error_rate  = sum(1 for e in events if e.status_code >= 500) / len(events)

        # ── Status resolution (critical wins over warning) ────────────────────
        status = "ok"
        reason = None

        if overall_p95 > c.p95_critical_ms or anomaly > c.anomaly_critical:
            status = "critical"
            reason = (
                f"p95={overall_p95:.0f}ms > {c.p95_critical_ms:.0f}ms"
                if overall_p95 > c.p95_critical_ms
                else f"anomaly_score={anomaly:.2f} > {c.anomaly_critical}"
            )
        elif overall_p95 > c.p95_warning_ms or anomaly > c.anomaly_warning:
            status = "warning"
            reason = (
                f"p95={overall_p95:.0f}ms > {c.p95_warning_ms:.0f}ms"
                if overall_p95 > c.p95_warning_ms
                else f"anomaly_score={anomaly:.2f} > {c.anomaly_warning}"
            )

        if error_rate > c.error_rate_critical:
            status = "critical"
            reason = f"error_rate={error_rate:.1%} > {c.error_rate_critical:.0%}"
        elif error_rate > c.error_rate_warning and status != "critical":
            status = "warning"
            reason = reason or f"error_rate={error_rate:.1%} > {c.error_rate_warning:.0%}"

        return AlertEvent(
            status = status,
            reason = reason,
            metrics = AlertMetrics(
                overall_p95_ms = round(overall_p95, 3),
                webhook_p95_ms = round(webhook_p95, 3),
                api_p95_ms     = round(api_p95, 3),
                error_rate     = round(error_rate, 4),
                anomaly_score  = round(anomaly, 4),
                sample_size    = len(events),
            ),
            thresholds = self._thresholds(),
            timestamp  = int(time.time()),
        )

    def _thresholds(self) -> AlertThresholds:
        c = self._config
        return AlertThresholds(
            p95_warning_ms      = c.p95_warning_ms,
            p95_critical_ms     = c.p95_critical_ms,
            anomaly_warning     = c.anomaly_warning,
            anomaly_critical    = c.anomaly_critical,
            error_rate_warning  = c.error_rate_warning,
            error_rate_critical = c.error_rate_critical,
        )


class AlertDeduplicator:
    """
    Redis TTL-based deduplication — prevents the same alert type from
    firing more than once per ``cooldown_seconds``.

    Fails open: if Redis is unavailable, alerts are allowed through.

    Args:
        redis:  Synchronous ``redis.Redis`` client.
        config: :class:`~fastapi_alertengine.config.AlertConfig` instance.

    Usage::

        dedup = AlertDeduplicator(redis=rdb)
        event = engine.evaluate()
        if event.status != "ok" and dedup.should_fire(event.status):
            send_notification(event)
    """

    def __init__(self, redis: Any, config: AlertConfig = _DEFAULT_CONFIG) -> None:
        self._rdb      = redis
        self._cooldown = config.cooldown_seconds

    def should_fire(self, alert_type: str, severity: str = "warning") -> bool:
        """Return True if the alert should be emitted (not a duplicate)."""
        key = f"alert:dedup:{alert_type}"
        try:
            result = self._rdb.set(
                key,
                f"{severity}:{int(time.time())}",
                nx=True,
                ex=self._cooldown,
            )
            return bool(result)
        except Exception as exc:
            logger.warning("AlertDeduplicator: Redis error — failing open: %s", exc)
            return True

    def reset(self, alert_type: str) -> None:
        """Clear the dedup key so the alert can fire immediately."""
        try:
            self._rdb.delete(f"alert:dedup:{alert_type}")
        except Exception as exc:
            logger.warning("AlertDeduplicator.reset failed: %s", exc)


# ── Internal ──────────────────────────────────────────────────────────────────

def _p95(values: list) -> float:
    if not values:
        return 0.0
    s   = sorted(values)
    idx = int(len(s) * 0.95)
    return s[min(idx, len(s) - 1)]
