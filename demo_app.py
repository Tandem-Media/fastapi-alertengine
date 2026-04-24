# demo_app.py
"""
fastapi-alertengine Demo App

Designed for controlled, repeatable demos. One endpoint processes
"payments", a failure switch degrades it on command, and AlertEngine
detects and scores the degradation in real time.

Run:
    uvicorn demo_app:app --reload

Then in a second terminal:
    python load.py

Trigger failure:
    curl -X POST http://localhost:8000/demo/fail

Recover:
    curl -X POST http://localhost:8000/demo/recover

Watch health:
    open http://localhost:8000/health/alerts
"""

import asyncio
import random

import redis
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

app = FastAPI(title="AlertEngine Demo", docs_url="/docs")

# ── AlertEngine setup ──────────────────────────────────────────────────────────
redis_client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
engine = get_alert_engine(redis_client=redis_client)
app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

# ── Failure switch ─────────────────────────────────────────────────────────────
_FAIL = {
    "enabled":        False,
    "latency_boost":  0.0,    # extra seconds added to response time
    "error_rate":     0.0,    # 0.0–1.0 probability of returning an error
}


# ── Core "payment" endpoint ────────────────────────────────────────────────────

@app.get("/api/payments/process")
async def process_payment():
    """Simulates a payment processor. Degrades when fail mode is active."""
    base_latency = random.uniform(0.05, 0.15)
    total_latency = base_latency + _FAIL["latency_boost"]
    await asyncio.sleep(total_latency)

    if _FAIL["enabled"] and random.random() < _FAIL["error_rate"]:
        return {"status": "error", "message": "Payment provider timeout", "latency_ms": round(total_latency * 1000)}

    return {"status": "success", "message": "Payment processed", "latency_ms": round(total_latency * 1000)}


# ── Health endpoint (AlertEngine) ─────────────────────────────────────────────

@app.get("/health/alerts")
def health():
    """Live health payload from AlertEngine."""
    return engine.evaluate()


# ── Demo control endpoints ─────────────────────────────────────────────────────

@app.post("/demo/fail")
def trigger_failure():
    """
    Full failure mode — high latency + high error rate.
    Health score should collapse to critical within 30–60 seconds.
    """
    _FAIL["enabled"]       = True
    _FAIL["latency_boost"] = 1.2    # adds ~1.2s to every request
    _FAIL["error_rate"]    = 0.5    # 50% of requests return errors
    return {
        "status":        "FAIL MODE ENABLED",
        "latency_boost": _FAIL["latency_boost"],
        "error_rate":    _FAIL["error_rate"],
        "message":       "Watch /health/alerts — score will drop within 30s.",
    }


@app.post("/demo/degrade")
def degrade_only():
    """
    Soft degradation — latency only, no errors.
    Good for showing early detection before thresholds are crossed.
    """
    _FAIL["enabled"]       = True
    _FAIL["latency_boost"] = 0.7
    _FAIL["error_rate"]    = 0.0
    return {
        "status":        "DEGRADED MODE",
        "latency_boost": _FAIL["latency_boost"],
        "error_rate":    _FAIL["error_rate"],
        "message":       "Latency degraded. No errors yet. Watch for RoC alert.",
    }


@app.post("/demo/recover")
def recover():
    """
    Full recovery — resets all failure flags.
    Health score should rise back above 75 within 30–60 seconds.
    """
    _FAIL["enabled"]       = False
    _FAIL["latency_boost"] = 0.0
    _FAIL["error_rate"]    = 0.0
    return {
        "status":  "RECOVERED",
        "message": "System restored. Watch /health/alerts — score will rise.",
    }


@app.get("/demo/status")
def demo_status():
    """Current state of the failure switch."""
    return {
        "fail_mode_enabled": _FAIL["enabled"],
        "latency_boost_s":   _FAIL["latency_boost"],
        "error_rate":        _FAIL["error_rate"],
    }


# ── Demo control panel (browser UI) ───────────────────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
def demo_panel():
    """Simple browser control panel for live demos."""
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AlertEngine Demo Control</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 560px; margin: 60px auto;
           padding: 0 20px; background: #0f172a; color: #e2e8f0; }
    h1   { color: #f8fafc; margin-bottom: 4px; }
    p    { color: #94a3b8; margin-top: 0; }
    .btn { display: block; width: 100%; padding: 14px; margin: 10px 0;
           border: none; border-radius: 8px; font-size: 16px; font-weight: 600;
           cursor: pointer; transition: opacity .15s; }
    .btn:hover { opacity: .85; }
    .fail    { background: #dc2626; color: white; }
    .degrade { background: #d97706; color: white; }
    .recover { background: #16a34a; color: white; }
    .status  { background: #1e293b; padding: 16px; border-radius: 8px;
               margin-top: 24px; font-family: monospace; font-size: 13px; }
    a { color: #38bdf8; }
  </style>
</head>
<body>
  <h1>⚡ AlertEngine Demo</h1>
  <p>Control panel — trigger failures and watch <a href="/health/alerts" target="_blank">/health/alerts</a> respond.</p>

  <button class="btn degrade" onclick="post('/demo/degrade')">🟡 Degrade — Latency Only</button>
  <button class="btn fail"    onclick="post('/demo/fail')">🔴 Full Failure — Latency + Errors</button>
  <button class="btn recover" onclick="post('/demo/recover')">🟢 Recover — Reset Everything</button>

  <div class="status" id="out">Click a button to trigger a demo event.</div>

  <script>
    async function post(url) {
      const r = await fetch(url, { method: 'POST' });
      const d = await r.json();
      document.getElementById('out').textContent = JSON.stringify(d, null, 2);
    }
  </script>
</body>
</html>""")
