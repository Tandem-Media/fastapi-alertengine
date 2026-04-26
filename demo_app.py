# demo_app.py
"""
fastapi-alertengine Demo App — with real Twilio WhatsApp alerts

Flow:
    1. Load generator hits /api/payments/process continuously
    2. POST /demo/fail → latency + errors spike
    3. Background monitor detects critical health
    4. Sends WhatsApp via Twilio with tap-to-recover link
    5. You tap the link → GET /action/recover → system recovers
    6. WhatsApp recovery confirmation sent

Run:
    pip install fastapi uvicorn redis fastapi-alertengine twilio python-dotenv httpx
    cp .env.example .env   # fill in your credentials
    redis-server
    uvicorn demo_app:app --no-access-log
    python load.py          # in a second terminal

Open simulator at: http://localhost:8000/sim/whatsapp_sim.html
"""

import asyncio
import logging
import os
import random

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fastapi_alertengine import instrument
from whatsapp_alert import send_critical_alert, send_recovery_message

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("demo")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# ── Failure switch ─────────────────────────────────────────────────────────────
_FAIL: dict = {
    "enabled":       False,
    "latency_boost": 0.0,
    "error_rate":    0.0,
}

# ── Alert state ────────────────────────────────────────────────────────────────
_ALERT: dict = {
    "sent":          False,
    "recovery_sent": False,
    "last_status":   None,
}

# ── App setup ──────────────────────────────────────────────────────────────────
app = FastAPI(title="AlertEngine Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = instrument(app, health_path="/health/alerts")

# Serve whatsapp_sim.html at http://localhost:8000/sim/whatsapp_sim.html
app.mount("/sim", StaticFiles(directory=".", html=True), name="sim")


# ── Background health monitor ──────────────────────────────────────────────────

async def _monitor_loop():
    await asyncio.sleep(15)   # let baseline build first
    logger.info("Health monitor started — polling every 5s")

    while True:
        try:
            health = engine.evaluate()
            hs     = health.get("health_score", {})
            score  = hs.get("score", 100)
            status = hs.get("status", "healthy")
            trend  = hs.get("trend", "stable")
            m      = health.get("metrics", {})
            p95    = m.get("overall_p95_ms", 0)
            err    = m.get("error_rate", 0)

            logger.info("Health: score=%.0f status=%s p95=%.0fms err=%.1f%%",
                        score, status, p95, err * 100)

            if status == "critical" and not _ALERT["sent"]:
                _ALERT["sent"]          = True
                _ALERT["recovery_sent"] = False
                confirm_url = f"{BASE_URL}/action/recover?token=demo-confirm"
                logger.warning("CRITICAL — sending WhatsApp alert")
                sent = send_critical_alert(
                    health_score=score,
                    p95_ms=p95,
                    error_rate=err,
                    trend=trend,
                    confirm_url=confirm_url,
                )
                if not sent:
                    logger.info("(WhatsApp not configured — would have sent alert here)")

            elif status in ("healthy", "degraded") and _ALERT["sent"] and not _ALERT["recovery_sent"]:
                _ALERT["recovery_sent"] = True
                _ALERT["sent"]          = False
                logger.info("RECOVERED — sending WhatsApp recovery message")
                sent = send_recovery_message(health_score=score)
                if not sent:
                    logger.info("(WhatsApp not configured — would have sent recovery here)")

            _ALERT["last_status"] = status

        except Exception as exc:
            logger.error("Monitor error: %s", exc)

        await asyncio.sleep(5)


@app.on_event("startup")
async def _start_monitor():
    asyncio.create_task(_monitor_loop())


# ── Core payment endpoint ──────────────────────────────────────────────────────

@app.get("/api/payments/process")
async def process_payment():
    latency = random.uniform(0.05, 0.15) + _FAIL["latency_boost"]
    await asyncio.sleep(latency)

    if _FAIL["enabled"] and random.random() < _FAIL["error_rate"]:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Payment provider timeout"},
        )

    return {
        "status":     "success",
        "message":    "Payment processed",
        "latency_ms": round(latency * 1000),
    }


# ── Demo controls ──────────────────────────────────────────────────────────────

@app.post("/demo/fail")
def trigger_failure():
    _FAIL.update({"enabled": True, "latency_boost": 1.2, "error_rate": 0.5})
    _ALERT["sent"] = False
    logger.warning("FAIL MODE ENABLED")
    return {
        "status":  "FAIL MODE ENABLED",
        "message": "Score will drop within 60s. Watch the simulator.",
    }


@app.post("/demo/degrade")
def degrade_only():
    _FAIL.update({"enabled": True, "latency_boost": 0.7, "error_rate": 0.0})
    logger.warning("DEGRADED MODE")
    return {"status": "DEGRADED MODE", "message": "Latency degraded. Watch for RoC alert."}


@app.post("/demo/recover")
def recover():
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("SYSTEM RECOVERED (manual)")
    return {"status": "RECOVERED", "message": "Score will rise within 60s."}


# ── Tap-to-recover (WhatsApp link target) ──────────────────────────────────────

@app.get("/action/recover", response_class=HTMLResponse)
def recover_from_whatsapp(token: str = Query(None)):
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("RECOVERED via WhatsApp tap (token=%s)", token)

    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Recovery Confirmed</title>
  <style>
    body { background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif;
           display:flex; align-items:center; justify-content:center;
           min-height:100vh; margin:0; }
    .card { text-align:center; padding:40px; max-width:360px; }
    .icon { font-size:64px; margin-bottom:16px; }
    h1   { font-size:24px; margin:0 0 8px; color:#f8fafc; }
    p    { color:#94a3b8; margin:0 0 24px; }
    .score { font-size:64px; font-weight:700; color:#16a34a; }
    .label { font-size:14px; color:#64748b; margin-top:4px; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Recovery Authorised</h1>
    <p>System is stabilising. Health score rising.</p>
    <div class="score" id="score">–</div>
    <div class="label">Current health score / 100</div>
  </div>
  <script>
    async function refresh() {
      try {
        const r = await fetch('/health/alerts');
        const d = await r.json();
        const s = d?.health_score?.score;
        if (s != null) document.getElementById('score').textContent = Math.round(s);
      } catch(e) {}
    }
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>""")


# ── Browser control panel ──────────────────────────────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
def demo_panel():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AlertEngine Demo</title>
  <style>
    * { box-sizing:border-box; margin:0; padding:0; }
    body { background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif;
           display:flex; flex-direction:column; align-items:center;
           min-height:100vh; padding:40px 20px; }
    h1   { font-size:24px; margin-bottom:4px; color:#f8fafc; }
    .sub { color:#64748b; margin-bottom:32px; font-size:14px; }
    a    { color:#38bdf8; }
    .btn { display:block; width:340px; padding:16px; margin:10px 0;
           border:none; border-radius:10px; font-size:16px; font-weight:600;
           cursor:pointer; transition:opacity .15s; }
    .btn:hover { opacity:.85; }
    .fail    { background:#dc2626; color:#fff; }
    .degrade { background:#d97706; color:#fff; }
    .recover { background:#16a34a; color:#fff; }
    .health  { background:#1e293b; border-radius:10px; padding:20px;
               margin-top:20px; width:340px; text-align:center; }
    .score-big { font-size:64px; font-weight:700; margin:8px 0 4px; }
    .trend     { font-size:13px; color:#64748b; }
    .green  { color:#16a34a; }
    .yellow { color:#d97706; }
    .red    { color:#dc2626; }
    .out { background:#1e293b; padding:16px; border-radius:10px; margin-top:16px;
           font-family:monospace; font-size:12px; width:340px; min-height:48px;
           color:#94a3b8; white-space:pre-wrap; }
  </style>
</head>
<body>
  <h1>⚡ AlertEngine Demo</h1>
  <div class="sub">
    <a href="/health/alerts" target="_blank">/health/alerts</a> ·
    <a href="/sim/whatsapp_sim.html" target="_blank">📱 WhatsApp Simulator</a> ·
    <a href="/docs" target="_blank">API docs</a>
  </div>

  <button class="btn degrade" onclick="post('/demo/degrade')">🟡 Degrade — Latency Only</button>
  <button class="btn fail"    onclick="post('/demo/fail')">🔴 Full Failure — Latency + Errors</button>
  <button class="btn recover" onclick="post('/demo/recover')">🟢 Recover — Reset System</button>

  <div class="health">
    <div class="score-big green" id="score">–</div>
    <div class="trend" id="trend">loading...</div>
  </div>

  <div class="out" id="out">Click a button to trigger a demo event.</div>

  <script>
    async function post(url) {
      const r = await fetch(url, { method: 'POST' });
      const d = await r.json();
      document.getElementById('out').textContent = JSON.stringify(d, null, 2);
    }

    async function pollHealth() {
      try {
        const r  = await fetch('/health/alerts');
        const d  = await r.json();
        const hs = d?.health_score || {};
        const score  = hs.score  != null ? Math.round(hs.score) : '–';
        const status = hs.status || 'unknown';
        const trend  = hs.trend  || '–';
        const el = document.getElementById('score');
        el.textContent = score;
        el.className   = 'score-big ' +
          (status === 'healthy' ? 'green' : status === 'critical' ? 'red' : 'yellow');
        document.getElementById('trend').textContent = `${status} · ${trend}`;
      } catch(e) {}
    }

    pollHealth();
    setInterval(pollHealth, 3000);
  </script>
</body>
</html>""")