# examples/main.py
"""
fastapi_alertengine — minimal demo app.

Three endpoints:
  GET /fast   — responds in ~5ms   (normal traffic)
  GET /slow   — responds in ~600ms (triggers warning)
  GET /error  — returns HTTP 500   (drives up error rate)

Run:
    pip install fastapi uvicorn redis fastapi-alertengine
    redis-server &
    uvicorn examples.main:app --reload

Then hit the endpoints a few times and check:
    curl http://localhost:8000/alerts
"""

import asyncio
import os

import redis
from fastapi import FastAPI

from fastapi_alertengine import (
    AlertConfig,
    AlertEngine,
    RequestMetricsMiddleware,
    aggregate,
    get_alert_engine,
)

# ── Redis ─────────────────────────────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="fastapi_alertengine demo", version="0.1.0")

# Register metrics middleware — must come before any other middleware
app.add_middleware(RequestMetricsMiddleware, redis=rdb)

# ── Demo endpoints ────────────────────────────────────────────────────────────

@app.get("/fast", tags=["demo"])
async def fast_endpoint():
    """Normal response — ~5ms latency."""
    await asyncio.sleep(0.005)
    return {"endpoint": "fast", "latency": "~5ms"}


@app.get("/slow", tags=["demo"])
async def slow_endpoint():
    """Slow response — ~600ms latency, will push p95 into warning range."""
    await asyncio.sleep(0.6)
    return {"endpoint": "slow", "latency": "~600ms"}


@app.get("/error", tags=["demo"])
async def error_endpoint():
    """Always returns 500 — drives up the error rate metric."""
    from fastapi import HTTPException
    raise HTTPException(status_code=500, detail="Intentional error for demo")


# ── Alert status endpoint ─────────────────────────────────────────────────────

@app.get("/alerts", tags=["observability"])
def alert_status():
    """
    Evaluate current alert status from recent request metrics.

    Returns the AlertEngine result plus a p95 breakdown by traffic type.
    """
    engine = get_alert_engine(redis_client=rdb)
    event  = engine.evaluate()

    return {
        "status":  event.status,
        "reason":  event.reason,
        "metrics": {
            "overall_p95_ms": event.metrics.overall_p95_ms,
            "webhook_p95_ms": event.metrics.webhook_p95_ms,
            "api_p95_ms":     event.metrics.api_p95_ms,
            "error_rate":     event.metrics.error_rate,
            "anomaly_score":  event.metrics.anomaly_score,
            "sample_size":    event.metrics.sample_size,
        },
        "thresholds": {
            "p95_warning_ms":      event.thresholds.p95_warning_ms,
            "p95_critical_ms":     event.thresholds.p95_critical_ms,
            "error_rate_critical": event.thresholds.error_rate_critical,
        },
        "aggregated": aggregate(rdb, AlertConfig()),
    }
