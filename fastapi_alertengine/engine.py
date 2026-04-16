# fastapi_alertengine/engine.py
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
from .storage import flush_aggregates, read_aggregates, read_metrics, write_batch, write_incident_event, read_incident_events

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE   = 10_000
_DRAIN_BATCH_SIZE = 100
_DRAIN_SLEEP_S    = 0.05
MAX_AGG_KEYS      = 50_000

# Seconds to wait for real traffic before activating demo mode.
# Override with ALERTENGINE_DEMO_DELAY env var (useful in tests/CI).
_DEMO_DELAY_S: int = 12


class _NullPipeline:
    def xadd(self, *a, **kw): return self
    def hset(self, *a, **kw): return self
    def expire(self, *a, **kw): return self
    def zadd(self, *a, **kw): return self
    def hgetall(self, *a, **kw): return self
    def execute(self, *a, **kw): return []


class _NullRedis:
    """Silent no-op Redis — used when Redis is unavailable."""
    def ping(self): raise ConnectionError("_NullRedis: no Redis configured")
    def xadd(self, *a, **kw): pass
    def xrevrange(self, *a, **kw): return []
    def zrevrange(self, *a, **kw): return []
    def hgetall(self, *a, **kw): return {}
    def expire(self, *a, **kw): pass
    def zadd(self, *a, **kw): pass
    def pipeline(self, *a, **kw): return _NullPipeline()


class AlertEngine:
    """
    Real-time SLO / latency alert engine with non-blocking metric ingestion.

    Quickest usage::

        from fastapi_alertengine import instrument
        instrument(app)

    Manual usage::

        engine = AlertEngine(config)
        engine.start(app)
    """

    def __init__(self, redis=None, config=None):
        if isinstance(redis, AlertConfig) and config is None:
            config, redis = redis, None
        if config is None: config = AlertConfig()
        if redis is None: redis = _NullRedis()
        self.redis  = redis
        self.config = config
        self._memory_mode = isinstance(redis, _NullRedis)
        self._recent = collections.deque(maxlen=200)
        self._queue  = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._agg    = {}
        self._last_agg_flush_ts = 0.0
        self._dropped_agg_keys  = 0
        self._stats  = {"enqueued": 0, "dropped": 0, "last_drain_at": None}
        self._alert_queue    = asyncio.Queue(maxsize=1_000)
        self._dropped_alerts = 0
        self._last_slack_ts  = 0.0
        # Onboarding state
        self._first_request_at: Optional[float] = None
        self._demo_mode_active: bool = False
        self._demo_alert_shown: bool = False

    # ── Demo / onboarding helpers ─────────────────────────────────────────────

    def _demo_allowed(self) -> bool:
        """
        Return True if demo spike mode is permitted for this engine instance.

        Demo mode is suppressed in Redis mode (real infrastructure), in any
        environment named 'production'/'prod', or when the caller sets
        ``ALERTENGINE_DISABLE_DEMO=1``.
        """
        if not self._memory_mode:
            return False
        env = os.getenv("ENV", os.getenv("ENVIRONMENT", "")).lower()
        if env in ("production", "prod"):
            return False
        if os.getenv("ALERTENGINE_DISABLE_DEMO", "").lower() in ("1", "true", "yes"):
            return False
        return True

    async def _run_demo_spike(self) -> None:
        """
        Background task: inject synthetic traffic after ``ALERTENGINE_DEMO_DELAY``
        seconds when no real traffic has arrived, so dashboards are never empty.

        Only runs in memory mode and non-production environments.
        """
        try:
            delay = int(os.getenv("ALERTENGINE_DEMO_DELAY", str(_DEMO_DELAY_S)))
            await asyncio.sleep(delay)

            if self._first_request_at is not None:
                return  # real traffic arrived — demo not needed

            if not self._demo_allowed():
                return

            self._demo_mode_active = True
            print("\n🧪 Demo Mode: synthetic metrics generated for preview")

            # Inject a mix of high-latency and error events.
            for _ in range(45):
                self._recent.append({
                    "latency_ms": random.uniform(900, 2_500),
                    "type": "api",
                    "status_code": 200,
                })
            for _ in range(5):
                self._recent.append({
                    "latency_ms": random.uniform(100, 300),
                    "type": "api",
                    "status_code": 500,
                })

            # Evaluate and emit ONE showcase alert.
            evaluation = self.evaluate()
            if evaluation.get("alerts") and not self._demo_alert_shown:
                self._demo_alert_shown = True
                alert = evaluation["alerts"][0]
                metric_val = evaluation["metrics"].get("overall_p95_ms", 0.0)
                sev = alert.get("severity", "warning").upper()
                print(f"\n⚠️  ALERT DETECTED (Demo)")
                print(f"  Service:  {self.config.service_name}")
                print(f"  Metric:   P95 latency breach")
                print(f"  Value:    {metric_val:.0f}ms")
                print(f"  Severity: {sev}")
                print(f"\n💡 Tip: Enable incident actions:")
                print(f"   from fastapi_alertengine import actions_router")
                print(f"   app.include_router(actions_router)")
        except asyncio.CancelledError:
            pass

    # ── Core metric ingestion ─────────────────────────────────────────────────

    def enqueue_metric(self, metric):
        metric.setdefault("service_name", self.config.service_name)
        metric.setdefault("instance_id",  self.config.instance_id)
        try:
            self._queue.put_nowait(metric)
            self._stats["enqueued"] += 1
        except asyncio.QueueFull:
            self._stats["dropped"] += 1

    async def drain(self):
        while True:
            try:
                batch = []
                while len(batch) < _DRAIN_BATCH_SIZE and not self._queue.empty():
                    try: batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty: break
                if batch:
                    write_batch(self.redis, self.config, batch)
                    self._aggregate_batch(batch)
                    self._stats["last_drain_at"] = time.time()
                now = time.time()
                if now - self._last_agg_flush_ts >= self.config.agg_flush_interval_seconds:
                    self._flush_aggregates()
                    self._last_agg_flush_ts = now
                await asyncio.sleep(_DRAIN_SLEEP_S)
            except asyncio.CancelledError: break
            except Exception:
                logger.exception("drain() error; recovering")
                await asyncio.sleep(1.0)

    def _aggregate_batch(self, batch):
        bucket_size = self.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        for metric in batch:
            service  = metric.get("service_name", self.config.service_name)
            path     = metric.get("path", "")
            method   = str(metric.get("method", "")).upper()
            sc       = metric.get("status_code", 0)
            latency  = float(metric.get("latency_ms", 0.0))
            sg       = f"{sc // 100}xx"
            self._recent.append({"latency_ms": latency, "type": "webhook" if "webhook" in path else "api", "status_code": sc})
            key = (service, now_bucket, path, method, sg)
            if key not in self._agg:
                if len(self._agg) >= MAX_AGG_KEYS:
                    self._dropped_agg_keys += 1; continue
                self._agg[key] = [0, 0.0, 0.0]
            row = self._agg[key]; row[0] += 1; row[1] += latency; row[2] = max(row[2], latency)

    def _flush_aggregates(self):
        if not self._agg: return
        bucket_size = self.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        to_flush    = {k: v for k, v in self._agg.items() if k[1] < now_bucket}
        if not to_flush: return
        for k in to_flush: del self._agg[k]
        flush_aggregates(self.redis, self.config, to_flush)

    async def flush_all_aggregates(self):
        if not self._agg: return
        snapshot = dict(self._agg); self._agg.clear()
        flush_aggregates(self.redis, self.config, snapshot)

    def aggregated_history(self, service=None, last_n_buckets=10):
        return read_aggregates(self.redis, self.config, service or self.config.service_name, last_n_buckets)

    def get_ingestion_stats(self):
        return {**self._stats, "dropped_agg_keys": self._dropped_agg_keys, "dropped_alerts": self._dropped_alerts}

    def enqueue_alert(self, evaluation):
        try: self._alert_queue.put_nowait(evaluation); return True
        except asyncio.QueueFull: self._dropped_alerts += 1; return False

    async def alert_delivery_loop(self):
        while True:
            try:
                ev = await self._alert_queue.get()
                await self.deliver_alert(ev)
            except asyncio.CancelledError: break
            except Exception: logger.exception("alert_delivery_loop error"); await asyncio.sleep(1.0)

    async def deliver_alert(self, evaluation):
        url = self.config.slack_webhook_url
        if not url: return False
        now = time.monotonic()
        if now - self._last_slack_ts < self.config.slack_rate_limit_seconds: return False
        status  = evaluation.get("status", "unknown")
        emoji   = {"ok": ":white_check_mark:", "warning": ":warning:", "critical": ":rotating_light:"}.get(status, ":question:")
        metrics = evaluation.get("metrics", {})
        msg = (f"{emoji} *fastapi-alertengine*\nService: `{self.config.service_name}` Status: *{status.upper()}*\n"
               f"p95: {metrics.get('overall_p95_ms',0):.1f}ms | error rate: {metrics.get('error_rate',0):.1%} | samples: {metrics.get('sample_size',0)}")
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                (await c.post(url, json={"text": msg})).raise_for_status()
            self._last_slack_ts = now; return True
        except Exception as exc: logger.warning("deliver_alert failed: %s", exc); return False

    def history(self, last_n=100):
        return [{"path": e.path, "method": e.method, "status_code": e.status_code, "latency_ms": e.latency_ms, "type": e.type}
                for e in read_metrics(self.redis, self.config, last_n)]

    def _fetch_recent(self, last_n=200):
        if self._memory_mode:
            items = list(self._recent); return items[-last_n:] if len(items) > last_n else items
        try: raw = self.redis.xrevrange(self.config.stream_key, count=last_n)
        except Exception: return []
        events = []
        for _, f in raw:
            try: events.append({"latency_ms": float(f.get("latency_ms",0)), "type": f.get("type","api"), "status_code": int(f.get("status",0))})
            except Exception: continue
        return events

    @staticmethod
    def _percentile(values, pct):
        if not values: return 0.0
        s = sorted(values); idx = min(int(math.ceil(len(s)*pct/100))-1, len(s)-1)
        return s[max(idx,0)]

    def evaluate(self, window_size=200):
        events = self._fetch_recent(window_size)
        ts     = int(time.time())
        if not events:
            return {"status": "ok", "reason": "no_data", "service_name": self.config.service_name,
                    "instance_id": self.config.instance_id,
                    "metrics": {"overall_p95_ms": 0.0, "webhook_p95_ms": 0.0, "api_p95_ms": 0.0,
                                "error_rate": 0.0, "anomaly_score": 0.0, "sample_size": 0},
                    "alerts": [], "timestamp": ts}
        all_lat = [e["latency_ms"] for e in events]
        overall_p95 = self._percentile(all_lat, 95)
        webhook_p95 = self._percentile([e["latency_ms"] for e in events if e["type"]=="webhook"], 95)
        api_p95     = self._percentile([e["latency_ms"] for e in events if e["type"]=="api"], 95)
        baseline    = sum(all_lat)/len(all_lat)
        anomaly     = abs(overall_p95-baseline)/baseline if baseline else 0.0
        error_rate  = sum(1 for e in events if e["status_code"]>=500)/len(events)
        cfg = self.config; alerts = []; status = "ok"
        if overall_p95 > cfg.p95_critical_ms or anomaly > 2.0:
            alerts.append({"type": "latency_spike", "severity": "critical",
                           "message": f"P95 latency ({overall_p95:.0f}ms) exceeds threshold ({cfg.p95_critical_ms:.0f}ms)"}); status="critical"
        elif overall_p95 > cfg.p95_warning_ms or anomaly > 1.0:
            alerts.append({"type": "latency_spike", "severity": "warning",
                           "message": f"P95 latency ({overall_p95:.0f}ms) exceeds threshold ({cfg.p95_warning_ms:.0f}ms)"}); status="warning"
        erpct = error_rate*100
        if erpct > cfg.error_rate_critical_pct:
            alerts.append({"type": "error_anomaly", "severity": "critical",
                           "message": f"Error rate elevated: {erpct:.1f}% (Baseline: {cfg.error_rate_baseline_pct}%)"}); status="critical"
        elif erpct > cfg.error_rate_warning_pct:
            alerts.append({"type": "error_anomaly", "severity": "warning",
                           "message": f"Error rate elevated: {erpct:.1f}% (Baseline: {cfg.error_rate_baseline_pct}%)"}); status="warning" if status!="critical" else status
        # ── Write to incident timeline (Redis ZSET) if degraded ────────────
        if status in ("warning", "critical") and not self._memory_mode:
            import time as _t
            for alert in alerts:
                write_incident_event(self.redis, self.config, {
                    "timestamp": float(ts),
                    "service":   self.config.service_name,
                    "instance":  self.config.instance_id,
                    "status":    status,
                    "event_type": alert.get("type", "ALERT"),
                    "severity":  alert.get("severity", status),
                    "message":   alert.get("message", ""),
                    "metrics": {
                        "p95_ms":     round(overall_p95, 1),
                        "error_rate": round(error_rate, 4),
                        "samples":    len(events),
                    },
                })

        return {"status": status, "service_name": self.config.service_name, "instance_id": self.config.instance_id,
                "metrics": {"overall_p95_ms": round(overall_p95,1), "webhook_p95_ms": round(webhook_p95,1),
                            "api_p95_ms": round(api_p95,1), "error_rate": round(error_rate,4),
                            "anomaly_score": round(anomaly,3), "sample_size": len(events)},
                "alerts": alerts, "timestamp": ts}

    # ── App wiring ────────────────────────────────────────────────────────────

    def start(self, app, *, health_path="/health/alerts"):
        # Detect Redis availability.
        if isinstance(self.redis, _NullRedis):
            import redis as _rl
            try:
                c = _rl.Redis.from_url(self.config.redis_url, decode_responses=True); c.ping()
                self.redis = c; self._memory_mode = False
            except Exception: self._memory_mode = True
        else:
            try: self.redis.ping(); self._memory_mode = False
            except Exception: self.redis = _NullRedis(); self._memory_mode = True

        # ── Startup banner ────────────────────────────────────────────────────
        mode_word  = "memory" if self._memory_mode else "redis"
        mode_label = "memory (no Redis detected)" if self._memory_mode else "redis"
        actions_key = bool(os.getenv("ACTION_SECRET_KEY"))
        action_paths = {getattr(r, "path", "") for r in app.router.routes}
        actions_mounted = "/action/confirm" in action_paths or "/action/restart" in action_paths
        actions_status = "ENABLED" if (actions_key and actions_mounted) else "DISABLED"

        print(f"⚡ fastapi-alertengine initialized ({mode_word} mode)")
        print("─" * 50)
        print(f"  Mode:    {mode_label}")
        print(f"  Metrics: ACTIVE")
        print(f"  Alerts:  ACTIVE")
        print(f"  Actions: {actions_status}")
        print(f"\n  Endpoints:")
        print(f"    GET  {health_path}")
        print(f"    POST /alerts/evaluate")
        print(f"    GET  /metrics/history")
        print(f"    GET  /metrics/ingestion")
        print(f"    GET  /__alertengine/status")
        print(f"\n  Waiting for traffic...")
        print()

        # ── Middleware and lifecycle hooks ─────────────────────────────────────
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

        # ── Auto-registered endpoints ──────────────────────────────────────────
        @app.get(health_path, include_in_schema=False)
        def _h(): return engine.evaluate()

        @app.post("/alerts/evaluate", include_in_schema=False)
        def _ae(): r = engine.evaluate(); engine.enqueue_alert(r); return r

        @app.get("/metrics/history", include_in_schema=False)
        def _mh(service: Optional[str]=None, last_n_buckets: int=10):
            return {"metrics": engine.aggregated_history(service=service, last_n_buckets=last_n_buckets)}

        @app.get("/metrics/ingestion", include_in_schema=False)
        def _mi(): return engine.get_ingestion_stats()

        @app.get("/incidents/timeline", include_in_schema=False)
        def _it(service: Optional[str] = None, since: float = 0.0, limit: int = 50):
            if engine._memory_mode:
                return {"events": [], "mode": "memory", "note": "Timeline requires Redis"}
            return {"events": read_incident_events(
                engine.redis, engine.config,
                service or engine.config.service_name,
                since=since, limit=limit,
            )}

        @app.get("/__alertengine/status", include_in_schema=False)
        def _status():
            ap = {getattr(r, "path", "") for r in app.router.routes}
            return {
                "mode": "memory" if engine._memory_mode else "redis",
                "metrics_active": True,
                "alerts_active": True,
                "actions_enabled": "/action/confirm" in ap,
                "ingestion": engine.get_ingestion_stats(),
                "demo_mode": engine._demo_mode_active,
            }

        return self

