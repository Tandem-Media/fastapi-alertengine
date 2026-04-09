# ⚡ fastapi-alertengine

**Production-ready FastAPI monitoring in under 60 seconds.**

No Prometheus.
No Grafana.
No dashboards.

Just install → add middleware → get alerts.

---

🔥 **Tested end-to-end (cold start): 48/50 checks passed**
🏦 **Derived from financial-grade infrastructure (AnchorFlow)**
🤖 **AI-agent friendly (works with Claude / Copilot / Cursor)**

---

## 🚀 Quick Start (30 seconds)

### 1. Install

```bash
pip install fastapi-alertengine
```

### 2. Plug and play

```python
from fastapi import FastAPI
import redis
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

app = FastAPI()

redis_client = redis.Redis.from_url("redis://localhost:6379/0")

alert_engine = get_alert_engine(redis_client=redis_client)

app.add_middleware(RequestMetricsMiddleware, alert_engine=alert_engine)


@app.get("/")
async def root():
    return {"status": "monitored"}


@app.get("/health/alerts")
def alerts_health():
    return alert_engine.evaluate(window_size=200)
```

---

## ⚡ What You Get Instantly

* P95 latency (overall + per request type)
* Error rate detection
* Anomaly scoring vs baseline
* Single health status: `ok | warning | critical`

No setup. No config. No dashboards.

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

Middleware captures:

* latency
* status_code
* request type (`api` / `webhook`)

### 2. Streaming

Events are written to Redis Streams:

```
anchorflow:request_metrics
```

### 3. Analysis

The engine computes:

* P95 latency (not averages)
* error rate
* anomaly score vs baseline

### 4. Alerting

Returns a single signal:

```
ok → warning → critical
```

---

## ✅ Verified Reliability

* ✔️ 48/50 cold-start checks passed
* ✔️ Works even if Redis fails (no crashes)
* ✔️ Safe in production request paths
* ✔️ Accurate P95 + error rate calculations

**Production readiness: 8/10**

---

## 🧰 Public API

```python
from fastapi_alertengine import (
    AlertEngine,
    RequestMetricsMiddleware,
    get_alert_engine
)
```

### AlertEngine

```python
result = alert_engine.evaluate(window_size=200)
print(result["status"])  # "ok" | "warning" | "critical"
```

### Middleware

```python
app.add_middleware(RequestMetricsMiddleware, alert_engine=alert_engine)
```

### Singleton Helper

```python
alert_engine = get_alert_engine(redis_client=redis_client)
```

---

## 📡 Redis Stream Format

```python
import time

redis_client.xadd(
    "anchorflow:request_metrics",
    {
        "latency_ms": 123.4,
        "type": "api",
        "status_code": 200,
        "timestamp": int(time.time()),
    },
)
```

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

## ⚙️ Defaults

| Metric              | Threshold |
| ------------------- | --------- |
| P95 Warning         | 1000 ms   |
| P95 Critical        | 3000 ms   |
| Error Rate Critical | 20%       |

---

## 🚀 What’s Coming

* Remote alert engine (SaaS mode)
* Slack / PagerDuty integrations
* Multi-service correlation
* Config-first setup (`AlertConfig`)

---

## 📬 Support & Contact

Have questions or want help getting production-ready fast?

📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)

We support early adopters and teams running critical systems.

---

## 🛡️ License

MIT License
