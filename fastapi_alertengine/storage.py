# fastapi_alertengine/storage.py
"""
Redis Streams read/write for request metrics.

Two public functions:
  write_metric(rdb, config, path, method, status_code, latency_ms)
  read_metrics(rdb, config, last_n) -> list[RequestMetricEvent]

Both fail silently on Redis errors.
"""

import logging
from typing import Any, List

from .config import AlertConfig
from .schemas import RequestMetricEvent

logger = logging.getLogger(__name__)


def _classify(path: str) -> str:
    return "webhook" if "webhook" in path else "api"


def write_metric(
    rdb,
    config:      AlertConfig,
    path:        str,
    method:      str,
    status_code: int,
    latency_ms:  float,
) -> None:
    """Append one request event to the Redis Stream. Never raises."""
    try:
        rdb.xadd(
            config.stream_key,
            {
                "path":        path,
                "method":      method,
                "status":      str(status_code),
                "latency_ms":  f"{latency_ms:.3f}",
                "type":        _classify(path),
            },
            maxlen=config.stream_maxlen,
            approximate=True,
        )
    except Exception as exc:
        logger.warning("fastapi_alertengine.write_metric failed: %s", exc)


def read_metrics(
    rdb,
    config: AlertConfig,
    last_n: int,
) -> List[RequestMetricEvent]:
    """
    Read the most recent *last_n* events from the stream.

    Returns an empty list on error.
    """
    try:
        raw = rdb.xrevrange(config.stream_key, count=last_n)
    except Exception as exc:
        logger.warning("fastapi_alertengine.read_metrics failed: %s", exc)
        return []

    events: List[RequestMetricEvent] = []
    for _sid, fields in raw:
        try:
            events.append(RequestMetricEvent(
                path        = fields.get("path", ""),
                method      = fields.get("method", ""),
                status_code = int(fields.get("status", 0)),
                latency_ms  = float(fields.get("latency_ms", 0)),
                type        = fields.get("type", "api"),
            ))
        except (ValueError, TypeError):
            continue
    return events


def aggregate(
    rdb,
    config: AlertConfig,
    last_n: int = 500,
) -> dict:
    """
    Read *last_n* events and return p95 latency by traffic type.

    Returns::

        {
            "webhook_latency": {"p95_ms": float | None, "count": int},
            "api_latency":     {"p95_ms": float | None, "count": int},
            "overall_latency": {"p95_ms": float | None, "count": int},
        }
    """
    events = read_metrics(rdb, config, last_n)

    webhook_ms = [e.latency_ms for e in events if e.type == "webhook"]
    api_ms     = [e.latency_ms for e in events if e.type == "api"]
    all_ms     = webhook_ms + api_ms

    return {
        "webhook_latency": _bucket(webhook_ms),
        "api_latency":     _bucket(api_ms),
        "overall_latency": _bucket(all_ms),
    }


# ── Internal ──────────────────────────────────────────────────────────────────

def _p95(values: List[float]):
    if not values:
        return None
    s   = sorted(values)
    idx = max(0, int(len(s) * 0.95) - 1)
    return round(s[idx], 3)


def _bucket(values: List[float]) -> dict:
    return {"p95_ms": _p95(values), "count": len(values)}
