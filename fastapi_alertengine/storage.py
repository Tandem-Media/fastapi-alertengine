# fastapi_alertengine/storage.py
"""
Redis Streams read/write for request metrics, plus aggregation helpers.

Public functions:
  write_metric(rdb, config, metric)              -- write one metric dict
  write_batch(rdb, config, metrics)              -- write many metrics via pipeline
  flush_aggregates(rdb, config, snapshot)        -- persist minute-bucket aggregates
  read_aggregates(rdb, config, service, ...)     -> list[dict]
  read_metrics(rdb, config, last_n)              -> list[RequestMetricEvent]
  aggregate(rdb, config, last_n)                 -> dict

All Redis operations fail silently.
"""

import json
import logging
from typing import List

from .config import AlertConfig
from .schemas import RequestMetricEvent

logger = logging.getLogger(__name__)

# ── Canonical stream field schema ─────────────────────────────────────────
#
#   path        str   request.url.path
#   method      str   HTTP verb, upper-cased
#   status      str   HTTP status code as string  e.g. "200"
#   latency_ms  str   wall-clock ms, 3 d.p.       e.g. "143.720"
#   type        str   "api" | "webhook"
#
# All values are stored as strings because Redis Streams hash values are bytes.


def _classify(path: str) -> str:
    """Tag a request as 'webhook' or 'api' based on its path."""
    return "webhook" if "webhook" in path.lower() else "api"


def _build_fields(config: AlertConfig, metric: dict) -> dict:
    """Build the Redis Stream field map from a metric dict."""
    return {
        "path":         metric["path"],
        "method":       str(metric["method"]).upper(),
        "status":       str(metric["status_code"]),
        "latency_ms":   f"{metric['latency_ms']:.3f}",
        "type":         _classify(metric["path"]),
        "service_name": metric.get("service_name", config.service_name),
        "instance_id":  metric.get("instance_id", config.instance_id),
    }


def write_metric(
    rdb,
    config: AlertConfig,
    metric: dict,
) -> None:
    """
    Append one request event to the Redis Stream. Never raises.

    Required keys: path, method, status_code, latency_ms.
    Optional keys: service_name, instance_id (fall back to config values).
    """
    try:
        rdb.xadd(
            config.stream_key,
            _build_fields(config, metric),
            maxlen=config.stream_maxlen,
            approximate=True,
        )
    except Exception as exc:
        logger.warning("fastapi_alertengine.write_metric failed: %s", exc)


def write_batch(
    rdb,
    config: AlertConfig,
    metrics: List[dict],
) -> None:
    """
    Write a list of metric dicts using a Redis pipeline for efficiency.

    Never raises — individual pipeline errors are swallowed.
    """
    if not metrics:
        return
    try:
        pipe = rdb.pipeline(transaction=False)
        for metric in metrics:
            pipe.xadd(
                config.stream_key,
                _build_fields(config, metric),
                maxlen=config.stream_maxlen,
                approximate=True,
            )
        pipe.execute()
    except Exception as exc:
        logger.warning("fastapi_alertengine.write_batch failed: %s", exc)


def flush_aggregates(rdb, config: AlertConfig, snapshot: dict) -> None:
    """
    Write completed-bucket aggregates to Redis hashes via pipeline.

    snapshot keys: (service, bucket_ts, path, method, status_group)
    snapshot values: [count, total_latency, max_latency]

    Redis layout:
      Hash key:  {agg_key_prefix}:{service}:{bucket_ts}
      Field:     {path}|{method}|{status_group}
      Value:     JSON: {"c": count, "t": total, "m": max_latency}

      ZSET index key: {agg_key_prefix}:index:{service}
      Score: bucket_ts (unix timestamp)
      Member: str(bucket_ts)

    Each key gets an EXPIRE of config.agg_ttl_seconds.
    Never raises.
    """
    if not snapshot:
        return
    try:
        pipe = rdb.pipeline(transaction=False)
        seen: set = set()  # (service, bucket_ts) pairs — for expire + ZADD

        for (service, bucket_ts, path, method, status_group), (count, total, max_lat) in snapshot.items():
            redis_key = f"{config.agg_key_prefix}:{service}:{bucket_ts}"
            field = f"{path}|{method}|{status_group}"
            value = json.dumps({"c": count, "t": round(total, 3), "m": round(max_lat, 3)})
            pipe.hset(redis_key, field, value)
            seen.add((service, bucket_ts))

        # Group bucket_ts values by service for ZADD, then apply EXPIRE to all keys.
        by_service: dict = {}
        for (service, bucket_ts) in seen:
            redis_key = f"{config.agg_key_prefix}:{service}:{bucket_ts}"
            pipe.expire(redis_key, config.agg_ttl_seconds)
            if service not in by_service:
                by_service[service] = {}
            by_service[service][str(bucket_ts)] = bucket_ts  # member → score

        for service, mapping in by_service.items():
            index_key = f"{config.agg_key_prefix}:index:{service}"
            pipe.zadd(index_key, mapping)  # idempotent: same member updates same score
            pipe.expire(index_key, config.agg_ttl_seconds)

        pipe.execute()
    except Exception as exc:
        logger.warning("fastapi_alertengine.flush_aggregates failed: %s", exc)


def read_aggregates(
    rdb,
    config: AlertConfig,
    service: str,
    last_n_buckets: int = 10,
) -> List[dict]:
    """
    Read aggregated metrics for *service* from Redis.

    Uses the ZSET index ``{agg_key_prefix}:index:{service}`` to retrieve the
    most recent *last_n_buckets* bucket timestamps in descending order, then
    fetches each bucket hash via a single pipeline.

    Returns a list of dicts with:
      bucket_ts, service, path, method, status_group,
      count, avg_latency_ms, max_latency_ms

    Values may be JSON (current format) or pipe-delimited (legacy fallback).
    Returns [] on error or when no data exists.
    """
    index_key = f"{config.agg_key_prefix}:index:{service}"
    results: List[dict] = []
    try:
        members = rdb.zrevrange(index_key, 0, last_n_buckets - 1)
        if not members:
            return []

        # Fetch all bucket hashes in one pipeline.
        pipe = rdb.pipeline(transaction=False)
        for bucket_ts_str in members:
            redis_key = f"{config.agg_key_prefix}:{service}:{bucket_ts_str}"
            pipe.hgetall(redis_key)
        hash_results = pipe.execute()

        for bucket_ts_str, data in zip(members, hash_results):
            try:
                bucket_ts = int(bucket_ts_str)
            except (ValueError, TypeError):
                bucket_ts = 0

            for field, value in (data or {}).items():
                try:
                    path, method, status_group = field.split("|", 2)
                    # Try JSON first; fall back to legacy pipe-delimited format.
                    try:
                        v       = json.loads(value)
                        count   = int(v["c"])
                        total   = float(v["t"])
                        max_lat = float(v["m"])
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        count_s, total_s, max_s = value.split("|", 2)
                        count   = int(count_s)
                        total   = float(total_s)
                        max_lat = float(max_s)
                    results.append({
                        "bucket_ts":      bucket_ts,
                        "service":        service,
                        "path":           path,
                        "method":         method,
                        "status_group":   status_group,
                        "count":          count,
                        "avg_latency_ms": round(total / count, 3) if count else 0.0,
                        "max_latency_ms": round(max_lat, 3),
                    })
                except Exception:
                    continue
    except Exception as exc:
        logger.warning("fastapi_alertengine.read_aggregates failed: %s", exc)
    return results


def read_metrics(
    rdb,
    config: AlertConfig,
    last_n: int,
) -> List[RequestMetricEvent]:
    """
    Read the most recent *last_n* events from the stream.

    Returns an empty list on any Redis or parse error.
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
                latency_ms  = float(fields.get("latency_ms", 0.0)),
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
    Read *last_n* events and return p95 latency broken down by traffic type.

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
    all_ms     = [e.latency_ms for e in events]

    return {
        "webhook_latency": _bucket(webhook_ms),
        "api_latency":     _bucket(api_ms),
        "overall_latency": _bucket(all_ms),
    }


# ── Internals ─────────────────────────────────────────────────────────────

def _p95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s   = sorted(values)
    idx = max(0, int(len(s) * 0.95) - 1)
    return round(s[idx], 3)


def _bucket(values: List[float]) -> dict:
    return {"p95_ms": _p95(values), "count": len(values)}
