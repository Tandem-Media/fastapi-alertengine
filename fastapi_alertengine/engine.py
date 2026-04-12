# fastapi_alertengine/engine.py

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from .config import AlertConfig
from .storage import flush_aggregates, read_aggregates, read_metrics, write_batch

logger = logging.getLogger(__name__)

# Public constant — kept for backwards-compatibility with any code that imports it.
MAX_QUEUE_SIZE    = 10_000
_DRAIN_BATCH_SIZE = 100
_DRAIN_SLEEP_S    = 0.05
# Maximum number of distinct aggregation keys held in memory at once.
# New keys are dropped (and counted) when this limit is reached.
MAX_AGG_KEYS      = 50_000


class AlertEngine:
    """
    Real-time SLO / latency alert engine.

    Metrics are pushed onto a bounded ``asyncio.Queue`` by the middleware and
    flushed to Redis Streams in batches by the background ``drain()`` task.
    A separate ``alert_delivery_loop()`` task processes the alert queue for
    non-blocking Slack delivery.
    """

    def __init__(self, redis, config: AlertConfig) -> None:
        self.redis  = redis
        self.config = config
        # Metric ingestion queue — bounded; newest metric dropped on overflow.
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10_000)
        # In-memory aggregation buffer.
        # Key: (service, bucket_ts, path, method, status_group)
        # Value: [count, total_latency, max_latency]
        self._agg: Dict[tuple, list] = {}
        self._last_agg_flush_ts: float = 0.0
        # Ingestion counters for the /metrics/ingestion endpoint.
        self._stats: Dict[str, Any] = {
            "enqueued":      0,
            "dropped":       0,
            "last_drain_at": None,
        }
        self._dropped_agg_keys: int = 0   # new keys dropped due to memory guard
        self._dropped_alerts:   int = 0   # alerts dropped due to full alert queue
        # Alert delivery queue — consumed by alert_delivery_loop().
        self._alert_queue: asyncio.Queue = asyncio.Queue(maxsize=1_000)
        # Timestamp of last successful Slack notification.
        self._last_slack_ts: float = 0.0

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def enqueue_metric(self, metric: dict) -> None:
        """
        Push one metric dict onto the queue without blocking the request path.

        Increments ``enqueued`` or ``dropped`` ingestion counters accordingly.
        service_name and instance_id are added from config if absent.
        """
        metric.setdefault("service_name", self.config.service_name)
        metric.setdefault("instance_id",  self.config.instance_id)
        try:
            self._queue.put_nowait(metric)
            self._stats["enqueued"] += 1
        except asyncio.QueueFull:
            self._stats["dropped"] += 1

    # ── Background drain ──────────────────────────────────────────────────────

    async def drain(self) -> None:
        """
        Continuously flush the metric queue to Redis Streams in batches.

        Also feeds the in-memory aggregation buffer and periodically flushes
        completed minute-buckets to Redis hashes.
        Designed for ``asyncio.create_task(engine.drain())``.
        """
        while True:
            try:
                batch: List[dict] = []
                while len(batch) < _DRAIN_BATCH_SIZE and not self._queue.empty():
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                if batch:
                    write_batch(self.redis, self.config, batch)
                    self._aggregate_batch(batch)
                    self._stats["last_drain_at"] = time.time()

                # Periodically flush completed aggregation buckets to Redis.
                now = time.time()
                if now - self._last_agg_flush_ts >= self.config.agg_flush_interval_seconds:
                    self._flush_aggregates()
                    self._last_agg_flush_ts = now

                await asyncio.sleep(_DRAIN_SLEEP_S)
            except asyncio.CancelledError:
                break  # clean shutdown
            except Exception:
                logger.exception("drain() loop encountered an unexpected error; recovering")
                await asyncio.sleep(1.0)

    # ── Aggregation ───────────────────────────────────────────────────────────

    def _aggregate_batch(self, batch: List[dict]) -> None:
        """Accumulate a batch of metrics into the in-memory aggregation buffer.

        New aggregation keys are silently dropped (and counted) when the buffer
        exceeds MAX_AGG_KEYS to prevent unbounded memory growth.
        """
        bucket_size = self.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size

        for metric in batch:
            service      = metric.get("service_name", self.config.service_name)
            path         = metric.get("path", "")
            method       = str(metric.get("method", "")).upper()
            status_code  = metric.get("status_code", 0)
            latency      = float(metric.get("latency_ms", 0.0))
            status_group = f"{status_code // 100}xx"

            key = (service, now_bucket, path, method, status_group)
            if key not in self._agg:
                if len(self._agg) >= MAX_AGG_KEYS:
                    self._dropped_agg_keys += 1
                    continue
                self._agg[key] = [0, 0.0, 0.0]  # count, total, max
            row = self._agg[key]
            row[0] += 1
            row[1] += latency
            row[2]  = max(row[2], latency)

    def _flush_aggregates(self) -> None:
        """
        Write data for completed (past) buckets to Redis and remove from buffer.

        The current bucket stays in memory so it can keep accumulating.
        This means each bucket is written to Redis exactly once, allowing a
        simple HSET (no atomic increment needed).
        """
        if not self._agg:
            return
        bucket_size = self.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size

        # Only past (completed) buckets.
        to_flush = {k: v for k, v in self._agg.items() if k[1] < now_bucket}
        if not to_flush:
            return
        for k in to_flush:
            del self._agg[k]
        flush_aggregates(self.redis, self.config, to_flush)

    async def flush_all_aggregates(self) -> None:
        """
        Flush ALL buckets — including the current one — to Redis.

        Intended for graceful-shutdown hooks. Clears the in-memory buffer.
        Never raises, even if Redis is unavailable.
        """
        if not self._agg:
            return
        snapshot = dict(self._agg)
        self._agg.clear()
        flush_aggregates(self.redis, self.config, snapshot)

    def aggregated_history(self, service: Optional[str] = None, last_n_buckets: int = 10) -> List[dict]:
        """Return aggregated history for *service* from Redis hashes."""
        svc = service or self.config.service_name
        return read_aggregates(self.redis, self.config, svc, last_n_buckets)

    # ── Ingestion stats ───────────────────────────────────────────────────────

    def get_ingestion_stats(self) -> Dict[str, Any]:
        """Return a snapshot of ingestion and alert counters."""
        return {
            **self._stats,
            "dropped_agg_keys": self._dropped_agg_keys,
            "dropped_alerts":   self._dropped_alerts,
        }

    # ── Alert queue ───────────────────────────────────────────────────────────

    def enqueue_alert(self, evaluation: dict) -> bool:
        """
        Enqueue an evaluation result for background Slack delivery.

        Returns True if enqueued, False if the alert queue is full (dropped).
        Non-blocking — safe to call from any synchronous context.
        """
        try:
            self._alert_queue.put_nowait(evaluation)
            return True
        except asyncio.QueueFull:
            self._dropped_alerts += 1
            return False

    async def alert_delivery_loop(self) -> None:
        """
        Background loop that consumes from the alert queue and calls deliver_alert().

        Rate limiting is handled inside deliver_alert(). Designed for
        ``asyncio.create_task(engine.alert_delivery_loop())``.
        """
        while True:
            try:
                evaluation = await self._alert_queue.get()
                await self.deliver_alert(evaluation)
            except asyncio.CancelledError:
                break  # clean shutdown
            except Exception:
                logger.exception("alert_delivery_loop error; recovering")
                await asyncio.sleep(1.0)

    # ── Slack delivery ────────────────────────────────────────────────────────

    async def deliver_alert(self, evaluation: Dict[str, Any]) -> bool:
        """
        Post an alert to Slack if a webhook URL is configured and the
        rate-limit window has elapsed.

        Returns ``True`` if a message was sent, ``False`` otherwise.
        """
        url = self.config.slack_webhook_url
        if not url:
            return False

        now = time.monotonic()
        if now - self._last_slack_ts < self.config.slack_rate_limit_seconds:
            return False  # rate-limited

        status  = evaluation.get("status", "unknown")
        emoji   = {
            "ok":       ":white_check_mark:",
            "warning":  ":warning:",
            "critical": ":rotating_light:",
        }.get(status, ":question:")
        metrics = evaluation.get("metrics", {})
        message = (
            f"{emoji} *fastapi-alertengine alert*\n"
            f"Service: `{self.config.service_name}` | Instance: `{self.config.instance_id}`\n"
            f"Status: *{status.upper()}*\n"
            f"p95 latency: {metrics.get('overall_p95_ms', 0):.1f} ms | "
            f"error rate: {metrics.get('error_rate', 0):.1%} | "
            f"samples: {metrics.get('sample_size', 0)}"
        )

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json={"text": message})
                resp.raise_for_status()
            self._last_slack_ts = now
            return True
        except Exception as exc:
            logger.warning("deliver_alert failed: %s", exc)
            return False

    # ── History ───────────────────────────────────────────────────────────────

    def history(self, last_n: int = 100) -> List[dict]:
        """
        Return the most recent *last_n* raw metric events from Redis Streams
        as plain dicts (no analysis).
        """
        events = read_metrics(self.redis, self.config, last_n)
        return [
            {
                "path":        e.path,
                "method":      e.method,
                "status_code": e.status_code,
                "latency_ms":  e.latency_ms,
                "type":        e.type,
            }
            for e in events
        ]

    # ── Analysis ──────────────────────────────────────────────────────────────

    def _fetch_recent(self, last_n: int = 200):
        try:
            raw = self.redis.xrevrange(self.config.stream_key, count=last_n)
        except Exception:
            return []

        events = []
        for _, fields in raw:
            try:
                events.append({
                    "latency_ms":  float(fields.get("latency_ms", 0)),
                    "type":        fields.get("type", "api"),
                    # Written as "status" by storage.write_metric
                    "status_code": int(fields.get("status", 0)),
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
            return {"status": "ok", "reason": "no_data"}

        all_lat     = [e["latency_ms"] for e in events]
        webhook_lat = [e["latency_ms"] for e in events if e["type"] == "webhook"]
        api_lat     = [e["latency_ms"] for e in events if e["type"] == "api"]

        def p95(values):
            if not values:
                return 0.0
            values.sort()
            idx = int(len(values) * 0.95)
            return values[min(idx, len(values) - 1)]

        overall_p95 = p95(all_lat)
        webhook_p95 = p95(webhook_lat)
        api_p95     = p95(api_lat)

        baseline = sum(all_lat) / len(all_lat)
        anomaly  = self._anomaly_score(overall_p95, baseline)

        status = "ok"
        if overall_p95 > 3000 or anomaly > 2.0:
            status = "critical"
        elif overall_p95 > 1000 or anomaly > 1.0:
            status = "warning"

        error_rate = sum(1 for e in events if e["status_code"] >= 500) / len(events)
        if error_rate > 0.2:
            status = "critical"
        elif error_rate > 0.1 and status != "critical":
            status = "warning"

        return {
            "status": status,
            "service_name": self.config.service_name,
            "instance_id":  self.config.instance_id,
            "metrics": {
                "overall_p95_ms": overall_p95,
                "webhook_p95_ms": webhook_p95,
                "api_p95_ms":     api_p95,
                "error_rate":     error_rate,
                "anomaly_score":  anomaly,
                "sample_size":    len(events),
            },
            "thresholds": {
                "p95_warning_ms":      1000,
                "p95_critical_ms":     3000,
                "anomaly_warning":     1.0,
                "anomaly_critical":    2.0,
                "error_rate_warning":  0.1,
                "error_rate_critical": 0.2,
            },
            "timestamp": int(time.time()),
        }
