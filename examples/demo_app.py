# demo_app.py
"""
AlertEngine Lite — Recording version.
Has embedded monitor loop for demo purposes.
SWAP BACK to simulation-only version after recording.
"""

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
    send_validation_alert,
    send_recovery_message,
    send_voice_call,
    notify_secondary_engineer,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("alertengine_lite")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

if BASE_URL == "http://localhost:8000":
    logger.warning("BASE_URL=localhost — set ngrok URL in .env for real WhatsApp links")

# ── Failure switch ─────────────────────────────────────────────────────────────

_FAIL = {
    "enabled":       False,
    "latency_boost": 0.0,
    "error_rate":    0.0,
}

# ── Incident state ─────────────────────────────────────────────────────────────

_INCIDENT = {
    "active":          False,
    "id":              None,
    "stage":           None,
    "started_at":      0.0,
    "stage_at":        0.0,
    "last_status":     None,
    "score":           100.0,
    "p95":             0.0,
    "err":             0.0,
    "token":           None,
    "voice_sent":      False,
    "secondary_sent":  False,
}

PROPOSE_AFTER  = 5
VALIDATE_AFTER = 8
VOICE_S        = 180
SECONDARY_S    = 300

# ── Circuit breaker ────────────────────────────────────────────────────────────

_CB = {
    "failures":    0,
    "disabled_at": 0.0,
    "threshold":   3,
    "cooldown_s":  60,
}


def _cb_open() -> bool:
    if _CB["failures"] >= _CB["threshold"]:
        if time.time() - _CB["disabled_at"] < _CB["cooldown_s"]:
            return True
        _CB["failures"]    = 0
        _CB["disabled_at"] = 0.0
        logger.info("🔌 Circuit breaker reset")
    return False


def _cb_record(success: bool) -> None:
    if success:
        _CB["failures"] = 0
    else:
        _CB["failures"] += 1
        if _CB["failures"] >= _CB["threshold"]:
            _CB["disabled_at"] = time.time()
            logger.warning("🔌 Circuit breaker OPEN")


# ── Notify wrapper ─────────────────────────────────────────────────────────────

async def _notify(fn, *args, **kwargs) -> None:
    if _cb_open():
        logger.warning("🔌 Notification suppressed — circuit breaker open")
        return
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
        _cb_record(True)
    except Exception as e:
        logger.error("Notification error (%s): %s", fn.__name__, e)
        _cb_record(False)


def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as e:
        logger.error("🔥 Background task failed: %s", e)


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    task.add_done_callback(_handle_task_result)


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="AlertEngine Lite")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = instrument(app, health_path="/health/alerts")
app.mount("/sim", StaticFiles(directory=".", html=True), name="sim")


# ── State helpers ──────────────────────────────────────────────────────────────

def _transition(new_stage: str) -> None:
    if _INCIDENT["stage"] == new_stage:
        return
    logger.info("🔄 %s → %s (%s)", _INCIDENT["stage"], new_stage, _INCIDENT["id"])
    _INCIDENT["stage"]    = new_stage
    _INCIDENT["stage_at"] = time.time()


def _stage_age() -> float:
    return time.time() - _INCIDENT["stage_at"]


# ── Health evaluation ──────────────────────────────────────────────────────────

def _evaluate_health():
    recent = list(engine._recent)

    if len(recent) >= 15:
        lats     = [e["latency_ms"] for e in recent]
        errors   = sum(1 for e in recent if e.get("status_code", 200) >= 500)
        err_rate = errors / len(recent)
        p95      = engine._percentile(lats, 95)

        if err_rate > 0.4 or p95 > 1200:
            score  = max(0.0, 100.0 - (err_rate * 60) - max(0, (p95 - 200) / 30))
            score  = round(score, 1)
            status = "critical" if score < 70 else "degraded"
            logger.info("⚡ Fast-path: %s | score=%.0f p95=%.0fms err=%.1f%%",
                        status, score, p95, err_rate * 100)
            return status, score, p95, err_rate

    data   = engine.evaluate(window_size=200)
    hs     = data.get("health_score", {})
    m      = data.get("metrics", {})
    return (
        hs.get("status", "healthy"),
        hs.get("score", 100),
        m.get("overall_p95_ms", 0),
        m.get("error_rate", 0),
    )


# ── Monitor loop ───────────────────────────────────────────────────────────────

async def _monitor():
    await asyncio.sleep(15)
    logger.info("📡 Monitor active")

    while True:
        try:
            status, score, p95, err = _evaluate_health()
            now = time.time()

            # New incident
            if status == "critical" and not _INCIDENT["active"]:
                _INCIDENT.update({
                    "active":     True,
                    "id":         f"inc-{int(now)}",
                    "stage":      None,
                    "stage_at":   now,
                    "started_at": now,
                    "score":      score,
                    "p95":        p95,
                    "err":        err,
                })
                _transition("detected")
                _fire_and_forget(_notify(
                    send_critical_alert,
                    health_score=round(score),
                    p95_ms=round(p95),
                    error_rate=err,
                    trend="degrading",
                    confirm_url="",
                ))

            # DETECTED → PROPOSED
            elif _INCIDENT["stage"] == "detected" and _stage_age() >= PROPOSE_AFTER:
                _transition("proposed")

            # PROPOSED → VALIDATED + recovery link
            elif _INCIDENT["stage"] == "proposed" and _stage_age() >= VALIDATE_AFTER:
                token = generate_recovery_token(_INCIDENT["id"])
                url   = f"{BASE_URL}/action/recover?token={token}"
                _INCIDENT["token"] = token
                _transition("validated")
                _fire_and_forget(_notify(
                    send_validation_alert,
                    health_score=round(score),
                    p95_ms=round(p95),
                    confirm_url=url,
                ))

            # Recovery
            if (_INCIDENT["active"]
                    and status in ("healthy", "degraded")
                    and _INCIDENT["last_status"] == "critical"):
                logger.info("✅ Recovered — %s", _INCIDENT["id"])
                _fire_and_forget(_notify(
                    send_recovery_message,
                    health_score=round(score),
                ))
                _INCIDENT.update({
                    "active": False,
                    "id":     None,
                    "stage":  None,
                    "token":  None,
                })

            _INCIDENT["last_status"] = status

        except Exception as e:
            logger.error("Monitor error: %s", e)

        await asyncio.sleep(2)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_monitor())
    logger.info("🚀 AlertEngine Lite started")


# ── Payment endpoint ───────────────────────────────────────────────────────────

@app.get("/api/payments/process")
async def process_payment():
    latency = random.uniform(0.05, 0.15) + _FAIL["latency_boost"]
    await asyncio.sleep(latency)
    if _FAIL["enabled"] and random.random() < _FAIL["error_rate"]:
        return JSONResponse(status_code=500,
                            content={"status": "error", "message": "Payment provider timeout"})
    return {"status": "success", "latency_ms": round(latency * 1000)}


# ── Demo controls ──────────────────────────────────────────────────────────────

@app.post("/demo/fail")
def demo_fail():
    _FAIL.update({"enabled": True, "latency_boost": 2.5, "error_rate": 0.8})
    logger.warning("🔴 FAIL MODE ON")
    return {"status": "FAIL MODE"}


@app.post("/demo/degrade")
def demo_degrade():
    _FAIL.update({"enabled": True, "latency_boost": 0.7, "error_rate": 0.0})
    return {"status": "DEGRADED"}


@app.post("/demo/recover")
def demo_recover(payload: dict = Body(default={})):
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("🟢 MANUAL RECOVERY")
    return {"status": "RECOVERED"}


# ── Status ─────────────────────────────────────────────────────────────────────

@app.get("/status")
def pipeline_status():
    return {
        "incident_active": _INCIDENT["active"],
        "incident_id":     _INCIDENT["id"],
        "stage":           _INCIDENT["stage"],
        "fail_mode":       _FAIL["enabled"],
    }


# ── Tap-to-recover ─────────────────────────────────────────────────────────────

@app.get("/action/recover", response_class=HTMLResponse)
def action_recover(token: str = Query(None)):
    if not token:
        return HTMLResponse("<h1>Missing token</h1>", status_code=400)

    payload = verify_recovery_token(token)
    if not payload:
        return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Expired</title>
<style>body{background:#0f172a;color:#e2e8f0;font-family:system-ui;display:flex;
align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:24px;}
.icon{font-size:48px;margin-bottom:16px;}</style></head>
<body><div><div class="icon">⛔</div><h2>Link Expired</h2>
<p style="color:#64748b">This recovery link has expired.</p>
</div></body></html>""", status_code=403)

    if not consume_token(token):
        return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Used</title>
<style>body{background:#0f172a;color:#e2e8f0;font-family:system-ui;display:flex;
align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:24px;}
.icon{font-size:48px;margin-bottom:16px;}</style></head>
<body><div><div class="icon">⚠️</div><h2>Already Used</h2>
<p style="color:#64748b">This recovery action was already executed.</p>
</div></body></html>""", status_code=403)

    incident_id = payload.get("incident_id", "unknown")
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("✅ Recovery authorised — %s", incident_id)

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Recovery Authorised</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ background:#0f172a; color:#e2e8f0; font-family:-apple-system,system-ui,sans-serif;
            display:flex; align-items:center; justify-content:center;
            min-height:100vh; padding:24px; }}
    .card {{ background:#1e293b; border-radius:20px; padding:40px 32px;
             max-width:340px; width:100%; text-align:center;
             box-shadow:0 20px 40px rgba(0,0,0,0.4); }}
    .icon {{ font-size:56px; margin-bottom:16px; animation:pop .4s ease-out; }}
    @keyframes pop {{ 0%{{transform:scale(0.5);opacity:0}} 80%{{transform:scale(1.1)}} 100%{{transform:scale(1);opacity:1}} }}
    h1 {{ font-size:20px; font-weight:700; color:#f8fafc; margin-bottom:6px; }}
    p  {{ font-size:13px; color:#64748b; margin-bottom:24px; }}
    .score-wrap {{ background:#0f172a; border-radius:14px; padding:20px; margin-bottom:20px; }}
    .score {{ font-size:56px; font-weight:800; color:#16a34a; line-height:1; transition:color .5s; }}
    .score-label {{ font-size:12px; color:#475569; margin-top:4px; }}
    .bar-wrap {{ background:#1e293b; border-radius:8px; height:6px; margin:14px 0 6px; overflow:hidden; }}
    .bar {{ height:100%; background:#16a34a; border-radius:8px; width:0%; transition:width 2s ease; }}
    .bar-label {{ font-size:11px; color:#475569; }}
    .badge {{ display:inline-block; background:#16a34a; color:white; font-size:11px;
              font-weight:700; padding:4px 14px; border-radius:20px; margin-bottom:16px; }}
    .inc {{ font-size:11px; color:#334155; word-break:break-all; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Recovery Authorised</h1>
    <p>System is stabilising</p>
    <div class="score-wrap">
      <div class="score" id="score">–</div>
      <div class="score-label">Health Score / 100</div>
      <div class="bar-wrap"><div class="bar" id="bar"></div></div>
      <div class="bar-label" id="bar-label">Recovering...</div>
    </div>
    <div class="badge">EXECUTED</div>
    <div class="inc">Incident: {incident_id}</div>
  </div>
  <script>
    async function refresh() {{
      try {{
        const r  = await fetch('/health/alerts');
        const d  = await r.json();
        const s  = d?.health_score?.score;
        const st = d?.health_score?.status || '';
        if (s != null) {{
          const v = Math.round(s);
          document.getElementById('score').textContent = v;
          document.getElementById('bar').style.width = v + '%';
          document.getElementById('score').style.color =
            st === 'healthy' ? '#16a34a' : st === 'critical' ? '#dc2626' : '#d97706';
          document.getElementById('bar-label').textContent =
            st === 'healthy' ? 'System healthy ✓' : 'Recovering...';
        }}
      }} catch(e) {{}}
    }}
    refresh();
    setTimeout(() => {{ document.getElementById('bar').style.width = '60%'; }}, 200);
    setInterval(refresh, 3000);
  </script>
</body>
</html>""")


# ── Control panel ──────────────────────────────────────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
def demo_panel():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AlertEngine Lite</title>
  <style>
    * { box-sizing:border-box; margin:0; padding:0; }
    body { background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif;
           display:flex; flex-direction:column; align-items:center;
           min-height:100vh; padding:40px 20px; }
    h1   { font-size:22px; margin-bottom:4px; color:#f8fafc; }
    .sub { color:#64748b; font-size:12px; margin-bottom:28px; }
    a    { color:#38bdf8; }
    .btn { display:block; width:320px; padding:14px; margin:8px 0;
           border:none; border-radius:10px; font-size:15px; font-weight:600;
           cursor:pointer; transition:opacity .15s; }
    .btn:hover { opacity:.85; }
    .fail    { background:#dc2626; color:#fff; }
    .degrade { background:#d97706; color:#fff; }
    .recover { background:#16a34a; color:#fff; }
    .health  { background:#1e293b; border-radius:12px; padding:24px 20px;
               margin-top:20px; width:320px; text-align:center; }
    .score-big { font-size:64px; font-weight:800; margin:8px 0 4px; }
    .trend     { font-size:12px; color:#64748b; margin-bottom:10px; }
    .pill { display:inline-block; padding:3px 14px; border-radius:20px;
            font-size:11px; font-weight:700; letter-spacing:.04em; }
    .pill-none      { background:#1e293b; color:#475569; border:1px solid #334155; }
    .pill-detected  { background:#d97706; color:#fff; }
    .pill-proposed  { background:#7c3aed; color:#fff; }
    .pill-validated { background:#0284c7; color:#fff; }
    .pill-executed  { background:#16a34a; color:#fff; }
    .green { color:#16a34a; }
    .yellow{ color:#d97706; }
    .red   { color:#dc2626; }
    .log { background:#1e293b; padding:12px 14px; border-radius:10px; margin-top:12px;
           font-family:monospace; font-size:11px; width:320px; min-height:40px;
           color:#94a3b8; white-space:pre-wrap; }
  </style>
</head>
<body>
  <h1>⚡ AlertEngine Lite</h1>
  <div class="sub">
    <a href="/health/alerts" target="_blank">/health/alerts</a> ·
    <a href="/status" target="_blank">/status</a>
  </div>

  <button class="btn degrade" onclick="post('/demo/degrade')">🟡 Degrade — Latency only</button>
  <button class="btn fail"    onclick="post('/demo/fail')">🔴 Full Failure — Latency + Errors</button>
  <button class="btn recover" onclick="post('/demo/recover')">🟢 Recover — Reset system</button>

  <div class="health">
    <div class="score-big green" id="score">–</div>
    <div class="trend" id="trend">loading...</div>
    <div class="pill pill-none" id="stage-pill">no incident</div>
  </div>

  <div class="log" id="log">Waiting for events...</div>

  <script>
    const STAGE_CLASS = {
      detected:  'pill-detected',
      proposed:  'pill-proposed',
      validated: 'pill-validated',
      executed:  'pill-executed',
    };

    async function post(url) {
      const r = await fetch(url, { method: 'POST' });
      const d = await r.json();
      document.getElementById('log').textContent = JSON.stringify(d, null, 2);
    }

    async function poll() {
      try {
        const [hr, sr] = await Promise.all([
          fetch('/health/alerts'),
          fetch('/status'),
        ]);
        const hd = await hr.json();
        const sd = await sr.json();
        const hs = hd?.health_score || {};
        const s  = hs.score != null ? Math.round(hs.score) : '–';
        const st = hs.status || 'unknown';
        const tr = hs.trend  || '–';
        const el = document.getElementById('score');
        el.textContent = s;
        el.className   = 'score-big ' + (st==='healthy'?'green':st==='critical'?'red':'yellow');
        document.getElementById('trend').textContent = `${st} · ${tr}`;
        const stage = sd.stage || null;
        const pill  = document.getElementById('stage-pill');
        pill.textContent = stage || 'no incident';
        pill.className   = 'pill ' + (stage ? (STAGE_CLASS[stage] || 'pill-none') : 'pill-none');
      } catch(e) {}
    }

    poll();
    setInterval(poll, 2000);
  </script>
</body>
</html>""")