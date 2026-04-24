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
    docker run -p 6379:6379 redis
    uvicorn demo_app:app --reload
    python load.py          # in a second terminal

Expose publicly for WhatsApp tap-to-recover:
    ngrok http 8000         # set BASE_URL=https://xxxx.ngrok.io in .env
"""

import asyncio
import logging
import os
import random
from contextlib import asynccontextmanager

import redis
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine
from whatsapp_alert import send_critical_alert, send_recovery_message

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("demo")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# ── AlertEngine setup ──────────────────────────────────────────────────────────
redis_client = redis.Redis.from_url(
    os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
)
engine = get_alert_engine(redis_client=redis_client)

# ── Failure switch ─────────────────────────────────────────────────────────────
_FAIL: dict = {
    "enabled":       False,
    "latency_boost": 0.0,
    "error_rate":    0.0,
}

# ── Alert state (prevents repeated alerts) ────────────────────────────────────
_ALERT: dict = {
    "sent":            False,   # critical alert sent
    "recovery_sent":   False,   # recovery message sent
    "last_status":     None,
}

# ── Background health monitor ──────────────────────────────────────────────────

async def _monitor_loop():
    """
    Polls health every 5 seconds. Sends WhatsApp on critical,
    sends recovery message when health returns to normal.
    """
    await asyncio.sleep(10)   # let the app warm up first
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

            if status == "critical" and not _ALERT["sent"]:
                _ALERT["sent"]          = True
                _ALERT["recovery_sent"] = False

                # Build confirm URL — uses AlertEngine's action token
                confirm_url = f"{BASE_URL}/action/recover?token=demo-confirm"

                logger.warning(
                    "CRITICAL detected — score=%.0f trend=%s — sending WhatsApp",
                    score, trend,
                )
                sent = send_critical_alert(
                    health_score = score,
                    p95_ms       = p95,
                    error_rate   = err,
                    trend        = trend,
                    confirm_url  = confirm_url,
                )
                if not sent:
                    logger.info("WhatsApp not configured — alert would have been sent here.")

            elif status in ("healthy", "degraded") and _ALERT["sent"] and not _ALERT["recovery_sent"]:
                _ALERT["recovery_sent"] = True
                _ALERT["sent"]          = False

                logger.info("System recovered — score=%.0f — sending recovery message", score)
                sent = send_recovery_message(health_score=score)
                if not sent:
                    logger.info("WhatsApp not configured — recovery message would have been sent here.")

            _ALERT["last_status"] = status

        except Exception as exc:
            logger.error("Monitor error: %s", exc)

        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_monitor_loop())
    yield
    task.cancel()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="AlertEngine Demo", lifespan=lifespan)
app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)


# ── Core payment endpoint ──────────────────────────────────────────────────────

@app.get("/api/payments/process")
async def process_payment():
    """Simulates a payment processor. Degrades when fail mode is active."""
    latency = random.uniform(0.05, 0.15) + _FAIL["latency_boost"]
    await asyncio.sleep(latency)

    if _FAIL["enabled"] and random.random() < _FAIL["error_rate"]:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Payment provider timeout"},
        )

    return {"status": "success", "message": "Payment processed", "latency_ms": round(latency * 1000)}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health/alerts")
def health():
    return engine.evaluate()


# ── Demo controls ──────────────────────────────────────────────────────────────

@app.post("/demo/fail")
def trigger_failure():
    """Full failure — high latency + high error rate."""
    _FAIL.update({"enabled": True, "latency_boost": 1.2, "error_rate": 0.5})
    _ALERT["sent"] = False   # allow new alert
    logger.warning("FAIL MODE ENABLED")
    return {
        "status":  "FAIL MODE ENABLED",
        "message": "Watch /health/alerts — score will drop within 30s. WhatsApp alert incoming.",
    }


@app.post("/demo/degrade")
def degrade_only():
    """Soft degradation — latency only, no errors."""
    _FAIL.update({"enabled": True, "latency_boost": 0.7, "error_rate": 0.0})
    logger.warning("DEGRADED MODE ENABLED")
    return {"status": "DEGRADED MODE", "message": "Latency degraded. Watch for RoC alert."}


@app.post("/demo/recover")
def recover():
    """Manual recovery — resets all failure flags."""
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("SYSTEM RECOVERED (manual)")
    return {"status": "RECOVERED", "message": "System restored. Score will rise within 30s."}


# ── Tap-to-recover endpoint (WhatsApp link target) ─────────────────────────────

@app.get("/action/recover", response_class=HTMLResponse)
def recover_from_whatsapp(token: str = Query(None)):
    """
    The URL sent in the WhatsApp message. User taps it on their phone,
    this endpoint fires, system recovers.

    In production: validate the JWT token before recovering.
    For demo: any token is accepted.
    """
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("SYSTEM RECOVERED via WhatsApp tap (token=%s)", token)

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
    h1 { font-size:24px; margin:0 0 8px; color:#f8fafc; }
    p  { color:#94a3b8; margin:0 0 24px; }
    .score { font-size:48px; font-weight:700; color:#16a34a; }
    .label { font-size:14px; color:#64748b; margin-top:4px; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Recovery Authorised</h1>
    <p>System is stabilising. Health score rising.</p>
    <div class="score" id="score">–</div>
    <div class="label">Current health score</div>
  </div>
  <script>
    async function refresh() {
      try {
        const r = await fetch('/health/alerts');
        const d = await r.json();
        const s = d?.health_score?.score;
        if (s != null) document.getElementById('score').textContent = s.toFixed(0);
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
  <title>AlertEngine Demo Control</title>
  <style>
    * { box-sizing:border-box; margin:0; padding:0; }
    body { background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif;
           display:flex; flex-direction:column; align-items:center;
           min-height:100vh; padding:40px 20px; }
    h1 { font-size:24px; margin-bottom:4px; color:#f8fafc; }
    .sub { color:#64748b; margin-bottom:32px; font-size:14px; }
    a { color:#38bdf8; }
    .btn { display:block; width:340px; padding:16px; margin:10px 0;
           border:none; border-radius:10px; font-size:16px; font-weight:600;
           cursor:pointer; transition:opacity .15s; }
    .btn:hover { opacity:.85; }
    .fail    { background:#dc2626; color:#fff; }
    .degrade { background:#d97706; color:#fff; }
    .recover { background:#16a34a; color:#fff; }
    .out { background:#1e293b; padding:16px; border-radius:10px; margin-top:24px;
           font-family:monospace; font-size:13px; width:340px; min-height:60px;
           color:#94a3b8; white-space:pre-wrap; }
    .health { background:#1e293b; border-radius:10px; padding:16px;
              margin-top:16px; width:340px; font-family:monospace; font-size:13px; }
    .score-big { font-size:48px; font-weight:700; text-align:center;
                 margin:8px 0 4px; }
    .trend { text-align:center; font-size:13px; color:#64748b; }
    .green  { color:#16a34a; }
    .yellow { color:#d97706; }
    .red    { color:#dc2626; }
  </style>
</head>
<body>
  <h1>⚡ AlertEngine Demo</h1>
  <div class="sub">Control panel — <a href="/health/alerts" target="_blank">/health/alerts</a></div>

  <button class="btn degrade" onclick="post('/demo/degrade')">🟡 Degrade — Latency Only</button>
  <button class="btn fail"    onclick="post('/demo/fail')">🔴 Full Failure — Latency + Errors</button>
  <button class="btn recover" onclick="post('/demo/recover')">🟢 Recover — Reset System</button>

  <div class="health">
    <div class="score-big" id="score">–</div>
    <div class="trend" id="trend">loading...</div>
  </div>

  <div class="out" id="out">Click a button to trigger a demo event.</div>

  <script>
    async function post(url) {
      const r = await fetch(url, { method:'POST' });
      const d = await r.json();
      document.getElementById('out').textContent = JSON.stringify(d, null, 2);
    }

    async function pollHealth() {
      try {
        const r = await fetch('/health/alerts');
        const d = await r.json();
        const hs = d?.health_score || {};
        const score = hs.score != null ? hs.score.toFixed(0) : '–';
        const status = hs.status || 'unknown';
        const trend  = hs.trend  || '–';
        const el = document.getElementById('score');
        el.textContent = score;
        el.className = 'score-big ' + (status === 'healthy' ? 'green' : status === 'critical' ? 'red' : 'yellow');
        document.getElementById('trend').textContent = `${status} · ${trend}`;
      } catch(e) {}
    }

    pollHealth();
    setInterval(pollHealth, 3000);
  </script>
</body>
</html>""")
