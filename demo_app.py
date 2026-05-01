# demo_app.py
"""
AlertEngine Lite — SIMULATION LAYER ONLY.

Responsibilities:
- Generate synthetic API traffic
- Simulate failure conditions
- Visualise orchestrator pipeline state

MUST NOT contain:
- State machine logic
- Notification sending
- Incident memory
- Recovery token generation

The orchestrator owns all of that.
"""

import asyncio
import logging
import os
import random

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fastapi_alertengine import instrument

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("demo")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:9000")

# ── Failure switch ─────────────────────────────────────────────────────────────

_FAIL = {
    "enabled":       False,
    "latency_boost": 0.0,
    "error_rate":    0.0,
}

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="AlertEngine Demo — Simulation Layer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = instrument(app, health_path="/health/alerts")
app.mount("/sim", StaticFiles(directory=".", html=True), name="sim")


# ── Payment endpoint (simulated) ───────────────────────────────────────────────

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
    logger.warning("🟡 DEGRADE MODE ON")
    return {"status": "DEGRADED"}


@app.post("/demo/recover")
def demo_recover():
    _FAIL.update({"enabled": False, "latency_boost": 0.0, "error_rate": 0.0})
    logger.info("🟢 MANUAL RECOVERY")
    return {"status": "RECOVERED"}


# ── Control panel (reads state from orchestrator) ──────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
def demo_panel():
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AlertEngine — Simulation Layer</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif;
            display:flex; flex-direction:column; align-items:center;
            min-height:100vh; padding:40px 20px; }}
    h1   {{ font-size:22px; margin-bottom:4px; color:#f8fafc; }}
    .sub {{ color:#64748b; font-size:12px; margin-bottom:8px; }}
    .layer-badge {{ display:inline-block; background:#334155; color:#94a3b8;
                   font-size:10px; font-weight:700; padding:2px 10px;
                   border-radius:12px; margin-bottom:24px; letter-spacing:.05em; }}
    a    {{ color:#38bdf8; }}
    .btn {{ display:block; width:320px; padding:14px; margin:8px 0;
            border:none; border-radius:10px; font-size:15px; font-weight:600;
            cursor:pointer; transition:opacity .15s; }}
    .btn:hover {{ opacity:.85; }}
    .fail    {{ background:#dc2626; color:#fff; }}
    .degrade {{ background:#d97706; color:#fff; }}
    .recover {{ background:#16a34a; color:#fff; }}
    .health  {{ background:#1e293b; border-radius:12px; padding:24px 20px;
                margin-top:20px; width:320px; text-align:center; }}
    .score-big {{ font-size:64px; font-weight:800; margin:8px 0 4px; }}
    .trend     {{ font-size:12px; color:#64748b; margin-bottom:10px; }}
    .pill {{ display:inline-block; padding:3px 14px; border-radius:20px;
             font-size:11px; font-weight:700; letter-spacing:.04em; }}
    .pill-none       {{ background:#1e293b; color:#475569; border:1px solid #334155; }}
    .pill-detected   {{ background:#d97706; color:#fff; }}
    .pill-proposed   {{ background:#7c3aed; color:#fff; }}
    .pill-validated  {{ background:#0284c7; color:#fff; }}
    .pill-authorized {{ background:#16a34a; color:#fff; }}
    .pill-executed   {{ background:#16a34a; color:#fff; }}
    .pill-resolved   {{ background:#16a34a; color:#fff; }}
    .green {{ color:#16a34a; }}
    .yellow{{ color:#d97706; }}
    .red   {{ color:#dc2626; }}
    .log {{ background:#1e293b; padding:12px 14px; border-radius:10px; margin-top:12px;
            font-family:monospace; font-size:11px; width:320px; min-height:40px;
            color:#94a3b8; white-space:pre-wrap; }}
  </style>
</head>
<body>
  <h1>⚡ AlertEngine Lite</h1>
  <div class="sub">
    <a href="/health/alerts" target="_blank">/health/alerts</a> ·
    <a href="/sim/whatsapp_sim.html" target="_blank">📱 Simulator</a>
  </div>
  <div class="layer-badge">SIMULATION LAYER</div>

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
    const STAGE_CLASS = {{
      detected:   'pill-detected',
      proposed:   'pill-proposed',
      validated:  'pill-validated',
      authorized: 'pill-authorized',
      executed:   'pill-executed',
      resolved:   'pill-resolved',
    }};

    async function post(url) {{
      const r = await fetch(url, {{ method: 'POST' }});
      const d = await r.json();
      document.getElementById('log').textContent = JSON.stringify(d, null, 2);
    }}

    async function poll() {{
      try {{
        // Health from AlertEngine
        const hr = await fetch('/health/alerts');
        const hd = await hr.json();
        const hs = hd?.health_score || {{}};
        const s  = hs.score != null ? Math.round(hs.score) : '–';
        const st = hs.status || 'unknown';
        const tr = hs.trend  || '–';
        const el = document.getElementById('score');
        el.textContent = s;
        el.className   = 'score-big ' + (st==='healthy'?'green':st==='critical'?'red':'yellow');
        document.getElementById('trend').textContent = `${{st}} · ${{tr}}`;

        // Pipeline stage from orchestrator
        try {{
          const or_ = await fetch('{ORCHESTRATOR_URL}/status');
          const od  = await or_.json();
          const stage = od.stage || null;
          const pill  = document.getElementById('stage-pill');
          pill.textContent = stage || 'no incident';
          pill.className   = 'pill ' + (stage ? (STAGE_CLASS[stage] || 'pill-none') : 'pill-none');
        }} catch(e) {{
          // Orchestrator may not be running in local dev
          document.getElementById('stage-pill').textContent = 'orchestrator offline';
        }}
      }} catch(e) {{}}
    }}

    poll();
    setInterval(poll, 2000);
  </script>
</body>
</html>""")
