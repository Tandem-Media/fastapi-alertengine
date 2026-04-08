# fastapi-alertengine — Claude Code Skill

## Overview

`fastapi-alertengine` is a drop-in observability package for FastAPI services.  
It requires only `starlette` and `redis` — no Prometheus, no OpenTelemetry, no agents.

---

## Installation

```bash
pip install fastapi-alertengine
```

Or from source:

```bash
git clone https://github.com/Tandem-Media/Tandem_Hive_V1_Final.git
pip install -e ".[fastapi,dev]"
```

---

## Minimal integration (3 lines)

```python
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine
import redis

rdb = redis.Redis.from_url("redis://localhost:6379", decode_responses=True)
app.add_middleware(RequestMetricsMiddleware, redis=rdb)  # line 1
engine = get_alert_engine(redis_client=rdb)              # line 2
event  = engine.evaluate()                               # line 3
```

---

## Key classes

### `RequestMetricsMiddleware`

```python
app.add_middleware(
    RequestMetricsMiddleware,
    redis=rdb,
    config=AlertConfig(p95_warning_ms=500),
)
```

- Wraps every request via `BaseHTTPMiddleware`
- Measures latency with `time.perf_counter()`
- Classifies path: `"webhook"` if `"webhook"` in path, else `"api"`
- Writes to Redis Stream `config.stream_key` with approximate MAXLEN

### `AlertEngine`

```python
engine = AlertEngine(redis=rdb, config=AlertConfig())
event  = engine.evaluate()
# event.status: "ok" | "warning" | "critical"
# event.metrics.overall_p95_ms
# event.metrics.error_rate
```

Decision tree:
1. `p95 > p95_critical_ms` → `critical`
2. `p95 > p95_warning_ms` → `warning`
3. `anomaly_score > anomaly_critical` → `critical`
4. `error_rate > error_rate_critical` → `critical`
5. Otherwise `ok`

### `AlertDeduplicator`

```python
dedup = AlertDeduplicator(redis=rdb, config=AlertConfig(cooldown_seconds=60))
if event.status != "ok" and dedup.should_fire(event.status):
    # send notification
```

- Sets `alert:dedup:{alert_type}` with NX + TTL
- Fails open if Redis is down

### `get_alert_engine()`

```python
engine = get_alert_engine(
    redis_url   = "redis://localhost:6379",  # OR
    redis_client= existing_rdb,
    config      = AlertConfig(),
)
```

- Process-level singleton — safe to call from multiple threads/routes
- Caches by `redis_url` or `id(redis_client)`

---

## AlertConfig reference

```python
AlertConfig(
    stream_key          = "anchorflow:request_metrics",
    stream_maxlen       = 10_000,
    p95_warning_ms      = 1_000.0,
    p95_critical_ms     = 3_000.0,
    anomaly_warning     = 1.0,
    anomaly_critical    = 2.0,
    error_rate_warning  = 0.10,
    error_rate_critical = 0.20,
    window_size         = 200,
    cooldown_seconds    = 300,
)
```

---

## Testing without Redis

```python
from fastapi_alertengine import RequestMetricsMiddleware
from fastapi.testclient import TestClient

# Pass redis=None to disable stream writes
app.add_middleware(RequestMetricsMiddleware, redis=None)
client = TestClient(app)
```

Or use `fakeredis`:

```python
import fakeredis
from fastapi_alertengine import AlertEngine, AlertConfig

rdb    = fakeredis.FakeRedis(decode_responses=True)
engine = AlertEngine(redis=rdb, config=AlertConfig())
event  = engine.evaluate()
assert event.status == "ok"
assert event.reason == "no_data"
```

---

## Running the demo

```bash
uvicorn examples.main:app --reload
# GET /fast   → normal traffic
# GET /slow   → ~600ms, triggers warning
# GET /error  → HTTP 500, drives error rate
# GET /alerts → current alert status + aggregated p95
```
