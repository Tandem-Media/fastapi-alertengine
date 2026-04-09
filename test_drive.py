# test_drive.py

import asyncio
import random
import time
from typing import Optional

import redis
from fastapi import FastAPI, Response, status

from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

STREAM_KEY = "anchorflow:request_metrics"

app = FastAPI()


def init_redis() -> Optional[redis.Redis]:
    try:
        client = redis.Redis(host="localhost", port=6379, decode_responses=True)
        client.ping()
        print("✅ Connected to Redis on localhost:6379")
        return client
    except Exception as e:
        print(f"⚠️ Redis not available: {e}")
        return None


r = init_redis()
engine = get_alert_engine(redis_client=r) if r is not None else None


@app.middleware("http")
async def metrics_stream_middleware(request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    if r is not None:
        try:
            kind = "chaos" if request.url.path.startswith("/chaos") else "api"
            r.xadd(
                STREAM_KEY,
                {
                    "latency_ms": duration_ms,
                    "type": kind,
                    "status_code": response.status_code,
                    "timestamp": int(time.time()),
                },
            )
        except Exception as e:
            print(f"⚠️ Failed to write to Redis stream: {e}")

    return response


if engine is not None:
    app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)


@app.get("/normal")
async def normal_route():
    return {"status": "all good"}


@app.get("/chaos")
async def chaos_route():
    if random.random() < 0.2:
        await asyncio.sleep(1.5)

    if random.random() < 0.15:
        return Response(
            content='{"error": "bad luck"}',
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            media_type="application/json",
        )

    return {"status": "survived"}


@app.get("/status")
def get_status():
    if engine is None:
        return {
            "status": "ok",
            "reason": "no_redis",
            "metrics": {},
            "thresholds": {},
            "timestamp": int(time.time()),
        }

    return engine.evaluate(window_size=100)