# demo_app.py
"""
AnchorFlow AlertEngine — Loom Demo App
Run: uvicorn demo_app:app --reload
"""
import asyncio
from fastapi import FastAPI
from fastapi_alertengine import instrument, AlertConfig

app = FastAPI(title="AnchorFlow Demo")

config = AlertConfig(
    service_name="payments-api",
    instance_id="harare-node-1",
    p95_warning_ms=1_000,
    p95_critical_ms=3_000,
    error_rate_warning_pct=2.0,
    error_rate_critical_pct=5.0,
    # Add your Slack webhook here for the live alert in the video
    # slack_webhook_url="https://hooks.slack.com/services/...",
)

instrument(app, config=config)


@app.get("/pay")
async def pay():
    """Normal payment — fast path."""
    await asyncio.sleep(0.05)
    return {"status": "ok", "message": "Payment processed"}


@app.get("/pay/slow")
async def pay_slow():
    """Slow payment — triggers warning alert. Use this during Loom recording."""
    await asyncio.sleep(6.0)
    return {"status": "ok", "message": "Payment processed (slow)"}


@app.get("/pay/critical")
async def pay_critical():
    """Very slow — triggers critical alert."""
    await asyncio.sleep(10.0)
    return {"status": "ok", "message": "Payment processed (critical)"}


@app.get("/pay/error")
async def pay_error():
    """Simulates a 500 error — raises error rate."""
    raise Exception("Payment gateway timeout")


@app.get("/health")
async def health():
    return {"status": "up", "service": "payments-api"}