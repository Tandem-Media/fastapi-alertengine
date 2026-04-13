# ⚡ fastapi-alertengine

**Production-ready FastAPI monitoring in one line.**

No Prometheus. No Grafana. No dashboards required — but one is included.

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

That’s it. Four endpoints are now live:

| Endpoint | Description |
|----------|-------------|
| `GET /health/alerts` | Current SLO status — ok / warning / critical |
| `POST /alerts/evaluate` | Evaluate + enqueue for Slack delivery |
| `GET /metrics/history` | Aggregated per-minute metrics from Redis |
| `GET /metrics/ingestion` | Ingestion counters (enqueued / dropped) |

---

## 📊 Observability Dashboard

A full Streamlit dashboard is included in `dashboard/`. Point it at any running instance.

```bash
pip install -r dashboard/requirements.txt
ALERTENGINE_BASE_URL=http://localhost:8000 streamlit run dashboard/app.py
```

**What you get:**
- System status card, P95 latency, error rate, req/min, health score (0–100)
- Time-series charts: requests/min, error rate %, latency (avg + max)
- Endpoint performance table sorted by impact score (requests × avg latency)
- Live alert panel with threshold reference
- Ingestion health: enqueued/dropped counters, queue pressure indicator
- Auto-refresh every 10 seconds

---

## ⚡ How it works

```
Request → middleware (enqueue only, ~0μs) → response returned immediately
              ↓
        deque (in-memory)
              ↓  every 50ms
        drain() → Redis Stream (batched pipeline)
              ↓
        evaluate() → GET /health/alerts → Dashboard
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
| `ALERTENGINE_SERVICE_NAME` | `default` | Service name on every metric |
| `ALERTENGINE_INSTANCE_ID` | `default` | Instance ID on every metric |
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
from fastapi_alertengine import AlertEngine, AlertConfig

config = AlertConfig(service_name="payments-api", p95_critical_ms=500)
engine = AlertEngine(config)
engine.start(app)   # wires middleware, drain task, and all endpoints
```

---

## ✅ Production readiness

- ✔️ **164/164 tests passing** (no live Redis required)
- ✔️ **Memory mode** — runs without Redis, metrics buffered in-process
- ✔️ **Fail-safe** — Redis down never crashes requests or the drain loop
- ✔️ **Backpressure** — queue capped at 10,000 events, drops oldest on overflow
- ✔️ **Batched writes** — 100 events per Redis pipeline, 50ms drain interval
- ✔️ **Graceful shutdown** — flushes all in-memory aggregates on app stop
- ✔️ **Slack delivery** — rate-limited, non-blocking, survives HTTP errors
- ✔️ **Streamlit dashboard** — dark-themed, auto-refresh, zero extra backend config

---

## 🚀 What’s coming (v1.5)

- Per-endpoint breakdown in evaluate()
- alertengine-server Docker image (multi-service ingest)
- PagerDuty / OpsGenie routing

---

## 💬 Support

📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)

🐙 [github.com/Tandem-Media/fastapi-alertengine](https://github.com/Tandem-Media/fastapi-alertengine)

---

## 🛡️ License

MIT
