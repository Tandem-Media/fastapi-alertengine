#  fastapi-alertengine

**Rugged, self-hosted observability for high-stakes APIs.**

When a critical endpoint starts failing, dashboards don't help.  
You need to know immediately — and act before it costs you money.

AlertEngine was built out of necessity for **AnchorFlow / Tofamba** — financial systems processing live mobile-money transactions where latency and failure directly translate into lost revenue. I refused to pay a SaaS tax for slow, bloated monitoring stacks.

So this was built to do one thing:

> **Reduce MTTR from minutes of panic to seconds of controlled action.**

---

[![PyPI version](https://badge.fury.io/py/fastapi-alertengine.svg)](https://pypi.org/project/fastapi-alertengine/)
[![Tests](https://img.shields.io/badge/tests-259%20passed-brightgreen)](https://github.com/Tandem-Media/fastapi-alertengine)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/fastapi-alertengine/)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/Tandem-Media/fastapi-alertengine/blob/main/LICENSE)
[![PyPI Downloads](https://img.shields.io/pypi/dm/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)

 **259/259 tests passing**  
**Derived from financial infrastructure (AnchorFlow / Tofamba)**  
 **AI-agent friendly (Claude Code / Copilot / Cursor)**  
 **Memory mode — runs without Redis**

---

## 🚀 Quickstart

```bash
pip install fastapi-alertengine
```

```python
from fastapi import FastAPI
import redis
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

app = FastAPI()

redis_client = redis.Redis.from_url("redis://localhost:6379/0")
engine       = get_alert_engine(redis_client=redis_client)

app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

@app.get("/health/alerts")
def health():
    return engine.evaluate(window_size=200)
```

That's it. Four endpoints are now live:

| Endpoint | Description |
|---|---|
| `GET /health/alerts` | SLO health payload — P95, error rate, anomaly score, alert array |
| `GET /incidents/timeline` | Append-only incident event log (Redis ZSET) |
| `GET /action/confirm` | JWT-signed action confirmation page |
| `POST /action/restart` | JWT-gated restart trigger with replay protection |

---
## 🧠 v1.6 — Decision & Recovery Layer

fastapi-alertengine has evolved beyond observability.

It now functions as a **real-time decision support system for production APIs**.

Where traditional monitoring tools stop at dashboards, AlertEngine interprets system behavior and produces actionable operational intelligence.

---

### ⚙️ What Changed in v1.6

#### 1. Health Intelligence Layer
Every request is now evaluated into a single system health score:

- p95 latency deviation
- error rate impact
- anomaly patterns

Output:
- `health_score` (0–100)
- `health_status` (healthy / degraded / critical)
- explanation of contributing factors

---

#### 2. Action Recommendation System
Instead of only alerting, the system now suggests operational responses:

- WARNING → monitor closely
- HIGH → investigate latency spikes
- CRITICAL → isolate or restart service

No automatic execution is performed by default—this remains a safe, human-controlled system.

---

#### 3. Incident Replay Capability
All events are stored in an append-only Redis stream, enabling:

- full incident reconstruction
- trace-based filtering
- forensic debugging of failures

---

#### 4. Decision-Oriented Architecture
The system now follows a structured flow:

## 🏦 Why AlertEngine?

---

### 🧠 Design Philosophy

AlertEngine is not a dashboard replacement.

It is an **operational reasoning layer for high-stakes APIs**.

It focuses on:
- tail latency (P95, not averages)
- silent degradation detection
- fast interpretation under failure conditions
- minimal overhead on production systems

---

### ⚡ v1.6 Output Contract

Each evaluation returns:

- metrics snapshot
- health score (0–100)
- system status
- alerts (if any)
- recommended action
- reasons for classification

---

### 🚀 Why This Matters

Most observability systems tell you what happened.

AlertEngine is designed to tell you:

> what is happening, how bad it is, and what you should do next.
**Production-grade monitoring in one line — without Prometheus, Grafana, or vendor lock-in.**

Most FastAPI applications fall into one of two traps: they run completely blind, or they bolt on a full Prometheus + Grafana stack that takes days to configure and costs money every month. AlertEngine occupies the gap.

**Financial-grade reliability**  
Built to protect endpoints like `/checkout` where latency is revenue. Tracks the Hidden 5% — P95 tail latency that averages always hide, but where your most valuable users silently drop off.

**Zero SaaS tax**  
No per-node pricing. No vendor lock-in. Full control of your data. Runs on your own Redis instance — Railway, Upstash, ElastiCache, or bare Redis.

**Active recovery ready**  
Designed for closed-loop incident response: detect → explain → act. Not just a dashboard — a control system.

**Designed for minimal overhead**  
Async ingestion pipeline. Fire-and-forget stream writes. Your request path is never blocked by the observability layer.

---

## ⚡ What You Get

```json
{
  "status": "critical",
  "metrics": {
    "overall_p95_ms": 854.2,
    "webhook_p95_ms": 910.4,
    "api_p95_ms":     720.1,
    "error_rate":     0.19,
    "anomaly_score":  1.4,
    "sample_size":    187
  },
  "alerts": [
    {
      "type":     "latency_spike",
      "message":  "P95 latency (854ms) exceeds threshold (800ms)",
      "severity": "critical"
    }
  ],
  "timestamp":      1712756301,
  "engine_version": "1.3.0"
}
```

All keys always present. No optional fields. Safe to consume in monitoring scripts without null-checks.

---

## 🧩 How It Works

### Detect
`RequestMetricsMiddleware` intercepts every ASGI request. Captures wall-clock latency in milliseconds, HTTP status code, and request type (`api` / `webhook`). Writes a structured event to Redis via `XADD` — fire-and-forget, designed for minimal impact on the hot path.

### Explain
`AlertEngine.evaluate()` reads the last N events via `XREVRANGE`. Computes:
- Rolling P95 latency (overall + per request type)
- Error rate as a fraction of total requests
- Anomaly score vs learned baseline
- Endpoint hotspot identification and business impact translation

### Act
Threshold breach → structured alert → signed JWT action token (90-second HS256). Incident recorded to Redis ZSET. Recovery authorization available via `/action/confirm` and `/action/restart`.

---

## 🛡️ Incident Timeline

Every threshold breach is recorded to an append-only Redis ZSET:

```bash
GET /incidents/timeline?limit=20&since=1712756000
```

```json
{
  "events": [
    {
      "timestamp":  "2026-04-10T14:38:21Z",
      "event_type": "latency_spike",
      "severity":   "critical",
      "message":    "P95 latency (3,240ms) exceeds threshold (3,000ms)",
      "metrics":    { "p95_ms": 3240 }
    }
  ]
}
```

---

## 🔑 JWT Action Tokens

When a critical alert fires, AlertEngine issues a signed 90-second action token:

```python
# Included in alert payload
"action_url": "https://your-app.railway.app/action/confirm?token=eyJ..."
```

The token encodes the incident context. `/action/confirm` renders a confirmation page. `/action/restart` executes the recovery with JTI replay protection — no action can be authorized twice.

**WhatsApp delivery of action links is in active development (v1.4.0).**

---

## 🧰 Public API

```python
from fastapi_alertengine import (
    AlertEngine,
    RequestMetricsMiddleware,
    get_alert_engine,
)
```

### `AlertEngine`
```python
result = engine.evaluate(window_size=200)
# result["status"] → "ok" | "warning" | "critical"
# result["metrics"] → full metric dict
# result["alerts"]  → list of active alerts
```

### `RequestMetricsMiddleware`
```python
app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)
```

### `get_alert_engine()`
```python
engine = get_alert_engine(redis_client=redis_client)
# Returns a singleton. Safe to call at module level.
```

---

## ⚙️ Configuration

All thresholds are injectable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `ALERTENGINE_REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `ALERT_P95_WARNING_MS` | `1000` | P95 warning threshold (ms) |
| `ALERT_P95_CRITICAL_MS` | `3000` | P95 critical threshold (ms) |
| `ALERT_ERROR_CRITICAL_PCT` | `20` | Error rate critical threshold (%) |
| `ALERT_ANOMALY_WARN` | `1.0×` | Anomaly score warning multiplier |
| `ALERT_ANOMALY_CRIT` | `2.0×` | Anomaly score critical multiplier |

---

## 📡 Redis Stream Format

Events are written as flat Redis hashes under a namespaced stream key. The stream is capped with `MAXLEN ~` — no background cleanup required.

```python
redis_client.xadd(
    "anchorflow:request_metrics",
    {
        "latency_ms":  "143.72",
        "type":        "api",       # "api" | "webhook"
        "status_code": "200",
        "timestamp":   "1712756301",
    }
)
```

Works on any Redis 5.0+ instance. No RedisTimeSeries or RedisBloom required.

---

## ✅ Reliability

- 259/259 tests passing (pytest + httpx + fakeredis)
- Circuit breaker: Redis failures are isolated automatically. Your request path never blocks or throws due to the observability layer.
- Memory mode: runs without Redis at all — useful for local development and CI
- Safe for production request paths — observability failures are contained, never propagated

---

## 🤖 AI-Agent Friendly

Clean `__all__` export surface, predictable output schema, minimal integration steps.

Works seamlessly with Claude Code, GitHub Copilot, and Cursor. The health endpoint schema is stable across versions — safe to consume in AI-driven pipelines without defensive null-checks.

---

## 🛡️ Stability Audits & Active Recovery

AlertEngine is the engine behind a production observability service for teams that cannot afford downtime but do not have a full SRE function.

**For your API, I offer:**

| Tier | What You Get | |
|---|---|---|
| **24h Forensic Audit** | Full P95 & error analysis. Identifying revenue leaks your current monitoring misses. | `$1,500` |
| **Revenue Protection Engine** | Proprietary AlertEngine install. P95 monitoring on revenue-critical endpoints. | `$4,500` |
| **Active Recovery Shield** | AlertEngine + WhatsApp Command Center. CEO-level mobile control with 5-second authorized recovery. Your kill switch. | `$9,500` |

This is not a report. It is an operational control system.

→ **[View on Upwork](https://www.upwork.com/freelancers/~01f56dcb08577b4472?viewMode=1)**  
→ **[anchorflow@outlook.com](mailto:anchorflow@outlook.com)** for direct consulting

---

## 🗺️ Roadmap

| Feature | Status |
|---|---|
| WhatsApp incident alerts (Twilio) — detect → notify → confirm → restart | 🔄 In progress (v1.4.0) |
| Remote alert engine (hosted SaaS mode) | 📋 Planned |
| Multi-service correlation | 📋 Planned |
| Grafana dashboard JSON provisioning | 📋 Planned |
| Config-first setup via `AlertConfig` | 📋 Planned |

---

## 📬 Contact & Support

**📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)**  
**🐙 [github.com/Tandem-Media/fastapi-alertengine](https://github.com/Tandem-Media/fastapi-alertengine)**

We support early adopters and teams running critical systems.

---

## 📄 License

MIT — free to use, modify, and deploy. No strings attached.
