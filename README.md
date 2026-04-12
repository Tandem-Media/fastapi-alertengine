# ⚡ fastapi-alertengine

**Production-ready FastAPI monitoring in under 60 seconds.**

No Prometheus.
No Grafana.

Just install → `instrument(app)` → get alerts.

---

🔥 **150/150 tests passing**
🏦 **Derived from financial-grade infrastructure (AnchorFlow)**
🤖 **AI-agent friendly (works with Claude / Copilot / Cursor)**
📦 **v1.3.0**

---

## 🚀 Quick Start (one line)

### 1. Install

```bash
pip install fastapi-alertengine
```

### 2. Instrument your app

```python
from fastapi import FastAPI
from fastapi_alertengine import instrument

app = FastAPI()

# Option 1 – configure via env var (recommended for production):
#   export ALERTENGINE_REDIS_URL=redis://localhost:6379/0
instrument(app)

# Option 2 – pass the Redis URL directly:
# instrument(app, redis_url="redis://localhost:6379/0")
```

That's it. `instrument()` automatically:

- **Adds the request metrics middleware** — captures latency, status code, and path for every request.
- **Starts a background drain task** on app startup — asynchronously flushes metrics to Redis Streams.
- **Starts the alert delivery loop** — Slack notifications posted in the background, with rate limiting.
- **Registers four observability endpoints** (see [Auto-registered Endpoints](#-auto-registered-endpoints) below).

### 3. Check your alert status

```bash
curl http://localhost:8000/health/alerts
```

```json
{
  "status": "ok",
  "service_name": "my-api",
  "instance_id": "default",
  "metrics": {
    "overall_p95_ms": 45.2,
    "webhook_p95_ms": 0.0,
    "api_p95_ms": 45.2,
    "error_rate": 0.0,
    "anomaly_score": 0.12,
    "sample_size": 128
  },
  "thresholds": {
    "p95_warning_ms": 1000,
    "p95_critical_ms": 3000,
    "error_rate_warning": 0.1,
    "error_rate_critical": 0.2,
    "anomaly_warning": 1.0,
    "anomaly_critical": 2.0
  },
  "timestamp": 1712954670
}
```

---

## 📡 Auto-registered Endpoints

`instrument()` registers four endpoints automatically:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health/alerts` (configurable) | `evaluate()` result — current status |
| `POST` | `/alerts/evaluate` | Evaluate + enqueue for Slack delivery |
| `GET` | `/metrics/history` | Aggregated per-minute metrics (filter by `?service=`) |
| `GET` | `/metrics/ingestion` | Ingestion counters: enqueued / dropped |

---

## ⚡ What You Get Instantly

* P95 latency (overall + per request type: `api` / `webhook`)
* Error rate detection
* Anomaly scoring vs baseline
* Per-minute aggregated metrics stored in Redis
* Slack alerts with configurable rate limiting
* Single health status: `ok | warning | critical`
* Streamlit observability dashboard (zero extra infra)

---

## 📊 Example Output

```json
{
  "status": "critical",
  "service_name": "my-api",
  "instance_id": "worker-1",
  "metrics": {
    "overall_p95_ms": 854.2,
    "webhook_p95_ms": 910.4,
    "api_p95_ms": 720.1,
    "error_rate": 0.19,
    "anomaly_score": 1.4,
    "sample_size": 187
  }
}
```

---

## 🧩 How It Works

### 1. Sensing

Middleware captures for every HTTP request:

* latency (ms)
* status code
* request path & method
* request type (`api` / `webhook`)

### 2. Queueing (backpressure-safe)

Events are enqueued in a bounded in-memory queue (max 10 000 entries). The oldest entry is dropped when the queue is full, so memory stays bounded under load.

### 3. Streaming

A background `drain()` task flushes the queue to Redis Streams:

```
anchorflow:request_metrics
```

`drain()` is self-healing — it recovers from transient Redis errors and only stops on `asyncio.CancelledError` (clean shutdown).

### 4. Aggregation

Every 60 seconds (configurable), completed per-minute buckets are written to Redis hashes indexed by `(service, bucket_ts, path, method, status_group)`. Up to 50 000 distinct keys are held in memory at once; additional keys are dropped and counted.

### 5. Analysis

The engine computes on demand:

* P95 latency (not averages)
* Error rate
* Anomaly score vs baseline

### 6. Alerting

Returns a single signal:

```
ok → warning → critical
```

### 7. Slack Delivery

When `ALERTENGINE_SLACK_WEBHOOK_URL` is set, `POST /alerts/evaluate` posts a formatted alert to Slack. A configurable rate limit (default: 10 s) prevents notification floods. The delivery loop runs as a background task — your request path is never blocked.

---

## ✅ Verified Reliability

* ✔️ 150/150 tests passing (no live Redis required)
* ✔️ Works even if Redis fails (no crashes)
* ✔️ Safe in production request paths (non-blocking enqueue)
* ✔️ Accurate P95 + error rate calculations
* ✔️ Always uses `decode_responses=True` — warns if you pass a client that doesn't
* ✔️ Slack delivery rate-limited and non-blocking
* ✔️ Aggregation buffer capped at 50 000 keys (memory-safe)

**Production readiness: 9/10**

---

## 🧰 Public API

```python
from fastapi_alertengine import (
    instrument,           # one-line setup (recommended)
    AlertEngine,
    RequestMetricsMiddleware,
    get_alert_engine,
    AlertConfig,
    aggregate,
    write_batch,
)
```

### `instrument(app, ...)` — recommended

```python
engine = instrument(
    app,
    redis_url="redis://localhost:6379/0",  # or set ALERTENGINE_REDIS_URL
    health_path="/health/alerts",          # default
)
```

### `AlertEngine.evaluate()`

```python
result = engine.evaluate(window_size=200)
print(result["status"])  # "ok" | "warning" | "critical"
```

### `AlertEngine.aggregated_history()`

```python
buckets = engine.aggregated_history(service="my-api", last_n_buckets=10)
```

### `AlertEngine.get_ingestion_stats()`

```python
stats = engine.get_ingestion_stats()
# {"enqueued": 4200, "dropped": 0, "last_drain_at": 1712954670.1,
#  "dropped_agg_keys": 0, "dropped_alerts": 0}
```

### `AlertConfig`

```python
from fastapi_alertengine import AlertConfig

config = AlertConfig(
    redis_url="redis://localhost:6379/0",
    stream_key="anchorflow:request_metrics",
    stream_maxlen=5000,
    service_name="my-api",
    instance_id="worker-1",
    slack_webhook_url="https://hooks.slack.com/services/...",
    slack_rate_limit_seconds=10,
    agg_bucket_seconds=60,
    agg_ttl_seconds=3600,
)
# Or via environment variables:
#   ALERTENGINE_REDIS_URL=redis://...
#   ALERTENGINE_STREAM_KEY=my:stream
#   ALERTENGINE_STREAM_MAXLEN=10000
#   ALERTENGINE_SERVICE_NAME=my-api
#   ALERTENGINE_INSTANCE_ID=worker-1
#   ALERTENGINE_SLACK_WEBHOOK_URL=https://hooks.slack.com/...
#   ALERTENGINE_SLACK_RATE_LIMIT_SECONDS=10
```

---

## 🔧 Advanced (optional manual wiring)

If you need full control over each component:

```python
import redis
import asyncio
from fastapi import FastAPI
from fastapi_alertengine import AlertConfig, AlertEngine, RequestMetricsMiddleware, get_alert_engine

app = FastAPI()
config = AlertConfig(redis_url="redis://localhost:6379/0", service_name="my-api")
redis_client = redis.Redis.from_url(config.redis_url, decode_responses=True)
engine = get_alert_engine(config=config, redis_client=redis_client)

app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

@app.on_event("startup")
async def start_drain():
    asyncio.create_task(engine.drain())
    asyncio.create_task(engine.alert_delivery_loop())

@app.on_event("shutdown")
async def on_shutdown():
    await engine.flush_all_aggregates()

@app.get("/health/alerts")
def alerts_health():
    return engine.evaluate(window_size=200)

@app.post("/alerts/evaluate")
def alerts_evaluate():
    result = engine.evaluate()
    engine.enqueue_alert(result)
    return result

@app.get("/metrics/history")
def metrics_history(service: str = None, last_n_buckets: int = 10):
    return {"metrics": engine.aggregated_history(service=service, last_n_buckets=last_n_buckets)}

@app.get("/metrics/ingestion")
def metrics_ingestion():
    return engine.get_ingestion_stats()
```

---

## 📡 Redis Stream Format

Metrics are written with these fields:

| Field | Type | Description |
|---|---|---|
| `path` | string | Request path |
| `method` | string | HTTP method (uppercase) |
| `status` | string | HTTP status code |
| `latency_ms` | string | Response time in ms (3 decimal places) |
| `type` | string | `"api"` or `"webhook"` |

---

---

## ⚙️ Defaults

| Metric                    | Threshold / Default |
| ------------------------- | ------------------- |
| P95 Warning               | 1000 ms             |
| P95 Critical              | 3000 ms             |
| Anomaly Warning           | 1.0                 |
| Anomaly Critical          | 2.0                 |
| Error Rate Warning        | 10%                 |
| Error Rate Critical       | 20%                 |
| Queue Max Size            | 10 000              |
| Stream Max Length         | 5 000               |
| Agg Bucket Size           | 60 s                |
| Agg TTL (Redis)           | 3 600 s (1 h)       |
| Max Agg Keys (in-memory)  | 50 000              |
| Slack Rate Limit          | 10 s                |

## 📊 Observability Dashboard

A Streamlit dashboard is included in `dashboard/app.py`:

```bash
pip install -r dashboard/requirements.txt
ALERTENGINE_BASE_URL=http://localhost:8000 streamlit run dashboard/app.py
```

Features:
* **Health strip** — 5 metric cards (status · P95 · error rate · RPM · health score), color-coded against thresholds
* **Time-series charts** — requests/min, error rate %, avg+max latency with warning/critical reference lines
* **Endpoint table** — sorted by `impact_score = request_count × avg_latency`
* **Alerts panel** — severity card from `/health/alerts` with expandable threshold reference
* **Ingestion debug** — queue/agg/alert drop counters from `/metrics/ingestion`

---

## 🏦 Why This Exists

Most FastAPI apps either:

* Run blind, or
* Require heavy monitoring stacks (Prometheus + Grafana)

This gives you **80% of the value in 1% of the setup time.**

---

## 🤖 Built for AI-Assisted Development

* Clean API surface (`__all__`)
* Minimal integration steps
* Predictable outputs

Works seamlessly with:

* Claude Code
* GitHub Copilot
* Cursor

---

## 🚀 What's Coming

* Remote alert engine (SaaS mode)
* PagerDuty integrations
* Multi-service correlation

---

## 📬 Support & Contact

Have questions or want help getting production-ready fast?

📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)

We support early adopters and teams running critical systems.

---

## 🛡️ License

MIT License
