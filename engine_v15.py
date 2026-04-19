# fastapi_alertengine/engine.py
"""
v1.5 — Adaptive Intelligence Layer

Changes from v1.4:
1. Baseline Learning (opt-in via config.baseline_learning_mode)
   - Calibrates AdaptiveThresholds from collected BaselineSnapshots
   - Recalibrates every config.baseline_recalibrate_interval_s seconds
   - Only activates when snapshot count >= config.baseline_min_snapshots
   - Falls back to static thresholds when inactive — safe by default

2. Health Score Engine
   - Composite 0-100 score from latency, error rate, anomaly components
   - Weighted by config.health_weight_* fields
   - Trend derived from rolling evaluation history
   - Exposed in evaluate() output as "health_score" (with full breakdown)

3. Rate-of-Change Detection
   - Detects sudden spikes between consecutive evaluation windows
   - Fires even when absolute thresholds are not crossed
   - Controlled by config.roc_latency_spike_pct / roc_error_rate_spike_pct
   - Suppressed when prior values are too low (prevents false positives)

4. Enhanced Alert Payloads
   - evaluate() now returns enriched alerts with:
     reason_for_trigger, baseline_comparison, trend_direction, triggered_by
   - Backward compatible: "type", "message", "severity" keys preserved

evaluate() output is a superset of v1.4 — no keys removed.
"""
import asyncio
import collections
import logging
import math
import os
import random
import time
from typing import Any, Dict, List, Optional

import httpx

from .config import AlertConfig
from .schemas import (
    AdaptiveThresholds,
    BaselineSnapshot,
    HealthScore,
    RateOfChangeEvent,
)
from .intelligence import (
    calibrate_thresholds,
    compute_baseline_summary,
    compute_health_score,
    detect_rate_of_change,
    enrich_alert,
)
from .storage import (
    flush_aggregates,
    read_aggregates,
    read_metrics,
    write_batch,
    write_baseline_snapshot,
    write_incident_event,
    read_incident_events,
)

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE    = 10_000
_DRAIN_BATCH_SIZE = 100
_DRAIN_SLEEP_S    = 0.05
MAX_AGG_KEYS      = 50_000
_DEMO_DELAY_S: int = 12


# ── Null objects ───────────────────────────────────────────────────────────────

class _NullPipeline:
    def xadd(self, *a, **kw): return self
    def hset(self, *a, **kw): return self
    def expire(self, *a, **kw): return self
    def zadd(self, *a, **kw): return self
    def hgetall(self, *a, **kw): return self
    def execute(self, *a, **kw): return []


class _NullRedis:
    def ping(self): raise ConnectionError("_NullRedis: no Redis configured")
    def xadd(self, *a, **kw): pass
    def xrevrange(self, *a, **kw): return []
    def zrevrange(self, *a, **kw): return []
    def zrangebyscore(self, *a, **kw): return []
    def hgetall(self, *a, **kw): return {}
    def expire(self, *a, **kw): pass
    def zadd(self, *a, **kw): pass
    def zremrangebyrank(self, *a, **kw): pass
    def pipeline(self, *a, **kw): return _NullPipeline()


# ── Circuit breaker states ─────────────────────────────────────────────────────

class _CircuitState:
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class AlertEngine:
    """
    Real-time SLO / latency alert engine.

    v1.5: Adaptive thresholds + health scoring + rate-of-change detection.
    """

    def __init__(self, redis=None, config=None):
        if isinstance(redis, AlertConfig) and config is None:
            config, redis = redis, None
        if config is None:
            config = AlertConfig()
        if redis is None:
            redis = _NullRedis()

        self.redis        = redis
        self.config       = config
        self._memory_mode = isinstance(redis, _NullRedis)

        self._recent = collections.deque(maxlen=200)
        self._queue  = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._agg    = {}
        self._last_agg_flush_ts = 0.0
        self._dropped_agg_keys  = 0

        self._stats = {"enqueued": 0, "dropped": 0, "last_drain_at": None}

        self._alert_queue    = asyncio.Queue(maxsize=1_000)
        self._dropped_alerts = 0
        self._last_slack_ts  = 0.0

        # Onboarding
        self._first_request_at: Optional[float] = None
        self._demo_mode_active: bool = False
        self._demo_alert_shown: bool = False

        # ── v1.4: Circuit breaker ─────────────────────────────────────────────
        self._cb_state          = _CircuitState.CLOSED
        self._cb_failure_count  = 0
        self._cb_opened_at: Optional[float] = None
        self._cb_dropped_events = 0
        self._cb_buffer: collections.deque = collections.deque(
            maxlen=config.memory_buffer_maxlen
        )

        # ── v1.4: Baseline preparation ────────────────────────────────────────
        self._baseline_snapshots: collections.deque = collections.deque(
            maxlen=config.baseline_max_snapshots
        )
        self._last_baseline_ts    = 0.0

        # ── v1.5: Adaptive thresholds ─────────────────────────────────────────
        # None until first calibration completes
        self._adaptive_thresholds: Optional[AdaptiveThresholds] = None
        self._last_calibration_ts = 0.0

        # ── v1.5: Health score history ────────────────────────────────────────
        # Rolling deque of recent composite scores for trend analysis
        self._score_history: collections.deque = collections.deque(
            maxlen=config.evaluation_history_size
        )

        # ── v1.5: Rate-of-change state ────────────────────────────────────────
        # Previous evaluation's metrics — compared against current window
        self._prev_p95_ms:     Optional[float] = None
        self._prev_error_rate: Optional[float] = None

        # ── v1.5: Recent rate-of-change events ───────────────────────────────
        self._roc_events: collections.deque = collections.deque(maxlen=50)

    # ── Circuit breaker ────────────────────────────────────────────────────────

    def _cb_record_success(self) -> None:
        was_open = self._cb_state != _CircuitState.CLOSED
        self._cb_state         = _CircuitState.CLOSED
        self._cb_failure_count = 0
        self._cb_opened_at     = None
        if was_open:
            logger.info("AlertEngine circuit breaker CLOSED (buffered: %d)",
                        len(self._cb_buffer))
            self._cb_drain_buffer()

    def _cb_record_failure(self) -> None:
        self._cb_failure_count += 1
        if (self._cb_state == _CircuitState.CLOSED
                and self._cb_failure_count >= self.config.circuit_breaker_threshold):
            self._cb_state     = _CircuitState.OPEN
            self._cb_opened_at = time.monotonic()
            logger.warning("AlertEngine circuit breaker OPEN after %d failures.",
                           self._cb_failure_count)
        elif self._cb_state == _CircuitState.HALF_OPEN:
            self._cb_state     = _CircuitState.OPEN
            self._cb_opened_at = time.monotonic()
            logger.warning("AlertEngine circuit breaker re-OPEN after failed probe.")

    def _cb_should_attempt_write(self) -> bool:
        if self._cb_state == _CircuitState.CLOSED:
            return True
        if self._cb_state == _CircuitState.OPEN:
            elapsed = time.monotonic() - (self._cb_opened_at or 0)
            if elapsed >= self.config.circuit_breaker_cooldown_s:
                self._cb_state = _CircuitState.HALF_OPEN
                logger.info("AlertEngine circuit breaker HALF-OPEN — probing.")
                return True
            return False
        return True  # HALF_OPEN

    def _cb_write_batch_safe(self, batch: list) -> None:
        if not self._cb_should_attempt_write():
            for metric in batch:
                if len(self._cb_buffer) < self.config.memory_buffer_maxlen:
                    self._cb_buffer.append(metric)
                else:
                    self._cb_dropped_events += 1
            return
        try:
            write_batch(self.redis, self.config, batch)
            self._cb_record_success()
        except Exception as exc:
            logger.warning("AlertEngine Redis write failed: %s", exc)
            self._cb_record_failure()
            for metric in batch:
                if len(self._cb_buffer) < self.config.memory_buffer_maxlen:
                    self._cb_buffer.append(metric)
                else:
                    self._cb_dropped_events += 1

    def _cb_drain_buffer(self) -> None:
        if not self._cb_buffer:
            return
        buffered = list(self._cb_buffer)
        self._cb_buffer.clear()
        try:
            write_batch(self.redis, self.config, buffered)
            logger.info("AlertEngine: drained %d buffered events.", len(buffered))
        except Exception as exc:
            logger.warning("AlertEngine buffer drain failed: %s", exc)
            for m in buffered:
                if len(self._cb_buffer) < self.config.memory_buffer_maxlen:
                    self._cb_buffer.append(m)
                else:
                    self._cb_dropped_events += 1

    def get_circuit_breaker_status(self) -> dict:
        return {
            "state":          self._cb_state,
            "failure_count":  self._cb_failure_count,
            "buffer_size":    len(self._cb_buffer),
            "dropped_events": self._cb_dropped_events,
            "opened_at":      self._cb_opened_at,
        }

    # ── v1.5: Adaptive threshold calibration ──────────────────────────────────

    def _maybe_recalibrate(self) -> None:
        """
        Recalibrate adaptive thresholds from collected snapshots.

        Only runs when:
        - baseline_learning_mode is True
        - Enough time has elapsed since last calibration
        - There are snapshots to calibrate from

        Safe to call frequently — guarded by timestamp check.
        """
        if not self.config.baseline_learning_mode:
            return
        now = time.time()
        if now - self._last_calibration_ts < self.config.baseline_recalibrate_interval_s:
            return
        if not self._baseline_snapshots:
            return

        snapshots = [s.as_dict() for s in self._baseline_snapshots]
        summary   = compute_baseline_summary(snapshots, self.config.service_name)
        if summary is None:
            return

        self._adaptive_thresholds = calibrate_thresholds(summary, self.config)
        self._last_calibration_ts = now

        if self._adaptive_thresholds.active:
            logger.info(
                "AlertEngine adaptive thresholds calibrated "
                "(n=%d, confidence=%s): warning=%.0fms critical=%.0fms",
                self._adaptive_thresholds.calibrated_from,
                self._adaptive_thresholds.confidence,
                self._adaptive_thresholds.warning_ms,
                self._adaptive_thresholds.critical_ms,
            )

    def get_adaptive_thresholds(self) -> Optional[dict]:
        """Return current adaptive thresholds as a dict, or None."""
        if self._adaptive_thresholds is None:
            return None
        return self._adaptive_thresholds.as_dict()

    # ── Demo / onboarding ─────────────────────────────────────────────────────

    def _demo_allowed(self) -> bool:
        if not self._memory_mode:
            return False
        env = os.getenv("ENV", os.getenv("ENVIRONMENT", "")).lower()
        if env in ("production", "prod"):
            return False
        if os.getenv("ALERTENGINE_DISABLE_DEMO", "").lower() in ("1", "true", "yes"):
            return False
        return True

    async def _run_demo_spike(self) -> None:
        try:
            delay = int(os.getenv("ALERTENGINE_DEMO_DELAY", str(_DEMO_DELAY_S)))
            await asyncio.sleep(delay)
            if self._first_request_at is not None:
                return
            if not self._demo_allowed():
                return
            self._demo_mode_active = True
            print("\n🚀 Demo Mode: synthetic metrics generated for preview")
            for _ in range(45):
                self._recent.append({"latency_ms": random.uniform(900, 2_500),
                                     "type": "api", "status_code": 200})
            for _ in range(5):
                self._recent.append({"latency_ms": random.uniform(100, 300),
                                     "type": "api", "status_code": 500})
            ev = self.evaluate()
            if ev.get("alerts") and not self._demo_alert_shown:
                self._demo_alert_shown = True
                a   = ev["alerts"][0]
                val = ev["metrics"].get("overall_p95_ms", 0.0)
                sev = a.get("severity", "warning").upper()
                print(f"\n⚠️  ALERT DETECTED (Demo)\n"
                      f"  Service: {self.config.service_name}\n"
                      f"  Value:   {val:.0f}ms  Severity: {sev}")
                print("\n💡 Tip: from fastapi_alertengine import actions_router")
        except asyncio.CancelledError:
            pass

    # ── Core metric ingestion ──────────────────────────────────────────────────

    def enqueue_metric(self, metric: dict) -> None:
        metric.setdefault("service_name", self.config.service_name)
        metric.setdefault("instance_id",  self.config.instance_id)
        try:
            self._queue.put_nowait(metric)
            self._stats["enqueued"] += 1
        except asyncio.QueueFull:
            self._stats["dropped"] += 1

    async def drain(self) -> None:
        while True:
            try:
                batch: list = []
                while len(batch) < _DRAIN_BATCH_SIZE and not self._queue.empty():
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                if batch:
                    self._aggregate_batch(batch)
                    if not self._memory_mode:
                        self._cb_write_batch_safe(batch)
                    self._stats["last_drain_at"] = time.time()

                now = time.time()
                if now - self._last_agg_flush_ts >= self.config.agg_flush_interval_seconds:
                    self._flush_aggregates()
                    self._last_agg_flush_ts = now

                # v1.4: baseline snapshot collection
                if (self.config.baseline_preparation_mode and
                        now - self._last_baseline_ts >= self.config.baseline_snapshot_interval_s):
                    self._collect_baseline_snapshot()
                    self._last_baseline_ts = now

                # v1.5: adaptive threshold recalibration
                self._maybe_recalibrate()

                await asyncio.sleep(_DRAIN_SLEEP_S)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("drain() error; recovering")
                await asyncio.sleep(1.0)

    # ── Baseline preparation (v1.4) ────────────────────────────────────────────

    def _collect_baseline_snapshot(self) -> None:
        events = self._fetch_recent(200)
        if not events:
            return
        all_lat    = [e["latency_ms"] for e in events]
        p95_ms     = self._percentile(all_lat, 95)
        p50_ms     = self._percentile(all_lat, 50)
        mean_ms    = sum(all_lat) / len(all_lat)
        error_rate = sum(1 for e in events if e.get("status_code", 0) >= 500) / len(events)
        baseline   = mean_ms
        anomaly    = abs(p95_ms - baseline) / baseline if baseline else 0.0
        cfg    = self.config
        status = "ok"
        if p95_ms > cfg.p95_critical_ms or anomaly > 2.0:
            status = "critical"
        elif p95_ms > cfg.p95_warning_ms or anomaly > 1.0:
            status = "warning"
        if error_rate * 100 > cfg.error_rate_critical_pct:
            status = "critical"
        elif error_rate * 100 > cfg.error_rate_warning_pct and status != "critical":
            status = "warning"

        snap = BaselineSnapshot(
            timestamp    = time.time(),
            service      = self.config.service_name,
            instance_id  = self.config.instance_id,
            sample_size  = len(events),
            p95_ms       = round(p95_ms, 1),
            p50_ms       = round(p50_ms, 1),
            mean_ms      = round(mean_ms, 1),
            error_rate   = round(error_rate, 4),
            anomaly_score = round(anomaly, 3),
            status       = status,
        )
        self._baseline_snapshots.append(snap)
        if not self._memory_mode:
            write_baseline_snapshot(self.redis, self.config, snap)

    def get_baseline_snapshots(self) -> List[dict]:
        return [s.as_dict() for s in self._baseline_snapshots]

    # ── Aggregation ────────────────────────────────────────────────────────────

    def _aggregate_batch(self, batch: list) -> None:
        bucket_size = self.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        for metric in batch:
            service = metric.get("service_name", self.config.service_name)
            path    = metric.get("route_template") or metric.get("path", "")
            method  = str(metric.get("method", "")).upper()
            sc      = metric.get("status_code", 0)
            latency = float(metric.get("latency_ms", 0.0))
            sg      = f"{sc // 100}xx"
            self._recent.append({
                "latency_ms":  latency,
                "type":        "webhook" if "webhook" in path else "api",
                "status_code": sc,
                "route":       path,
            })
            key = (service, now_bucket, path, method, sg)
            if key not in self._agg:
                if len(self._agg) >= MAX_AGG_KEYS:
                    self._dropped_agg_keys += 1
                    continue
                self._agg[key] = [0, 0.0, 0.0]
            row = self._agg[key]
            row[0] += 1; row[1] += latency; row[2] = max(row[2], latency)

    def _flush_aggregates(self) -> None:
        if not self._agg:
            return
        now_bucket = int(time.time()) // self.config.agg_bucket_seconds * self.config.agg_bucket_seconds
        to_flush   = {k: v for k, v in self._agg.items() if k[1] < now_bucket}
        if not to_flush:
            return
        for k in to_flush:
            del self._agg[k]
        if not self._memory_mode:
            flush_aggregates(self.redis, self.config, to_flush)

    async def flush_all_aggregates(self) -> None:
        if not self._agg:
            return
        snapshot = dict(self._agg); self._agg.clear()
        if not self._memory_mode:
            flush_aggregates(self.redis, self.config, snapshot)

    def aggregated_history(self, service=None, last_n_buckets=10):
        return read_aggregates(self.redis, self.config,
                               service or self.config.service_name, last_n_buckets)

    def get_ingestion_stats(self) -> dict:
        return {
            **self._stats,
            "dropped_agg_keys":   self._dropped_agg_keys,
            "dropped_alerts":     self._dropped_alerts,
            "circuit_breaker":    self.get_circuit_breaker_status(),
            "baseline_snapshots": len(self._baseline_snapshots),
            # v1.5 additions
            "adaptive_active":    (self._adaptive_thresholds is not None
                                   and self._adaptive_thresholds.active),
            "health_score":       (self._score_history[-1]
                                   if self._score_history else None),
            "roc_events_recent":  len(self._roc_events),
        }

    # ── Alert delivery ─────────────────────────────────────────────────────────

    def enqueue_alert(self, evaluation: dict) -> bool:
        try:
            self._alert_queue.put_nowait(evaluation); return True
        except asyncio.QueueFull:
            self._dropped_alerts += 1; return False

    async def alert_delivery_loop(self) -> None:
        while True:
            try:
                ev = await self._alert_queue.get()
                await self.deliver_alert(ev)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("alert_delivery_loop error")
                await asyncio.sleep(1.0)

    async def deliver_alert(self, evaluation: dict) -> bool:
        url = self.config.slack_webhook_url
        if not url:
            return False
        now = time.monotonic()
        if now - self._last_slack_ts < self.config.slack_rate_limit_seconds:
            return False
        status  = evaluation.get("status", "unknown")
        emoji   = {"ok": ":white_check_mark:", "warning": ":warning:",
                   "critical": ":rotating_light:"}.get(status, ":question:")
        metrics = evaluation.get("metrics", {})
        score   = evaluation.get("health_score", {})
        score_v = score.get("score", "?") if isinstance(score, dict) else "?"
        msg = (f"{emoji} *fastapi-alertengine v1.5*\n"
               f"Service: `{self.config.service_name}` | Status: *{status.upper()}* "
               f"| Health: {score_v}/100\n"
               f"p95: {metrics.get('overall_p95_ms',0):.1f}ms | "
               f"error rate: {metrics.get('error_rate',0):.1%} | "
               f"samples: {metrics.get('sample_size',0)}")
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                (await c.post(url, json={"text": msg})).raise_for_status()
            self._last_slack_ts = now; return True
        except Exception as exc:
            logger.warning("deliver_alert failed: %s", exc); return False

    # ── History helpers ────────────────────────────────────────────────────────

    def history(self, last_n=100):
        return [
            {"path": e.path, "route_template": e.route_template,
             "method": e.method, "status_code": e.status_code,
             "latency_ms": e.latency_ms, "type": e.type, "trace_id": e.trace_id}
            for e in read_metrics(self.redis, self.config, last_n)
        ]

    def _fetch_recent(self, last_n=200) -> list:
        if self._memory_mode:
            items = list(self._recent)
            return items[-last_n:] if len(items) > last_n else items
        try:
            raw = self.redis.xrevrange(self.config.stream_key, count=last_n)
        except Exception:
            return []
        events = []
        for _, f in raw:
            try:
                events.append({
                    "latency_ms":  float(f.get("latency_ms", 0)),
                    "type":        f.get("type", "api"),
                    "status_code": int(f.get("status", 0)),
                    "route":       f.get("route_template") or f.get("path", ""),
                })
            except Exception:
                continue
        return events

    @staticmethod
    def _percentile(values: list, pct: float) -> float:
        if not values:
            return 0.0
        s   = sorted(values)
        idx = min(int(math.ceil(len(s) * pct / 100)) - 1, len(s) - 1)
        return s[max(idx, 0)]

    # ── Core evaluation (v1.5 enhanced) ───────────────────────────────────────

    def evaluate(self, window_size: int = 200) -> dict:
        """
        Evaluate current system health.

        v1.5 additions to output (backward compatible — no keys removed):
        - health_score:       dict with score, status, components, trend
        - adaptive_thresholds: dict or null
        - rate_of_change:     list of RoC spike events (may be empty)
        - alerts:             now include reason_for_trigger, trend_direction,
                              triggered_by, baseline_comparison (when available)
        """
        events = self._fetch_recent(window_size)
        ts     = int(time.time())

        if not events:
            # Still compute health score from empty state
            hs = compute_health_score(
                p95_ms=0.0, error_rate=0.0, anomaly_score=0.0,
                config=self.config,
                score_history=list(self._score_history),
                adaptive=self._adaptive_thresholds,
            )
            self._score_history.append(hs.score)
            return {
                "status":             "ok",
                "reason":             "no_data",
                "service_name":       self.config.service_name,
                "instance_id":        self.config.instance_id,
                "metrics": {
                    "overall_p95_ms": 0.0, "webhook_p95_ms": 0.0,
                    "api_p95_ms": 0.0, "error_rate": 0.0,
                    "anomaly_score": 0.0, "sample_size": 0,
                },
                "alerts":             [],
                "health_score":       hs.as_dict(),
                "adaptive_thresholds": self.get_adaptive_thresholds(),
                "rate_of_change":     [],
                "timestamp":          ts,
            }

        all_lat     = [e["latency_ms"] for e in events]
        overall_p95 = self._percentile(all_lat, 95)
        webhook_p95 = self._percentile(
            [e["latency_ms"] for e in events if e.get("type") == "webhook"], 95)
        api_p95     = self._percentile(
            [e["latency_ms"] for e in events if e.get("type") == "api"], 95)
        baseline    = sum(all_lat) / len(all_lat)
        anomaly     = abs(overall_p95 - baseline) / baseline if baseline else 0.0
        error_rate  = sum(1 for e in events if e.get("status_code", 0) >= 500) / len(events)

        # ── Choose effective thresholds ────────────────────────────────────────
        at = self._adaptive_thresholds
        if at and at.active:
            eff_warn = at.warning_ms
            eff_crit = at.critical_ms
            threshold_source = "adaptive_threshold"
        else:
            eff_warn = self.config.p95_warning_ms
            eff_crit = self.config.p95_critical_ms
            threshold_source = "absolute_threshold"

        # ── Rate-of-change detection ───────────────────────────────────────────
        roc_events: List[RateOfChangeEvent] = []
        if (self._prev_p95_ms is not None
                and self._prev_error_rate is not None):
            roc_events = detect_rate_of_change(
                current_p95_ms     = overall_p95,
                previous_p95_ms    = self._prev_p95_ms,
                current_error_rate  = error_rate,
                previous_error_rate = self._prev_error_rate,
                config             = self.config,
            )
            for ev in roc_events:
                self._roc_events.append(ev)

        # ── Alert classification ───────────────────────────────────────────────
        cfg    = self.config
        alerts = []
        status = "ok"
        trend  = "stable"  # will be updated from health score below

        # Latency: absolute / adaptive thresholds
        if overall_p95 > eff_crit or anomaly > 2.0:
            alerts.append(enrich_alert(
                alert_type    = "latency_spike",
                severity      = "critical",
                message       = (f"P95 latency ({overall_p95:.0f}ms) exceeds "
                                 f"threshold ({eff_crit:.0f}ms)"),
                current_value = overall_p95,
                threshold     = eff_crit,
                baseline_value = at.median_p95_ms if (at and at.active) else None,
                trend         = trend,
                triggered_by  = threshold_source,
            ))
            status = "critical"
        elif overall_p95 > eff_warn or anomaly > 1.0:
            alerts.append(enrich_alert(
                alert_type    = "latency_spike",
                severity      = "warning",
                message       = (f"P95 latency ({overall_p95:.0f}ms) exceeds "
                                 f"threshold ({eff_warn:.0f}ms)"),
                current_value = overall_p95,
                threshold     = eff_warn,
                baseline_value = at.median_p95_ms if (at and at.active) else None,
                trend         = trend,
                triggered_by  = threshold_source,
            ))
            status = "warning"

        # Error rate
        erpct = error_rate * 100
        if erpct > cfg.error_rate_critical_pct:
            alerts.append(enrich_alert(
                alert_type    = "error_anomaly",
                severity      = "critical",
                message       = (f"Error rate elevated: {erpct:.1f}% "
                                 f"(Baseline: {cfg.error_rate_baseline_pct}%)"),
                current_value = erpct,
                threshold     = cfg.error_rate_critical_pct,
                baseline_value = cfg.error_rate_baseline_pct,
                trend         = trend,
                triggered_by  = "absolute_threshold",
            ))
            status = "critical"
        elif erpct > cfg.error_rate_warning_pct:
            alerts.append(enrich_alert(
                alert_type    = "error_anomaly",
                severity      = "warning",
                message       = (f"Error rate elevated: {erpct:.1f}% "
                                 f"(Baseline: {cfg.error_rate_baseline_pct}%)"),
                current_value = erpct,
                threshold     = cfg.error_rate_warning_pct,
                baseline_value = cfg.error_rate_baseline_pct,
                trend         = trend,
                triggered_by  = "absolute_threshold",
            ))
            if status != "critical":
                status = "warning"

        # Rate-of-change: promote status if RoC spike but no threshold breach
        for roc in roc_events:
            if status == "ok":
                status = "warning"
            alerts.append(enrich_alert(
                alert_type    = "latency_spike" if "latency" in roc.metric else "error_anomaly",
                severity      = "warning",
                message       = (f"Sudden {roc.delta_pct:.0f}% spike in "
                                 f"{roc.metric} ({roc.previous_value:.1f} → "
                                 f"{roc.current_value:.1f})"),
                current_value = roc.current_value,
                threshold     = roc.previous_value,
                baseline_value = roc.previous_value,
                trend         = "increasing",
                triggered_by  = "rate_of_change",
            ))

        # ── Health score ───────────────────────────────────────────────────────
        hs = compute_health_score(
            p95_ms        = overall_p95,
            error_rate    = error_rate,
            anomaly_score = anomaly,
            config        = self.config,
            score_history = list(self._score_history),
            adaptive      = self._adaptive_thresholds,
        )
        self._score_history.append(hs.score)

        # Back-fill trend into alerts now that we have it
        for alert in alerts:
            if hasattr(alert, "trend_direction") and alert.trend_direction == "stable":
                alert.trend_direction = hs.trend

        # ── Update previous-window state ───────────────────────────────────────
        self._prev_p95_ms     = overall_p95
        self._prev_error_rate = error_rate

        # ── Write incident timeline ────────────────────────────────────────────
        if status in ("warning", "critical") and not self._memory_mode:
            for alert in alerts:
                write_incident_event(self.redis, self.config, {
                    "timestamp":  float(ts),
                    "service":    self.config.service_name,
                    "instance":   self.config.instance_id,
                    "status":     status,
                    "event_type": alert.type,
                    "severity":   alert.severity,
                    "message":    alert.message,
                    "metrics": {
                        "p95_ms":      round(overall_p95, 1),
                        "error_rate":  round(error_rate, 4),
                        "samples":     len(events),
                        "health_score": round(hs.score, 1),
                    },
                })

        # ── Build output — v1.3/1.4 keys preserved, v1.5 keys added ──────────
        return {
            "status":       status,
            "service_name": self.config.service_name,
            "instance_id":  self.config.instance_id,
            "metrics": {
                "overall_p95_ms": round(overall_p95, 1),
                "webhook_p95_ms": round(webhook_p95, 1),
                "api_p95_ms":     round(api_p95, 1),
                "error_rate":     round(error_rate, 4),
                "anomaly_score":  round(anomaly, 3),
                "sample_size":    len(events),
            },
            # v1.5: enriched alerts (backward compat — type/message/severity preserved)
            "alerts": [a.as_dict() for a in alerts],
            # v1.5: new top-level keys
            "health_score":        hs.as_dict(),
            "adaptive_thresholds": self.get_adaptive_thresholds(),
            "rate_of_change":      [r.as_dict() for r in roc_events],
            "timestamp":           ts,
        }

    # ── App wiring ─────────────────────────────────────────────────────────────

    def start(self, app, *, health_path: str = "/health/alerts"):
        if isinstance(self.redis, _NullRedis):
            import redis as _rl
            try:
                c = _rl.Redis.from_url(self.config.redis_url, decode_responses=True)
                c.ping(); self.redis = c; self._memory_mode = False
            except Exception:
                self._memory_mode = True
        else:
            try:
                self.redis.ping(); self._memory_mode = False
            except Exception:
                self.redis = _NullRedis(); self._memory_mode = True

        mode_label = "memory" if self._memory_mode else "redis"
        actions_key = bool(os.getenv("ACTION_SECRET_KEY"))
        action_paths = {getattr(r, "path", "") for r in app.router.routes}
        actions_mounted = "/action/confirm" in action_paths or "/action/restart" in action_paths

        print(f"⚡ fastapi-alertengine v1.5.0 ({mode_label} mode)")
        print("─" * 50)
        print(f"  Metrics:    ACTIVE")
        print(f"  Alerts:     ACTIVE")
        print(f"  Actions:    {'ENABLED' if (actions_key and actions_mounted) else 'DISABLED'}")
        print(f"  Baseline:   {'ACTIVE' if self.config.baseline_preparation_mode else 'DISABLED'}")
        print(f"  Learning:   {'ACTIVE' if self.config.baseline_learning_mode else 'DISABLED'}")
        print(f"  Health:     ACTIVE (weights: lat={self.config.health_weight_latency} "
              f"err={self.config.health_weight_errors} "
              f"ano={self.config.health_weight_anomaly})")
        print(f"  RoC:        ACTIVE (latency≥{self.config.roc_latency_spike_pct:.0f}% "
              f"errors≥{self.config.roc_error_rate_spike_pct:.0f}%)")
        print()

        from .middleware import RequestMetricsMiddleware
        app.add_middleware(RequestMetricsMiddleware, alert_engine=self)
        engine = self

        async def _start():
            asyncio.create_task(engine.drain())
            asyncio.create_task(engine.alert_delivery_loop())
            if engine._demo_allowed():
                asyncio.create_task(engine._run_demo_spike())

        async def _stop():
            await engine.flush_all_aggregates()

        app.router.on_startup.append(_start)
        app.router.on_shutdown.append(_stop)

        @app.get(health_path, include_in_schema=False)
        def _h(): return engine.evaluate()

        @app.post("/alerts/evaluate", include_in_schema=False)
        def _ae(): r = engine.evaluate(); engine.enqueue_alert(r); return r

        @app.get("/metrics/history", include_in_schema=False)
        def _mh(service: Optional[str] = None, last_n_buckets: int = 10):
            return {"metrics": engine.aggregated_history(service, last_n_buckets)}

        @app.get("/metrics/ingestion", include_in_schema=False)
        def _mi(): return engine.get_ingestion_stats()

        @app.get("/incidents/timeline", include_in_schema=False)
        def _it(service: Optional[str] = None, since: float = 0.0, limit: int = 50):
            if engine._memory_mode:
                return {"events": [], "mode": "memory",
                        "note": "Timeline requires Redis"}
            return {"events": read_incident_events(
                engine.redis, engine.config,
                service or engine.config.service_name, since=since, limit=limit)}

        @app.get("/__alertengine/status", include_in_schema=False)
        def _status():
            ap = {getattr(r, "path", "") for r in app.router.routes}
            return {
                "version":              "1.5.0",
                "mode":                 "memory" if engine._memory_mode else "redis",
                "ingestion":            engine.get_ingestion_stats(),
                "circuit_breaker":      engine.get_circuit_breaker_status(),
                "baseline_mode":        engine.config.baseline_preparation_mode,
                "learning_mode":        engine.config.baseline_learning_mode,
                "adaptive_thresholds":  engine.get_adaptive_thresholds(),
                "demo_mode":            engine._demo_mode_active,
            }

        @app.get("/intelligence/thresholds", include_in_schema=False)
        def _thresh():
            return {
                "static": {
                    "p95_warning_ms":  engine.config.p95_warning_ms,
                    "p95_critical_ms": engine.config.p95_critical_ms,
                },
                "adaptive": engine.get_adaptive_thresholds(),
                "active_source": (
                    "adaptive" if (engine._adaptive_thresholds
                                   and engine._adaptive_thresholds.active)
                    else "static"
                ),
            }

        @app.get("/intelligence/health", include_in_schema=False)
        def _health_detail():
            ev = engine.evaluate()
            return {
                "health_score":       ev.get("health_score"),
                "adaptive_thresholds": ev.get("adaptive_thresholds"),
                "rate_of_change":     ev.get("rate_of_change"),
                "score_history":      list(engine._score_history),
            }

        if engine.config.baseline_preparation_mode:
            @app.get("/baseline/snapshots", include_in_schema=False)
            def _bs(last_n: int = 60):
                return {"snapshots": engine.get_baseline_snapshots()[-last_n:],
                        "count": len(engine._baseline_snapshots)}

        return self
