# ⚡ fastapi-alertengine

**Production-ready FastAPI monitoring in under 60 seconds.**

No Prometheus.
No Grafana.
No dashboards.

Just install → `instrument(app)` → get alerts.

---

🔥 **51/51 tests passing**
🏦 **Derived from financial-grade infrastructure (AnchorFlow)**
🤖 **AI-agent friendly (works with Claude / Copilot / Cursor)**

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
- **Registers a `/health/alerts` endpoint** — returns the current alert status (configurable with `health_path`).

### 3. Check your alert status

```bash
curl http://localhost:8000/health/alerts
```

```json
{
  "status": "ok",
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
    "error_rate_critical": 0.2
  },
  "timestamp": 1712954670
}
```

---

## ⚡ What You Get Instantly

* P95 latency (overall + per request type: `api` / `webhook`)
* Error rate detection
* Anomaly scoring vs baseline
* Single health status: `ok | warning | critical`

No setup. No config files. No dashboards.

---

## 📊 Example Output

```json
{
  "status": "critical",
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

### 4. Analysis

The engine computes on demand:

* P95 latency (not averages)
* Error rate
* Anomaly score vs baseline

### 5. Alerting

Returns a single signal:

```
ok → warning → critical
```

---

## ✅ Verified Reliability

* ✔️ 51/51 tests passing (no live Redis required)
* ✔️ Works even if Redis fails (no crashes)
* ✔️ Safe in production request paths (non-blocking enqueue)
* ✔️ Accurate P95 + error rate calculations
* ✔️ Always uses `decode_responses=True` — warns if you pass a client that doesn't

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

### `AlertConfig`

```python
from fastapi_alertengine import AlertConfig

config = AlertConfig(
    redis_url="redis://localhost:6379/0",
    stream_key="anchorflow:request_metrics",
    stream_maxlen=5000,
)
# Or via environment variables:
#   ALERTENGINE_REDIS_URL=redis://...
#   ALERTENGINE_STREAM_KEY=my:stream
#   ALERTENGINE_STREAM_MAXLEN=10000
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
config = AlertConfig(redis_url="redis://localhost:6379/0")
redis_client = redis.Redis.from_url(config.redis_url, decode_responses=True)
engine = get_alert_engine(config=config, redis_client=redis_client)

app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

@app.on_event("startup")
async def start_drain():
    asyncio.create_task(engine.drain())

@app.get("/health/alerts")
def alerts_health():
    return engine.evaluate(window_size=200)
```

---

## �� Redis Stream Format

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

| Metric              | Threshold |
| ------------------- | --------- |
| P95 Warning         | 1000 ms   |
| P95 Critical        | 3000 ms   |
| Error Rate Warning  | 10%       |
| Error Rate Critical | 20%       |
| Queue Max Size      | 10 000    |
| Stream Max Length   | 5 000     |

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
* Slack / PagerDuty integrations
* Multi-service correlation

---

## 📬 Support & Contact

Have questions or want help getting production-ready fast?

📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)

We support early adopters and teams running critical systems.

---

## 🛡️ License

MIT License
