# orchestrator/loop.py
"""
Orchestrator polling loop.

Responsibilities:
- Poll AlertEngine /health/alerts
- Load active incident from Redis
- Call Claude for decisioning
- Delegate transitions to pipeline.py
- Delegate notifications to notifications.py
- Persist state to memory.py

Rules:
- No business logic here
- No timing heuristics
- No direct notification calls (delegates only)
- Loop body must complete in < loop_interval seconds
"""

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

from pipeline import (
    new_incident,
    transition,
    next_required_stage,
    stage_age,
    incident_duration,
    is_terminal,
)
from memory import (
    get_active_incident,
    save_incident,
    resolve_incident,
    get_active_incident_id,
)
from notifications import (
    fire,
    send_detection,
    send_validation,
    send_recovery,
    send_voice_escalation,
    send_secondary_escalation,
)
from claude_engine import get_decision
from policy import should_alert, should_escalate_voice, should_escalate_secondary

logger = logging.getLogger("orchestrator.loop")

ALERTENGINE_URL  = os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000")
ACTION_BASE_URL  = os.getenv("ACTION_BASE_URL", ALERTENGINE_URL)
LOOP_INTERVAL_S  = float(os.getenv("LOOP_INTERVAL_S", "5"))
VOICE_AFTER_S    = float(os.getenv("VOICE_AFTER_S", "180"))
SECONDARY_AFTER_S = float(os.getenv("SECONDARY_AFTER_S", "300"))


# ── AlertEngine client ─────────────────────────────────────────────────────────

async def _fetch_health() -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{ALERTENGINE_URL}/health/alerts")
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.error("Health fetch failed: %s", e)
    return None


# ── Recovery action trigger ────────────────────────────────────────────────────

async def _trigger_recovery(token: str) -> bool:
    """
    Call the recovery endpoint programmatically.
    This is an API call — not UI-only.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{ACTION_BASE_URL}/action/recover",
                params={"token": token},
            )
            ok = r.status_code == 200
            if ok:
                logger.info("Recovery action executed via API")
            else:
                logger.warning("Recovery endpoint returned %d", r.status_code)
            return ok
    except Exception as e:
        logger.error("Recovery trigger failed: %s", e)
        return False


# ── Token generation ───────────────────────────────────────────────────────────

def _generate_recovery_url(incident_id: str) -> tuple[str, str]:
    """Generate signed JWT recovery token and full URL."""
    from action_generator import generate_recovery_token
    token = generate_recovery_token(incident_id)
    url   = f"{ACTION_BASE_URL}/action/recover?token={token}"
    return token, url


# ── Main loop iteration ────────────────────────────────────────────────────────

async def _run_once() -> None:
    # 1. Fetch health from AlertEngine
    health = await _fetch_health()
    if not health:
        logger.warning("No health data — skipping iteration")
        return

    hs     = health.get("health_score", {})
    m      = health.get("metrics", {})
    status = hs.get("status", "healthy")
    score  = hs.get("score", 100)
    p95    = m.get("overall_p95_ms", 0)
    err    = m.get("error_rate", 0)

    logger.info("Health: %s | score=%.0f p95=%.0fms err=%.1f%%",
                status, score, p95, err * 100)

    # 2. Load active incident from Redis
    incident = get_active_incident()
    now      = time.time()

    # 3. New incident detection
    if status == "critical" and incident is None:
        incident_id = f"inc-{int(now)}"

        # Ask Claude whether to open incident
        decision = await get_decision(health, incident=None)
        logger.info("Claude: %s (%.0f%%) — %s",
                    decision["action"], decision["confidence"] * 100, decision["reason"])

        if decision["action"] in ("escalate", "validate") and should_alert(score, err):
            incident = new_incident(incident_id, score, p95, err)
            save_incident(incident)
            logger.warning("🚨 Incident opened: %s", incident_id)
            fire(send_detection(incident_id, score, p95, err))
        else:
            logger.info("Claude suppressed incident: %s", decision["reason"])
        return

    # 4. No active incident and system healthy — nothing to do
    if incident is None:
        return

    incident_id = incident["id"]

    # 5. System recovered
    if status in ("healthy", "degraded") and incident.get("stage") not in ("resolved",):
        duration = incident_duration(incident)
        incident = transition(incident, "resolved", {"score": score})
        save_incident(incident)
        resolve_incident(incident_id)
        logger.info("✅ Resolved: %s (%.0fs)", incident_id, duration)
        fire(send_recovery(incident_id, score, duration))
        return

    # 6. Auto-advance pipeline stages (detected → proposed → validated)
    next_stage = next_required_stage(incident)
    if next_stage:
        if next_stage == "validated":
            # Generate recovery token at validated stage
            token, url = _generate_recovery_url(incident_id)
            incident = transition(incident, "validated", {"url": url})
            incident["token"]        = token
            incident["recovery_url"] = url
            save_incident(incident)
            fire(send_validation(incident_id, score, p95, url))
        else:
            incident = transition(incident, next_stage)
            save_incident(incident)

    # 7. Escalations
    duration = incident_duration(incident)

    if (not incident.get("voice_sent")
            and should_escalate_voice(duration, score)):
        incident["voice_sent"] = True
        save_incident(incident)
        fire(send_voice_escalation(incident_id, duration, score))

    if (not incident.get("secondary_sent")
            and should_escalate_secondary(duration, score)):
        incident["secondary_sent"] = True
        save_incident(incident)
        fire(send_secondary_escalation(incident_id, duration, score))

    # 8. Ask Claude periodically if action needed
    decision = await get_decision(health, incident=incident)
    logger.info("Claude: %s (%.0f%%) — %s",
                decision["action"], decision["confidence"] * 100, decision["reason"])

    if decision["action"] == "recover" and incident.get("token"):
        logger.info("Claude recommends recovery — triggering via API")
        await _trigger_recovery(incident["token"])


# ── Loop runner ────────────────────────────────────────────────────────────────

async def run_loop() -> None:
    logger.info("📡 Orchestrator loop started (interval=%.0fs)", LOOP_INTERVAL_S)
    while True:
        try:
            await _run_once()
        except Exception as e:
            logger.error("Loop error: %s", e)
        await asyncio.sleep(LOOP_INTERVAL_S)
