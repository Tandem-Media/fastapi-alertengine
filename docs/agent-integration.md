# SKILL: fastapi-alertengine

Drop-in request monitoring, alerting, and Slack delivery for FastAPI.

---

## What this does

Adds a middleware and background tasks to your FastAPI app that:

* Measure request latency and capture HTTP status codes
* Track error rates (4xx / 5xx) and P95 latency
* Buffer metrics in-memory and flush to Redis Streams in batches
* Aggregate per-minute metrics per endpoint, stored in Redis
* Post Slack alerts when thresholds are exceeded (rate-limited)
* Expose four observability endpoints automatically

No Prometheus. No Grafana.

---

## When to use this

Use this when:

* You want quick visibility into API performance
* You need latency + error-rate alerting without heavy observability tooling
* You want Slack notifications when things go wrong
* You are working on a small-to-medium FastAPI service

---

## How to integrate

### 1. Install

```bash
pip install fastapi-alertengine
```

---

### 2. Instrument your app (one line)

Given an existing FastAPI app:

```python
from fastapi import FastAPI

app = FastAPI()
```

Modify it to:

```python
from fastapi import FastAPI
from fastapi_alertengine import instrument

app = FastAPI()

# Redis URL via env var (recommended) or argument
# export ALERTENGINE_REDIS_URL=redis://localhost:6379/0
instrument(app)
```

`instrument()` automatically registers:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health/alerts` | Current alert status |
| `POST` | `/alerts/evaluate` | Evaluate + enqueue Slack delivery |
| `GET` | `/metrics/history` | Per-minute aggregated metrics |
| `GET` | `/metrics/ingestion` | Ingestion counters |

---

### 3. (Optional) Enable Slack alerts

```bash
export ALERTENGINE_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Or pass via `AlertConfig`:

```python
from fastapi_alertengine import AlertConfig, instrument

config = AlertConfig(
    slack_webhook_url="https://hooks.slack.com/services/...",
    slack_rate_limit_seconds=10,  # default
    service_name="my-api",
    instance_id="worker-1",
)
instrument(app, config=config)
```

---

### 4. Run the app

```bash
uvicorn main:app --reload
```

Check the alert status:

```bash
curl http://localhost:8000/health/alerts
```

```json
{
  "status": "ok",
  "service_name": "my-api",
  "instance_id": "worker-1",
  "metrics": {
    "overall_p95_ms": 42.1,
    "error_rate": 0.0,
    "sample_size": 128
  }
}
```

Trigger a Slack delivery:

```bash
curl -X POST http://localhost:8000/alerts/evaluate
```

---

## Assumptions

* Python 3.10+
* FastAPI app already exists
* Redis is available (default: localhost)

---

## Notes for agents

* Use `instrument(app)` — it is the recommended and simplest integration path
* Do not modify existing routes
* Only add `instrument()` and optional `AlertConfig`; keep integration minimal and non-invasive
* Do not introduce additional dependencies unless required
* `evaluate()` returns `{"status": "ok"|"warning"|"critical", ...}`; do not call `evaluate_alerts()`

---

## Optional enhancements

Agents may optionally:

* Configure thresholds and service identity using `AlertConfig`
* Set `ALERTENGINE_SLACK_WEBHOOK_URL` for Slack delivery
* Pass `health_path="/my/health"` to `instrument()` to change the health endpoint path

---

## Outcome

After integration:

* The application automatically tracks request performance
* `/health/alerts` returns the current alert status at any time
* Slack messages are sent when status is `warning` or `critical` (if webhook configured)
* Per-minute aggregated metrics are stored in Redis and accessible via `/metrics/history`
* No additional infrastructure is required beyond Redis

