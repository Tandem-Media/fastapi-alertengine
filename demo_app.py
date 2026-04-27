# demo_app.py

import asyncio
import logging
import os
import random
import time

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fastapi_alertengine import instrument
from token_utils import generate_recovery_token, verify_recovery_token, consume_token
from whatsapp_alert import (
    send_critical_alert,
    send_recovery_message,
    send_escalation_alert,
)

# ── Setup ─────────────────────────────────────────────────────────────────────

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("demo")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

if BASE_URL == "http://localhost:8000":
    logger.warning("BASE_URL is localhost — WhatsApp tap links won't work on phone. Set ngrok URL in .env")

# ── Failure simulation ─────────────────────────────────────────────────────────

_FAIL = {
    "enabled":       False,
    "latency_boost": 0.0,
    "error_rate":    0.0,
}

# ── Alert + escalation state ───────────────────────────────────────────────────

_ALERT = {
    "sent":            False,
    "recovery_sent":   False,
    "last_status":     None,
    "incident_id":     None,
    "incident_start":  0,
    "last_sent_at":    0,
    "reminder_sent":   False,
    "escalation_sent": False,
}

COOLDOWN_SECONDS = 120
REMINDER_AFTER   = 120
ESCALATE_AFTER   = 300

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="AlertEngine Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = instrument(app, health_path="/health/alerts")
app.mount("/sim", StaticFiles(directory=".", html=True), name="sim")


# ── Monitor loop ──────────────────────────────────────────────────────────────

async def _monitor_loop():
    await asyncio.sleep(15)
    logger.info("📡 Monitor started")

    while True:
        try:
            health = engine.evaluate(window_size=200)

            hs     = health.get("health_score", {})
            score  = hs.get("score", 100)
            status = hs.get("status", "healthy")
            trend  = hs.get("trend", "stable")
            m      = health.get("metrics", {})
            p95    = m.get("overall_p95_ms", 0)
            err    = m.get("error_rate", 0)
            now    = time.time()

            logger.info("Health: %s | score=%.0f p95=%.0fms err=%.1f%%",
                        status, score, p95, err * 100)

            # New incident detected
            if status == "critical" and _ALERT["last_status"] != "critical":
                _ALERT.update({
                    "sent":            False,
                    "recovery_sent":   False,
                    "incident_id":     f"inc-{int(now)}",
                    "incident_start":  now,
                    "reminder_sent":   False,
                    "escalation_sent": False,
                })

            # Initial alert
            if (status == "critical"
                    and not _ALERT["sent"]
                    and (now - _ALERT["last_sent_at"] > COOLDOWN_SECONDS)):
                _ALERT["sent"]        = True
                _ALERT["last_sent_at"] = now
                logger.warning("🚨 Initial alert (%s)", _ALERT["incident_id"])
                try:
                    send_critical_alert(
                        health_score=round(score),
                        p95_ms=round(p95),
                        error_rate=round(err * 100, 1),
                        trend=trend,
                        _token = generate_recovery_token(_ALERT["incident_id"])
                    confirm_url=f"{BASE_URL}/action/recover?token={_token}",
                    )
                except Exception as e:
                    logger.error("Alert send failed: %s", e)

            # Reminder
            if (status == "critical"
                    and not _ALERT["reminder_sent"]
                    and (now - _ALERT["incident_start"] > REMINDER_AFTER)):
                _ALERT["reminder_sent"] = True
                logger.warning("⏱ Reminder alert (%s)", _ALERT["incident_id"])
                try:
                    send_critical_alert(
                        health_score=round(score),
                        p95_ms=round(p95),
                        error_rate=round(err * 100, 1),
                        trend="still critical",
                        _token = generate_recovery_token(_ALERT["incident_id"])
                    confirm_url=f"{BASE_URL}/action/recover?token={_token}",
                    )
                except Exception as e:
                    logger.error("Reminder failed: %s", e)

            # Escalation
            if (status == "critical"
                    and not _ALERT["escalation_sent"]
                    and (now - _ALERT["incident_start"] > ESCALATE_AFTER)):
                _ALERT["escalation_sent"] = True
                logger.error("🚨 ESCALATION (%s)", _ALERT["incident_id"])
                try:
                    send_escalation_alert(
                        incident_id=_ALERT["incident_id"],
                        duration=int(now - _ALERT["incident_start"]),
                        health_score=round(score),
                    )
                except Exception as e:
                    logger.error("Escalation failed: %s", e)

            # Recovery
            if (status in ("healthy", "degraded")
                    and _ALERT["last_status"] == "critical"):
                logger.info("✅ RECOVERED (%s)", _ALERT["incident_id"])
                try:
                    send_recovery_message(health_score=round(score))
                except Exception as e:
                    logger.error("Recovery message failed: %s", e)
                _ALERT.update({
                    "sent":          False,
                    "recovery_sent": True,
                    "incident_id":   None,
                })

            _ALERT["last_status"] = status

        except Exception as e:
            logger.error("Monitor loop error: %s", e)

        await asyncio.sleep(5)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    asyncio.create_task(_monitor_loop())
    logger.info("🚀 App started")


# ── Payment endpoint ──────────────────────────────────────────────────────────

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


# ── Demo controls ─────────────────────────────────────────────────────────────

@app.post("/demo/fail")
def fail():
    _FAIL.update({"enabled": True, "latency_boost": 1.2, "error_rate": 0.5})
    _ALERT["sent"] = False
    logger.warning("🔴 FAIL MODE")
    return {"status": "FAIL MODE", "message": "Score will drop within 60s."}


@app.post("/demo/degrade")
def degrade():
    _FAIL.update({"enabled": True, "latency_boost": 0.7, "error_rate": 0.0})
    logger.warning("🟡 DEGRADED MODE")
    return {"status": "DEGRADED MODE"}


@app.post("/demo/recover")
def recover(payload: dict = Body(default={})):
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("🟢 MANUAL RECOVERY")
    return {"status": "RECOVERED", "message": "Score will rise within 60s."}


# ── Tap-to-recover page ───────────────────────────────────────────────────────

@app.get("/action/recover", response_class=HTMLResponse)
def recover_from_whatsapp(token: str = Query(None)):
    if not token:
        return HTMLResponse("<h1>⛔ Missing token</h1>", status_code=400)

    payload = verify_recovery_token(token)
    if not payload:
        return HTMLResponse("<h1>⛔ Link expired or invalid</h1>", status_code=403)

    if not consume_token(token):
        return HTMLResponse("<h1>⚠️ Link already used</h1>", status_code=403)

    incident_id = payload.get("incident_id") or payload.get("extra", {}).get("incident_id", "unknown")
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("🟢 RECOVERED via tap — incident=%s", incident_id)

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


# ── Control panel ─────────────────────────────────────────────────────────────

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
    <a href="/sim/whatsapp_sim.html" target="_blank">📱 Simulator</a> ·
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
