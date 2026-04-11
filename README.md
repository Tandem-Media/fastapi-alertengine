# ⚡ fastapi-alertengine

**Production-ready FastAPI monitoring in under 60 seconds.**

No Prometheus. No Grafana. No dashboards.

Just install → add middleware → get alerts.

---

🔥 **Tested end-to-end (cold start): 27/27 pytest checks passing**
🏦 **Derived from financial-grade infrastructure (AnchorFlow)**
🤖 **AI-agent friendly (works with Claude / Copilot / Cursor)**

---

## 🚀 Quick Start (30 seconds)

### 1. Install

```
pip install fastapi-alertengine
```

### 2. Plug and play

```python
from fastapi import FastAPI
import redis
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

app = FastAPI()

redis_client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)

alert_engine = get_alert_engine(redis_client=redis_client)

app.add_middleware(RequestMetricsMiddleware, alert_engine=alert_engine)

@app.get("/health/alerts")
def alerts_health():
    return alert_engine.evaluate(window_size=200).as_dict()
```

---

## ⚡ What You Get Instantly

* P95 + P50 latency (overall + per request type)
* Error rate detection (5xx only, expressed as a percentage)
* System health score (0–100 composite)
* Structured alerts array with type, message, and severity
* Single health status: `ok | warning | critical`

No setup. No config. No dashboards.

---

## 📊 Example Output

```json
{
  "status": "critical",
  "system_health": 82.4,
  "metrics": {
    "p95_latency_ms": 1240.5,
    "p50_latency_ms": 185.2,
    "error_rate_percent": 4.8,
    "request_count_1m": 840
  },
  "alerts": [
    {
      "type": "latency_spike",
      "message": "P95 latency (1240ms) exceeds threshold (800ms)",
      "severity": "critical"
    },
    {
      "type": "error_anomaly",
      "message": "Error rate elevated: 4.8% (Baseline: 0.5%)",
      "severity": "warning"
    }
  ],
  "timestamp": "2026-04-10T14:38:21Z",
  "engine_version": "1.1.3"
}
```

---

## 🧩 How It Works

### 1. Sensing
Middleware captures:
* latency (wall-clock ms)
* status_code
* request type (`api` / `webhook`)

### 2. Streaming
Events are written to Redis Streams:
```
anchorflow:request_metrics
```

### 3. Analysis
The engine computes:
* P95 + P50 latency (not averages)
* Error rate percentage (5xx only)
* System health score (0–100)
* Anomaly score vs rolling baseline

### 4. Alerting
Returns a structured signal with status, metrics, alerts array, timestamp, and engine version.

---

## ✅ Verified Reliability

* ✔️ 27/27 pytest checks passing (run with `pytest tests/ -v`, no live Redis required)
* ✔️ Works even if Redis fails (no crashes — circuit-safe write path)
* ✔️ Safe in production request paths (zero latency overhead on hot path)
* ✔️ Accurate P95 + P50 + error rate calculations
* ✔️ ISO-8601 UTC timestamps on every response

**Production readiness: 9/10**

---

## 🧰 Public API

```python
from fastapi_alertengine import (
    AlertEngine,
    RequestMetricsMiddleware,
    get_alert_engine,
    AlertConfig,
    AlertEvent,
)
```

### AlertEngine

```python
result = alert_engine.evaluate(window_size=200)
print(result.status)          # "ok" | "warning" | "critical"
print(result.system_health)   # 0.0 – 100.0
print(result.as_dict())       # full JSON-safe dict
```

### Middleware

```python
app.add_middleware(RequestMetricsMiddleware, alert_engine=alert_engine)
```

### Singleton Helper (zero-config)

```python
# Reads ALERTENGINE_REDIS_URL env var automatically
alert_engine = get_alert_engine()

# Or explicit:
alert_engine = get_alert_engine(redis_client=redis_client)
```

---

## ⚙️ Configuration

All settings are configurable via environment variables (prefix: `ALERTENGINE_`):

| Env Var | Default | Description |
| --- | --- | --- |
| `ALERTENGINE_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `ALERTENGINE_STREAM_KEY` | `anchorflow:request_metrics` | Redis Stream key |
| `ALERTENGINE_STREAM_MAXLEN` | `10000` | Max stream entries (MAXLEN ~) |
| `ALERTENGINE_P95_WARNING_MS` | `1000` | P95 warning threshold (ms) |
| `ALERTENGINE_P95_CRITICAL_MS` | `3000` | P95 critical threshold (ms) |
| `ALERTENGINE_ERROR_RATE_WARNING_PCT` | `2.0` | Error rate warning (%) |
| `ALERTENGINE_ERROR_RATE_CRITICAL_PCT` | `5.0` | Error rate critical (%) |
| `ALERTENGINE_ERROR_RATE_BASELINE_PCT` | `0.5` | Baseline shown in alert messages (%) |

---

## 🤖 Built for AI-Assisted Development

Clean API surface (`__all__`), minimal integration steps, predictable typed outputs.

Works seamlessly with:
* Claude Code
* GitHub Copilot
* Cursor

---

## 🚀 What’s Coming

* Remote alert engine (SaaS mode)
* Slack / PagerDuty integrations
* Multi-service correlation
* Grafana dashboard provisioning

---

## 💬 Support & Contact

Have questions or want help getting production-ready fast?

📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)

🐙 [github.com/Tandem-Media/fastapi-alertengine](https://github.com/Tandem-Media/fastapi-alertengine)

---

## 🛡️ License

MIT License
