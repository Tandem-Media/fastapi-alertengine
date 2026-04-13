# fastapi_alertengine/storage.py
"""
Redis Streams read/write for request metrics, plus aggregation helpers.
"""
import json as _json
import logging
from typing import List, Optional

from .config import AlertConfig
from .schemas import RequestMetricEvent

logger = logging.getLogger(__name__)


def _classify(path: str) -> str:
    return "webhook" if "webhook" in path.lower() else "api"


def _build_fields(config: AlertConfig, metric: dict) -> dict:
    return {
        "path":         metric["path"],
        "method":       str(metric["method"]).upper(),
        "status":       str(metric["status_code"]),
        "latency_ms":   f"{metric['latency_ms']:.3f}",
        "type":         _classify(metric["path"]),
        "service_name": metric.get("service_name", config.service_name),
        "instance_id":  metric.get("instance_id",  config.instance_id),
    }


def write_metric(rdb, config: AlertConfig, metric: dict) -> None:
    """Append one request event to the Redis Stream. Never raises."""
    try:
        rdb.xadd(config.stream_key, _build_fields(config, metric),
                 maxlen=config.stream_maxlen, approximate=True)
    except Exception as exc:
        logger.warning("fastapi_alertengine.write_metric failed: %s", exc)


def write_batch(rdb, config: AlertConfig, metrics: List[dict]) -> None:
    """Write a list of metric dicts via Redis pipeline. Never raises."""
    if not metrics:
        return
    try:
        pipe = rdb.pipeline(transaction=False)
        for metric in metrics:
            pipe.xadd(config.stream_key, _build_fields(config, metric),
                      maxlen=config.stream_maxlen, approximate=True)
        pipe.execute()
    except Exception as exc:
        logger.warning("fastapi_alertengine.write_batch failed: %s", exc)


def flush_aggregates(rdb, config: AlertConfig, snapshot: dict) -> None:
    """Write completed-bucket aggregates to Redis hashes. Never raises."""
    if not snapshot:
        return
    try:
        pipe = rdb.pipeline(transaction=False)
        seen: set = set()
        for (service, bucket_ts, path, method, sg), (count, total, max_lat) in snapshot.items():
            redis_key = f"{config.agg_key_prefix}:{service}:{bucket_ts}"
            field = f"{path}|{method}|{sg}"
            value = _json.dumps({"c": count, "t": round(total, 3), "m": round(max_lat, 3)})
            pipe.hset(redis_key, field, value)
            seen.add((service, bucket_ts))
        by_service: dict = {}
        for (service, bucket_ts) in seen:
            redis_key = f"{config.agg_key_prefix}:{service}:{bucket_ts}"
            pipe.expire(redis_key, config.agg_ttl_seconds)
            if service not in by_service:
                by_service[service] = {}
            by_service[service][str(bucket_ts)] = bucket_ts
        for service, mapping in by_service.items():
            index_key = f"{config.agg_key_prefix}:index:{service}"
            pipe.zadd(index_key, mapping)
            pipe.expire(index_key, config.agg_ttl_seconds)
        pipe.execute()
    except Exception as exc:
        logger.warning("fastapi_alertengine.flush_aggregates failed: %s", exc)


def read_aggregates(rdb, config: AlertConfig, service: str, last_n_buckets: int = 10) -> List[dict]:
    """Read aggregated metrics for *service* from Redis. Returns [] on error."""
    index_key = f"{config.agg_key_prefix}:index:{service}"
    results: List[dict] = []
    try:
        members = rdb.zrevrange(index_key, 0, last_n_buckets - 1)
        if not members:
            return []
        pipe = rdb.pipeline(transaction=False)
        for bts in members:
            pipe.hgetall(f"{config.agg_key_prefix}:{service}:{bts}")
        for bts, data in zip(members, pipe.execute()):
            try:
                bucket_ts = int(bts)
            except (ValueError, TypeError):
                bucket_ts = 0
            for field, value in (data or {}).items():
                try:
                    path, method, sg = field.split("|", 2)
                    try:
                        v = _json.loads(value)
                        count, total, max_lat = int(v["c"]), float(v["t"]), float(v["m"])
                    except Exception:
                        c, t, m = value.split("|", 2)
                        count, total, max_lat = int(c), float(t), float(m)
                    results.append({
                        "bucket_ts": bucket_ts, "service": service,
                        "path": path, "method": method, "status_group": sg,
                        "count": count,
                        "avg_latency_ms": round(total / count, 3) if count else 0.0,
                        "max_latency_ms": round(max_lat, 3),
                    })
                except Exception:
                    continue
    except Exception as exc:
        logger.warning("fastapi_alertengine.read_aggregates failed: %s", exc)
    return results


def read_metrics(rdb, config: AlertConfig, last_n: int) -> List[RequestMetricEvent]:
    """Read the most recent *last_n* events. Returns [] on any error."""
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


def aggregate(rdb, config: AlertConfig, last_n: int = 500) -> dict:
    events     = read_metrics(rdb, config, last_n)
    webhook_ms = [e.latency_ms for e in events if e.type == "webhook"]
    api_ms     = [e.latency_ms for e in events if e.type == "api"]
    all_ms     = [e.latency_ms for e in events]
    return {
        "webhook_latency": _bucket(webhook_ms),
        "api_latency":     _bucket(api_ms),
        "overall_latency": _bucket(all_ms),
    }


def _p95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s   = sorted(values)
    idx = max(0, int(len(s) * 0.95) - 1)
    return round(s[idx], 3)


def _bucket(values: List[float]) -> dict:
    return {"p95_ms": _p95(values), "count": len(values)}
