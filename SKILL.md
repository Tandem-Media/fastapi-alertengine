# SKILL: fastapi-alertengine

Drop-in request monitoring and alerting for FastAPI.

---

## What this does

Adds a middleware to your FastAPI app that:

* Measures request latency
* Captures HTTP status codes
* Tracks error rates (4xx/5xx)
* Emits alerts when performance degrades

No Prometheus. No Grafana. No dashboards required.

---

## When to use this

Use this when:

* You want quick visibility into API performance
* You need basic alerting without heavy observability tooling
* You are working on a small-to-medium FastAPI service

---

## How to integrate

### 1. Install

```bash
pip install fastapi-alertengine
```

---

### 2. Add middleware

Given an existing FastAPI app:

```python
from fastapi import FastAPI

app = FastAPI()
```

Modify it to:

```python
from fastapi import FastAPI
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

app = FastAPI()

engine = get_alert_engine()

app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)
```

---

### 3. Run the app

Start your server normally:

```bash
uvicorn main:app --reload
```

Make a few requests.

You will start seeing alerts like:

```
[ALERT] P95 latency 812ms exceeded threshold 500ms
[ALERT] Error rate 12% exceeded threshold 5%
```

---

## Assumptions

* Python 3.10+
* FastAPI app already exists
* Redis is available (default: localhost)

---

## Notes for agents

* Do not modify existing routes
* Only add middleware and engine initialization
* Keep integration minimal and non-invasive
* Do not introduce additional dependencies unless required

---

## Optional enhancement

Agents may optionally:

* Add a background task to periodically call `engine.evaluate_alerts()`
* Configure thresholds using `AlertConfig`

---

## Outcome

After integration:

* The application automatically tracks request performance
* Alerts appear in the console when thresholds are exceeded
* No additional infrastructure is required beyond Redis

