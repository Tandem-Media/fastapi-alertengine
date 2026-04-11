# test_drive.py
"""
Local smoke-test harness.

Run with:   uvicorn test_drive:app --reload
Then hit:   http://localhost:8000/normal
            http://localhost:8000/chaos
            http://localhost:8000/health/alerts
"""

import asyncio
import random
from typing import Optional

import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.responses import Response
from starlette import status

from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine
from fastapi_alertengine.config import AlertConfig

# ── Redis + engine ────────────────────────────────────────────────────────

def _try_redis() -> Optional[redis.Redis]:
    try:
        client = redis.Redis.from_url("redis://localhost:6379/0",
                                      decode_responses=True)
        client.ping()
        print("✅  Connected to Redis on localhost:6379")
        return client
    except Exception as exc:
        print(f"⚠️  Redis not available: {exc}")
        return None


_rdb    = _try_redis()
_config = AlertConfig()
_engine = get_alert_engine(config=_config, redis_client=_rdb) if _rdb else None

# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(title="fastapi-alertengine smoke test")

if _engine is not None:
    # Drop-in: one line, all metrics captured automatically.
    app.add_middleware(RequestMetricsMiddleware, alert_engine=_engine)


# ── Routes ────────────────────────────────────────────────────────────────

@app.get("/normal")
async def normal_route():
    return {"status": "all good"}


@app.get("/chaos")
async def chaos_route():
    """Injects random latency spikes and 500 errors to exercise alerting."""
    if random.random() < 0.20:
        await asyncio.sleep(1.5)

    if random.random() < 0.15:
        return Response(
            content='{"error": "bad luck"}',
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            media_type="application/json",
        )

    return {"status": "survived"}


@app.get("/health/alerts")
def health_alerts():
    """Live SLO status from the AlertEngine."""
    if _engine is None:
        return JSONResponse(
            {"status": "ok", "reason": "no_redis", "metrics": {}},
            status_code=200,
        )
    return JSONResponse(_engine.evaluate(window_size=200).as_dict())
