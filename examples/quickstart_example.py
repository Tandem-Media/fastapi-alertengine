# examples/quickstart_example.py
"""
FastAPI AlertEngine — Minimal Quickstart Example

Shows:
1. One-line instrumentation
2. /health/alerts JSON output
3. Simulated latency spike to trigger an alert

Requirements:
    pip install fastapi-alertengine uvicorn httpx

Optional (for Redis persistence):
    docker run -d -p 6379:6379 redis

Run:
    uvicorn quickstart_example:app --reload

Then visit:
    http://localhost:8000/health/alerts     ← health status
    http://localhost:8000/simulate/spike    ← trigger a latency spike
    http://localhost:8000/simulate/recover  ← return to normal
    http://localhost:8000/docs              ← interactive API docs
"""

import asyncio
import random
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_alertengine import instrument

app = FastAPI(
    title="AlertEngine Quickstart",
    description="Minimal example showing P95 latency tracking and health scoring.",
)

# ── One line. That's the entire integration. ───────────────────────────────────
instrument(app)

# ── Failure simulation state ───────────────────────────────────────────────────
_sim = {
    "spike":      False,   # True = inject high latency
    "error_rate": 0.0,     # 0.0–1.0 = fraction of requests that fail
}


# ── Sample API endpoints ───────────────────────────────────────────────────────

@app.get("/api/payments/process")
async def process_payment():
    """
    Simulated payment endpoint.
    Under normal conditions: ~50–150ms response time.
    During spike simulation: ~2000–4000ms response time.
    """
    base_latency = random.uniform(0.05, 0.15)
    spike_latency = random.uniform(2.0, 4.0) if _sim["spike"] else 0.0
    total = base_latency + spike_latency

    await asyncio.sleep(total)

    if random.random() < _sim["error_rate"]:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Payment provider timeout"},
        )

    return {
        "status":     "success",
        "latency_ms": round(total * 1000),
        "spike":      _sim["spike"],
    }


@app.get("/api/orders/list")
async def list_orders():
    """Normal endpoint — always fast."""
    await asyncio.sleep(random.uniform(0.02, 0.08))
    return {"orders": [], "count": 0}


@app.get("/api/products/search")
async def search_products(q: str = ""):
    """Normal endpoint — always fast."""
    await asyncio.sleep(random.uniform(0.03, 0.10))
    return {"results": [], "query": q}


# ── Simulation controls ────────────────────────────────────────────────────────

@app.post("/simulate/spike")
async def simulate_spike():
    """
    Inject a latency spike (2–4 seconds per request).
    Watch /health/alerts — score will drop toward critical within ~30 seconds.
    """
    _sim["spike"]      = True
    _sim["error_rate"] = 0.4
    return {
        "status":  "spike active",
        "message": "Watch /health/alerts — score will drop toward critical",
        "hint":    "Run: watch -n2 curl -s localhost:8000/health/alerts | python3 -m json.tool",
    }


@app.post("/simulate/recover")
async def simulate_recover():
    """Remove the latency spike. Health score will recover."""
    _sim["spike"]      = False
    _sim["error_rate"] = 0.0
    return {
        "status":  "recovered",
        "message": "Latency spike removed. Health score will recover within ~30 seconds.",
    }


# ── Load generator (optional — hit this in a loop to generate traffic) ─────────

@app.post("/simulate/load")
async def simulate_load(requests: int = 50):
    """
    Fire N requests against /api/payments/process to generate metric samples.
    Useful for warming up the health score before testing a spike.
    """
    import httpx

    results = {"success": 0, "error": 0, "total_ms": 0}

    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        for _ in range(requests):
            try:
                start = time.time()
                r = await client.get("/api/payments/process")
                elapsed = (time.time() - start) * 1000
                results["total_ms"] += elapsed
                if r.status_code == 200:
                    results["success"] += 1
                else:
                    results["error"] += 1
            except Exception:
                results["error"] += 1

    results["avg_ms"] = round(results["total_ms"] / max(requests, 1))
    results["requests"] = requests
    return results


# ── Startup message ────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    print("\n" + "="*60)
    print("  FastAPI AlertEngine — Quickstart Example")
    print("="*60)
    print("  Health endpoint:  http://localhost:8000/health/alerts")
    print("  Spike endpoint:   POST http://localhost:8000/simulate/spike")
    print("  Recover endpoint: POST http://localhost:8000/simulate/recover")
    print("  API docs:         http://localhost:8000/docs")
    print("="*60)
    print("\n  Try this sequence:")
    print("  1. curl -s localhost:8000/health/alerts | python3 -m json.tool")
    print("  2. curl -X POST localhost:8000/simulate/load")
    print("  3. curl -s localhost:8000/health/alerts | python3 -m json.tool")
    print("  4. curl -X POST localhost:8000/simulate/spike")
    print("  5. curl -X POST localhost:8000/simulate/load?requests=100")
    print("  6. curl -s localhost:8000/health/alerts | python3 -m json.tool")
    print()
