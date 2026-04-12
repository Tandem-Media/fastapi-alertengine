# ⚡ fastapi-alertengine

**Production-ready observability for FastAPI — in one line.**

Turn any FastAPI app into a real-time observability system with metrics, aggregation, alerts, and a live dashboard.

---

🔥 **150/150 tests passing**
🏦 **Derived from financial-grade infrastructure (AnchorFlow)**
🤖 **AI-agent friendly (works with Claude / Copilot / Cursor)**
📦 **v1.3.0**

---

## 🚀 One-line install

```bash
pip install fastapi-alertengine
```

## ⚡ One-line integration

```python
from fastapi import FastAPI
from fastapi_alertengine import instrument

app = FastAPI()
instrument(app)
```

That's it. No setup. No configuration required.

---

## 🧠 What happens automatically

When you call `instrument(app)`, fastapi-alertengine silently boots a full observability runtime:

### 🟢 Ingestion layer
- Captures every request via middleware
- Async, non-blocking queue buffering
- Backpressure-safe ingestion with drop tracking

### 🔵 Processing layer
- Batched Redis writes (or in-memory fallback mode)
- Failure-safe pipeline execution

### 🟣 Aggregation engine
- Real-time per-minute bucket aggregation
- Metrics grouped by: service, endpoint, method, status group
- Precomputed time-series for instant queries

### 🔴 Alerting system
- Automatic anomaly detection (latency + error rate)
- Async alert queue with background worker
- Slack/webhook delivery (optional)
- Rate-limited, failure-safe execution

### ⚫ Observability API (auto-registered)
- `/health/alerts` → system health + anomalies
- `/metrics/history` → aggregated time-series data
- `/metrics/ingestion` → queue + pipeline health
- `/alerts/evaluate` → trigger alert evaluation

---

## 📊 What you get instantly

No dashboards to configure. No metrics pipeline to build.

You immediately get:

- P95 + P50 latency tracking
- Error rate detection
- Anomaly scoring vs baseline
- Requests per minute
- Endpoint-level performance breakdown
- Live alert stream (Slack optional)
- Ingestion health visibility (drops, queue size, throughput)

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

## 🧩 Built-in dashboard (optional)

A Streamlit-based observability console is included:

```bash
pip install -r dashboard/requirements.txt
ALERTENGINE_BASE_URL=http://localhost:8000 streamlit run dashboard/app.py
```

It provides:

- **Health strip** — 5 metric cards (status · P95 · error rate · RPM · health score), color-coded against thresholds
- **Time-series charts** — requests/min, error rate %, avg+max latency with warning/critical reference lines
- **Endpoint table** — sorted by `impact_score = request_count × avg_latency`
- **Alerts panel** — severity card from `/health/alerts` with expandable threshold reference
- **Ingestion diagnostics** — queue/agg/alert drop counters from `/metrics/ingestion`

---

## ⚙️ Zero-config design

fastapi-alertengine automatically adapts to your environment:

| Environment | Behavior |
|---|---|
| Redis available | Full distributed observability mode |
| Redis missing | In-memory safe mode |
| Slack webhook set | Alert delivery enabled |
| Slack missing | Alerts disabled (no failure) |

---

## 🧠 Why it's fast

Unlike traditional observability systems:

- No log scanning
- No query-time aggregation
- No external dashboards required for core functionality

Instead, metrics are pre-aggregated in real-time as requests happen. This means:

- Constant-time queries
- Stable performance under load
- Predictable memory usage

---

## 📡 Auto-registered endpoints

`instrument()` registers four endpoints automatically:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health/alerts` (configurable) | `evaluate()` result — current status |
| `POST` | `/alerts/evaluate` | Evaluate + enqueue for Slack delivery |
| `GET` | `/metrics/history` | Aggregated per-minute metrics (filter by `?service=`) |
| `GET` | `/metrics/ingestion` | Ingestion counters: enqueued / dropped |

---

## 🔥 Example response

```json
{
  "status": "warning",
  "service_name": "payments-api",
  "instance_id": "worker-1",
  "metrics": {
    "overall_p95_ms": 812.4,
    "webhook_p95_ms": 910.4,
    "api_p95_ms": 720.1,
    "error_rate": 0.19,
    "anomaly_score": 1.4,
    "sample_size": 187
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

## 🧠 Design philosophy

fastapi-alertengine is built on a simple principle: **observability should be automatic, not configured.**

So instead of:
- setting up Prometheus
- configuring Grafana
- wiring exporters
- defining alert rules manually

You do:

```python
instrument(app)
```

---

## 🚀 Production-ready features

- Async ingestion pipeline
- Redis-backed aggregation engine
- Safe fallback in-memory mode
- Batch processing for high throughput
- Alert worker isolation
- Service-aware observability
- Drop tracking + ingestion metrics
- Zero-crash design (fail-safe everywhere)

## 🛡 Reliability guarantees

- Never blocks request path
- Never crashes due to Redis failure
- Graceful degradation in all external dependencies
- Backpressure-aware ingestion queue
- Safe background worker recovery

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

## ⚙️ Defaults

| Metric | Threshold / Default |
|---|---|
| P95 Warning | 1000 ms |
| P95 Critical | 3000 ms |
| Anomaly Warning | 1.0 |
| Anomaly Critical | 2.0 |
| Error Rate Warning | 10% |
| Error Rate Critical | 20% |
| Queue Max Size | 10 000 |
| Stream Max Length | 5 000 |
| Agg Bucket Size | 60 s |
| Agg TTL (Redis) | 3 600 s (1 h) |
| Max Agg Keys (in-memory) | 50 000 |
| Slack Rate Limit | 10 s |

---

## ✅ Verified Reliability

- ✔️ 150/150 tests passing (no live Redis required)
- ✔️ Works even if Redis fails (no crashes)
- ✔️ Safe in production request paths (non-blocking enqueue)
- ✔️ Accurate P95 + error rate calculations
- ✔️ Always uses `decode_responses=True` — warns if you pass a client that doesn't
- ✔️ Slack delivery rate-limited and non-blocking
- ✔️ Aggregation buffer capped at 50 000 keys (memory-safe)

---

## 📦 What's coming

- Multi-service correlation views
- Distributed tracing layer
- Advanced anomaly detection
- Hosted observability SaaS mode
- Native Grafana/Prometheus bridge

---

## 🤖 Built for AI-Assisted Development

Clean API surface (`__all__`), minimal integration steps, and predictable outputs. Works seamlessly with Claude Code, GitHub Copilot, and Cursor.

---

## 📬 Support & Contact

Have questions or want help getting production-ready fast?

📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)

We support early adopters and teams running critical systems.

---

## 🛡️ License

MIT License
