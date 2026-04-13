# ⚡ fastapi-alertengine

**Production-ready FastAPI monitoring in one line.**

No Prometheus. No Grafana. No dashboards. No Redis required to get started.

Just install → instrument → done.

---

🔥 **164/164 tests passing**
🏦 **Derived from financial-grade infrastructure (AnchorFlow / Tofamba)**
🤖 **AI-agent friendly (works with Claude / Copilot / Cursor)**
⚡ **Memory mode — runs without Redis at all**

---

## 🚀 Quickstart (one line)

```bash
pip install fastapi-alertengine
```

```python
from fastapi import FastAPI
from fastapi_alertengine import instrument

app = FastAPI()
instrument(app)   # set ALERTENGINE_REDIS_URL or run without Redis in memory mode
```

That’s it. Four endpoints are now live on your app:

| Endpoint | Description |
|----------|-------------|
| `GET /health/alerts` | Current SLO status — ok / warning / critical |
| `POST /alerts/evaluate` | Evaluate + enqueue for Slack delivery |
| `GET /metrics/history` | Aggregated per-minute metrics from Redis |
| `GET /metrics/ingestion` | Ingestion counters (enqueued / dropped) |

---

## ⚡ How it works

```
Request → middleware (enqueue only, ~0μs) → response returned immediately
              ↓
        deque (in-memory)
              ↓  every 50ms
        drain() → Redis Stream (batched pipeline)
              ↓
        evaluate() → GET /health/alerts
```

---

## 📊 Example output

```json
{
  "status": "warning",
  "service_name": "payments-api",
  "instance_id": "pod-3",
  "metrics": {
    "overall_p95_ms": 843.2,
    "webhook_p95_ms": 910.4,
    "api_p95_ms":     720.1,
    "error_rate":     0.012,
    "anomaly_score":  0.84,
    "sample_size":    187
  },
  "alerts": [
    {
      "type": "latency_spike",
      "severity": "warning",
      "message": "P95 latency (843ms) exceeds threshold (1000ms)"
    }
  ],
  "timestamp": 1712756301
}
```

---

## ⚙️ Configuration

All settings via environment variables (prefix: `ALERTENGINE_`):

| Env Var | Default | Description |
|---------|---------|-------------|
| `ALERTENGINE_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `ALERTENGINE_SERVICE_NAME` | `default` | Service name in every metric |
| `ALERTENGINE_INSTANCE_ID` | `default` | Instance ID in every metric |
| `ALERTENGINE_P95_WARNING_MS` | `1000` | P95 warning threshold (ms) |
| `ALERTENGINE_P95_CRITICAL_MS` | `3000` | P95 critical threshold (ms) |
| `ALERTENGINE_ERROR_RATE_WARNING_PCT` | `2.0` | Error rate warning (%) |
| `ALERTENGINE_ERROR_RATE_CRITICAL_PCT` | `5.0` | Error rate critical (%) |
| `ALERTENGINE_SLACK_WEBHOOK_URL` | `None` | Slack webhook for alert delivery |
| `ALERTENGINE_SLACK_RATE_LIMIT_SECONDS` | `10` | Minimum seconds between Slack messages |
| `ALERTENGINE_STREAM_MAXLEN` | `10000` | Max Redis Stream entries |

---

## 🧩 Manual wiring (full control)

```python
from fastapi import FastAPI
from fastapi_alertengine import AlertEngine, AlertConfig, RequestMetricsMiddleware

config = AlertConfig(service_name="payments-api", p95_critical_ms=500)
engine = AlertEngine(config)
engine.start(app)   # wires middleware, drain task, and all endpoints
```

Or wire each piece yourself:

```python
import asyncio
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

engine = get_alert_engine(redis_url="redis://localhost:6379/0")
app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

@app.on_event("startup")
async def start_drain():
    asyncio.create_task(engine.drain())
    asyncio.create_task(engine.alert_delivery_loop())

@app.get("/health/alerts")
def health():
    return engine.evaluate()
```

---

## ✅ Production readiness

- ✔️ **164/164 tests passing** (no live Redis required — `pip install fakeredis`)
- ✔️ **Memory mode** — runs without Redis, metrics buffered in-process
- ✔️ **Fail-safe** — Redis down never crashes requests or the drain loop
- ✔️ **Backpressure** — queue capped at 10,000 events, drops oldest on overflow
- ✔️ **Batched writes** — 100 events per Redis pipeline, 50ms drain interval
- ✔️ **Graceful shutdown** — flushes all in-memory aggregates on app stop
- ✔️ **Slack delivery** — rate-limited, non-blocking, survives HTTP errors

---

## 🚀 What’s coming (v1.5)

- Per-endpoint breakdown in evaluate()
- alertengine-server Docker image (multi-service ingest)
- Grafana dashboard provisioning
- PagerDuty / OpsGenie routing

---

## 💬 Support

📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)

🐙 [github.com/Tandem-Media/fastapi-alertengine](https://github.com/Tandem-Media/fastapi-alertengine)

---

## 🛡️ License

MIT
