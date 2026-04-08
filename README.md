# fastapi-alertengine

> Production-grade request metrics middleware and SLO alert engine for FastAPI.  
> Backed by **Redis Streams**. Zero external dependencies beyond FastAPI and redis-py.

```
pip install fastapi-alertengine
```

---

## What it does

| Component | Purpose |
|---|---|
| `RequestMetricsMiddleware` | Measures latency per request, classifies as `webhook` or `api`, writes to Redis Stream |
| `AlertEngine` | Reads stream, computes p95/anomaly/error-rate, returns `ok` / `warning` / `critical` |
| `AlertDeduplicator` | Redis TTL key prevents the same alert from spamming every evaluation cycle |
| `get_alert_engine()` | Process-level singleton — one Redis connection, called from anywhere |
| `aggregate()` | p95 breakdown by traffic type — suitable for dashboard endpoints |

---

## Quick start

```python
from fastapi import FastAPI
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine
import redis

app = FastAPI()
rdb = redis.Redis.from_url("redis://localhost:6379", decode_responses=True)

# 1. Register middleware (must be before CORSMiddleware)
app.add_middleware(RequestMetricsMiddleware, redis=rdb)

# 2. Expose an alert-status endpoint
@app.get("/health/alerts")
def alert_status():
    event = get_alert_engine(redis_client=rdb).evaluate()
    return {"status": event.status, "p95_ms": event.metrics.overall_p95_ms}
```

---

## Configuration

All thresholds live in `AlertConfig`. Override only what you need:

```python
from fastapi_alertengine import AlertConfig, RequestMetricsMiddleware

config = AlertConfig(
    p95_warning_ms  = 500,    # default 1000
    p95_critical_ms = 2000,   # default 3000
    cooldown_seconds = 120,   # dedup window, default 300
    stream_key      = "myapp:metrics",
    stream_maxlen   = 50_000,
)

app.add_middleware(RequestMetricsMiddleware, redis=rdb, config=config)
```

| Field | Default | Description |
|---|---|---|
| `stream_key` | `"anchorflow:request_metrics"` | Redis Stream key |
| `stream_maxlen` | `10_000` | Approximate cap (Redis `MAXLEN ~`) |
| `p95_warning_ms` | `1_000` | p95 latency warning threshold (ms) |
| `p95_critical_ms` | `3_000` | p95 latency critical threshold (ms) |
| `anomaly_warning` | `1.0` | Anomaly ratio (vs rolling mean) warning |
| `anomaly_critical` | `2.0` | Anomaly ratio critical |
| `error_rate_warning` | `0.10` | Fraction of 5xx responses — warning |
| `error_rate_critical` | `0.20` | Fraction of 5xx responses — critical |
| `window_size` | `200` | Events read per `evaluate()` call |
| `cooldown_seconds` | `300` | Min seconds between identical alerts |

---

## AlertEngine

```python
from fastapi_alertengine import AlertEngine

engine = AlertEngine(redis=rdb)
event  = engine.evaluate()

print(event.status)               # "ok" | "warning" | "critical"
print(event.metrics.overall_p95_ms)
print(event.metrics.error_rate)
print(event.reason)               # human-readable trigger reason
```

### AlertEvent fields

| Field | Type | Description |
|---|---|---|
| `status` | `str` | `"ok"` / `"warning"` / `"critical"` |
| `reason` | `str \| None` | Which threshold was breached |
| `metrics.overall_p95_ms` | `float` | p95 across all traffic |
| `metrics.webhook_p95_ms` | `float` | p95 for webhook traffic |
| `metrics.api_p95_ms` | `float` | p95 for API traffic |
| `metrics.error_rate` | `float` | Fraction of 5xx responses |
| `metrics.anomaly_score` | `float` | `|p95 - mean| / mean` |
| `metrics.sample_size` | `int` | Events evaluated |
| `timestamp` | `int` | Unix seconds |

---

## AlertDeduplicator

Prevents notification spam across evaluation cycles:

```python
from fastapi_alertengine import AlertEngine, AlertDeduplicator

engine = AlertEngine(redis=rdb)
dedup  = AlertDeduplicator(redis=rdb)

event = engine.evaluate()
if event.status != "ok" and dedup.should_fire(event.status):
    send_slack_alert(event)   # fires at most once per cooldown_seconds
```

---

## Aggregation helper

```python
from fastapi_alertengine import aggregate, AlertConfig

result = aggregate(rdb, AlertConfig(), last_n=500)
# {
#   "webhook_latency": {"p95_ms": 84.2, "count": 112},
#   "api_latency":     {"p95_ms": 23.7, "count": 388},
#   "overall_latency": {"p95_ms": 31.1, "count": 500},
# }
```

---

## Running the demo

```bash
git clone https://github.com/Tandem-Media/Tandem_Hive_V1_Final.git
cd Tandem_Hive_V1_Final
pip install "fastapi[standard]" redis
redis-server &
uvicorn examples.main:app --reload

# Hit the demo endpoints
curl http://localhost:8000/fast
curl http://localhost:8000/slow   # ~600ms
curl http://localhost:8000/error  # HTTP 500

# Check alert status
curl http://localhost:8000/alerts
```

---

## Redis Stream schema

Each event written to `anchorflow:request_metrics`:

| Field | Example |
|---|---|
| `path` | `/whatsapp-bot/webhook` |
| `method` | `POST` |
| `status` | `200` |
| `latency_ms` | `84.312` |
| `type` | `webhook` or `api` |

---

## Design principles

- **Non-blocking** — `_write()` is synchronous but Redis XADD is ~0.1ms; async overhead would cost more
- **Fail-silent** — every Redis call is wrapped in try/except; metrics never break requests
- **Approximate trimming** — `MAXLEN ~` avoids O(n) compaction on every write
- **Single connection** — `get_alert_engine()` caches the engine; reuse your existing Redis client

---

## License

MIT — see [LICENSE](LICENSE).
