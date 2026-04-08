# ⚡ fastapi-alertengine

[![PyPI version](https://img.shields.io/pypi/v/fastapi-alertengine.svg)](https://pypi.org/project/fastapi-alertengine/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-alertengine.svg)](https://pypi.org/project/fastapi-alertengine/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub Sponsors](https://img.shields.io/github/sponsors/Tandem-Media?style=social)](https://github.com/sponsors/Tandem-Media)

**Drop-in request monitoring + alerting for FastAPI — in under 60 seconds.**

No Prometheus.  
No Grafana.  
No dashboards to configure.  

Just install → add middleware → get alerts.

---

## 🚀 Quick Start (30 seconds)

### 1. Install

```bash
pip install fastapi-alertengine
2. Plug and play
Python
from fastapi import FastAPI
import redis
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

app = FastAPI()

redis_client = redis.Redis.from_url("redis://localhost:6379/0")

# Initialize the engine and add middleware
alert_engine = get_alert_engine(redis_client=redis_client)
app.add_middleware(RequestMetricsMiddleware, alert_engine=alert_engine)


@app.get("/")
async def root():
    return {"status": "monitored"}


@app.get("/health/alerts")
def alerts_health():
    """
    Returns:
      {
        "status": "ok" | "warning" | "critical",
        "metrics": {...},
        "thresholds": {...},
        "timestamp": ...
      }
    """
    return alert_engine.evaluate(window_size=200)
🧩 How It Works
fastapi-alertengine handles the heavy lifting of observability without the usual infrastructure overhead:

Sensing
Lightweight middleware captures request context (latency, status code, type).

Streaming
Metrics are piped into Redis Streams (e.g. anchorflow:request_metrics), so your API performance is never compromised by monitoring.

Analysis
The AlertEngine computes:

P95 latency (overall and by type: api vs webhook)
Error rate
An anomaly score vs recent baseline
Alerting
It emits a simple aggregate status:

JSON
{
  "status": "ok" | "warning" | "critical",
  "metrics": {
    "overall_p95_ms": 123.4,
    "webhook_p95_ms": 234.5,
    "api_p95_ms": 110.2,
    "error_rate": 0.03,
    "anomaly_score": 0.8,
    "sample_size": 187
  },
  "thresholds": {
    "p95_warning": 1000,
    "p95_critical": 3000,
    "anomaly_warning": 1.0,
    "anomaly_critical": 2.0,
    "error_rate_critical": 0.2
  },
  "timestamp": 1733779200
}
You can plug this into uptime checks, Pager/Slack alerts, or your own dashboards.

🧰 Public API
Python
from fastapi_alertengine import AlertEngine, RequestMetricsMiddleware, get_alert_engine
AlertEngine
The core evaluation engine, backed by Redis Streams.

Python
from fastapi_alertengine import AlertEngine
import redis

redis_client = redis.Redis.from_url("redis://localhost:6379/0")
alert_engine = AlertEngine(redis=redis_client)

result = alert_engine.evaluate(window_size=200)
print(result["status"])  # "ok" | "warning" | "critical"
RequestMetricsMiddleware
FastAPI middleware hook.

Python
from fastapi_alertengine import RequestMetricsMiddleware

app.add_middleware(RequestMetricsMiddleware, alert_engine=alert_engine)
Use this as the place to:

Measure per-request latency
Classify traffic (type="api" vs "webhook")
Write events into the Redis stream your AlertEngine reads from
get_alert_engine
Helper to construct a singleton engine:

Python
from fastapi_alertengine import get_alert_engine
import redis

redis_client = redis.Redis.from_url("redis://localhost:6379/0")
alert_engine = get_alert_engine(redis_client=redis_client)
Call this once in startup/DI and reuse the engine across your app.

📡 Redis Stream Format
AlertEngine expects events in a Redis Stream (default: anchorflow:request_metrics) like:

Python
import time

redis_client.xadd(
    "anchorflow:request_metrics",
    {
        "latency_ms": 123.4,
        "type": "api",          # or "webhook"
        "status_code": 200,
        "timestamp": int(time.time()),
    },
)
It then:

Reads the last N events (configurable window_size)
Computes P95 latency
Computes error rate and anomaly score
Emits an overall status and metrics bundle
🏦 Why “Financial-Grade”?
Derived from the core infrastructure ideas behind AnchorFlow, this engine is aimed at environments where downtime and “flying blind” aren’t options:

P95 Precision
Don’t just track averages. Catch the tail events that frustrate your users and customers.

Failure Pressure Signal
Combines error rate and latency spikes into a single, actionable status.

Audit-Friendly Shape
Structured metrics and thresholds that can feed your own logging / compliance pipeline.

AI-Agent Friendly
A clean __all__ surface (AlertEngine, RequestMetricsMiddleware, get_alert_engine) that tools like Cursor / Claude / Copilot can understand and wire automatically.

⚙️ Configuration (Roadmap)
Current version exposes thresholds and stream keys primarily in code (AlertEngine and config classes). A typical configuration layer might include:

Variable	Default	Description
REDIS_URL	redis://localhost:6379/0	Redis connection URL
STREAM_KEY	anchorflow:request_metrics	Redis stream for request metrics
LATENCY_P95_WARNING	1000 (ms)	P95 warning threshold
LATENCY_P95_CRITICAL	3000 (ms)	P95 critical threshold
ERROR_RATE_CRITICAL	0.2	20%+ error rate is critical
Future releases will promote these to first‑class config options via AlertConfig.

✅ Requirements
Python 3.10+
FastAPI
Redis reachable from your FastAPI app
🛡️ License
Distributed under the MIT License. See LICENSE for more information.
