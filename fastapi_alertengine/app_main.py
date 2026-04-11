# app_main.py
"""
Example FastAPI app using fastapi_alertengine.

Run with:
    uvicorn app_main:app --reload

Then hit:
    /              - normal endpoint
    /error         - always 500
    /health/alerts - current alert status JSON
"""

from fastapi import FastAPI, HTTPException
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

app = FastAPI(title="fastapi_alertengine demo", version="1.1.4")

# Zero-config engine (uses ALERTENGINE_* env vars or defaults)
engine = get_alert_engine()
@app.on_event("startup")
async def start_drain():
    import asyncio
    asyncio.create_task(engine.drain())

# Register metrics middleware
app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.get("/error")
async def error():
    raise HTTPException(status_code=500, detail="Intentional error for demo")


@app.get("/health/alerts")
def alerts_health():
    return engine.evaluate(window_size=200)
