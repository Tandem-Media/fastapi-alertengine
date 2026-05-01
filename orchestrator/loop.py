# orchestrator/loop.py
"""
Stateless polling executor — Pass 2 hardened.

New in Pass 2:
- Distributed locking (one worker per incident)
- Idempotent action execution
- Audit log on every transition
- DLQ on permanent action failure
- Degraded mode awareness
- Structured observability logs

loop.py remains dumb: fetch → lock → decide → execute → release.
All business logic stays in pipeline.py.
"""

import asyncio
import logging
import os
import time

import httpx

from pipeline import (
    open_incident,
    decide,
    decide_new_incident,
    apply_transition,
    validate_decision_schema,
)
from memory import (
    get_active_incident,
    save_incident,
    resolve_incident,
)
from notifications import (
    fire,
    send_detection,
    send_validation,
    send_recovery,
    send_voice_escalation,
    send_secondary_escalation,
)
from action_generator import generate_recovery_token
from claude_engine import get_decision as claude_decide
from policy import should_alert, should_escalate_voice, should_escalate_secondary
from lock import incident_lock
from idempotency import execute_once, make_action_id
from audit import append_event
from dlq import push as dlq_push
from degraded import (
    current_mode, can_mutate_state, can_escalate,
    can_send_notifications, record_redis_failure,
    record_notify_failure, record_success,
)

logger = logging.getLogger("orchestrator.loop")

ALERTENGINE_URL   = os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000")
ACTION_BASE_URL   = os.getenv("ACTION_BASE_URL", ALERTENGINE_URL)
LOOP_INTERVAL_S   = float(os.getenv("LOOP_INTERVAL_S", "5"))
VOICE_AFTER_S     = float(os.getenv("VOICE_AFTER_S", "180"))
SECONDARY_AFTER_S = float(os.getenv("SECONDARY_AFTER_S", "300"))


# ── AlertEngine fetch ──────────────────────────────────────────────────────────

async def _fetch_health() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{ALERTENGINE_URL}/health/alerts")
            if r.status_code == 200:
                record_success()
                return r.json()
            logger.warning("AlertEngine returned %d", r.status_code)
    except Exception as e:
        logger.error("Health fetch failed: %s", e)
        record_redis_failure()
    return None


# ── Idempotent notification dispatcher ────────────────────────────────────────

async def _notify_once(
    incident_id: str,
    stage: str,
    action_type: str,
    coro_fn,
    *args,
    **kwargs,
) -> None:
    """
    Fire notification exactly once per (incident, stage, action_type).
    Pushes to DLQ on permanent failure.
    """
    action_id = make_action_id(incident_id, stage, action_type)

    try:
        executed, _ = await execute_once(
            incident_id, stage, action_type,
            coro_fn, *args, **kwargs
        )
        if executed:
            logger.info("Notification sent: %s | %s | action_id=%s",
                        incident_id, action_type, action_id)
        else:
            logger.info("Notification skipped (idempotent): %s | %s | action_id=%s",
                        incident_id, action_type, action_id)
    except Exception as e:
        record_notify_failure()
        dlq_push(
            incident_id=incident_id,
            action_type=action_type,
            error=str(e),
            stage=stage,
            action_id=action_id,
        )
        logger.error("Notification failed → DLQ: %s | %s | %s",
                     incident_id, action_type, e)


# ── Action executor ────────────────────────────────────────────────────────────

async def _execute_actions(
    actions: list,
    incident: dict,
    health: dict,
) -> dict:
    """
    Execute all actions from pipeline decision.
    Idempotent. DLQ on failure. Degraded-mode aware.
    """
    incident_id = incident.get("incident_id", "unknown")
    stage       = incident.get("stage", "UNKNOWN")
    score       = health.get("health_score", {}).get("score", 100)
    p95         = health.get("metrics", {}).get("overall_p95_ms", 0)
    err         = health.get("metrics", {}).get("error_rate", 0)

    for action in actions:
        action_type = action.get("type")
        payload     = action.get("payload", {})

        if action_type == "SEND_NOTIFICATION":
            if not can_send_notifications():
                logger.warning("EMERGENCY mode: notification suppressed | %s", incident_id)
                continue

            notif_type = payload.get("type")
            if notif_type == "CRITICAL":
                fire(_notify_once(incident_id, stage, "SEND_DETECTION",
                                  send_detection, incident_id, score, p95, err))
            elif notif_type == "VALIDATION":
                url = incident.get("recovery_url", "")
                fire(_notify_once(incident_id, stage, "SEND_VALIDATION",
                                  send_validation, incident_id, score, p95, url))
            elif notif_type == "RECOVERY":
                duration = time.time() - incident.get("started_at", time.time())
                fire(_notify_once(incident_id, stage, "SEND_RECOVERY",
                                  send_recovery, incident_id, score, duration))

        elif action_type == "GENERATE_TOKEN":
            if not can_mutate_state():
                logger.warning("EMERGENCY mode: token generation suppressed")
                continue
            token = generate_recovery_token(incident_id)
            url   = f"{ACTION_BASE_URL}/action/recover?token={token}"
            incident = {**incident, "token": token, "recovery_url": url}
            logger.info("Token generated: %s | action_id=%s",
                        incident_id,
                        make_action_id(incident_id, stage, "GENERATE_TOKEN"))

        elif action_type == "TRIGGER_RECOVERY":
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(
                        f"{ACTION_BASE_URL}/action/recover",
                        params={"token": incident.get("token", "")},
                    )
                    logger.info("Recovery triggered: %d | %s", r.status_code, incident_id)
            except Exception as e:
                dlq_push(incident_id, "TRIGGER_RECOVERY", str(e), stage)
                logger.error("Recovery trigger failed → DLQ: %s", e)

        elif action_type == "ESCALATE":
            if not can_escalate():
                logger.warning("Mode %s: escalation suppressed | %s",
                               current_mode(), incident_id)
                continue
            duration = time.time() - incident.get("started_at", time.time())
            fire(send_voice_escalation(incident_id, duration, score))

    return incident


# ── Main loop iteration ────────────────────────────────────────────────────────

async def _run_once() -> None:
    mode = current_mode()

    # Step 1: Fetch health
    health = await _fetch_health()
    if not health:
        logger.warning("No health data — skipping [mode=%s]", mode)
        return

    hs     = health.get("health_score", {})
    m      = health.get("metrics", {})
    status = hs.get("status", "healthy")
    score  = hs.get("score", 100)
    p95    = m.get("overall_p95_ms", 0)
    err    = m.get("error_rate", 0)

    logger.info("Health: %s | score=%.0f p95=%.0fms err=%.1f%% | mode=%s",
                status, score, p95, err * 100, mode)

    # Step 2: Load incident
    incident = get_active_incident()
    now      = time.time()

    # Step 3: New incident
    if status == "critical" and incident is None:
        if not can_mutate_state():
            logger.warning("EMERGENCY mode: new incident suppressed")
            return

        claude = await claude_decide(health, incident=None)
        logger.info("Claude: %s (%.0f%%) — %s",
                    claude["action"], claude["confidence"] * 100, claude["reason"])

        if not should_alert(score, err):
            return
        if claude["action"] not in ("escalate", "validate"):
            return

        incident_id = f"inc-{int(now)}"
        decision    = decide_new_incident(incident_id, score, p95, err, claude["confidence"])

        valid, reason = validate_decision_schema(decision)
        if not valid:
            logger.error("Invalid schema: %s", reason)
            return

        incident_record = open_incident(incident_id, score, p95, err)
        if not save_incident(incident_record):
            record_redis_failure()
            logger.error("Failed to save incident — aborting")
            return

        append_event(
            incident_id=incident_id,
            stage="DETECTED",
            decision=claude["action"],
            reason=decision["reason"],
            confidence=decision["confidence"],
        )

        await _execute_actions(decision["actions"], incident_record, health)
        return

    if incident is None:
        return

    incident_id = incident["incident_id"]

    # Step 4: Acquire distributed lock
    async with incident_lock(incident_id) as acquired:
        if not acquired:
            logger.debug("Lock not acquired for %s — skipping cycle", incident_id)
            return

        # Step 5: Recovery
        if status in ("healthy", "degraded") and incident.get("stage") != "RECOVERED":
            claude   = await claude_decide(health, incident=incident)
            decision = decide(incident, health, claude)

            valid, reason = validate_decision_schema(decision)
            if not valid:
                logger.error("Invalid recovery decision: %s", reason)
                return

            if decision.get("next_stage") == "RECOVERED":
                if not can_mutate_state():
                    logger.warning("EMERGENCY: recovery transition suppressed")
                    return
                updated = apply_transition(incident, "RECOVERED")
                save_incident(updated)
                resolve_incident(incident_id)
                append_event(
                    incident_id=incident_id,
                    stage="RECOVERED",
                    decision=claude["action"],
                    reason=decision["reason"],
                    confidence=decision["confidence"],
                )
                await _execute_actions(decision["actions"], updated, health)
            return

        # Step 6: Pipeline decision
        claude   = await claude_decide(health, incident=incident)
        decision = decide(incident, health, claude)

        logger.info("Decision: %s → %s | notify=%s | mode=%s",
                    decision.get("current_stage"),
                    decision.get("next_stage"),
                    decision.get("should_notify"),
                    mode)

        valid, reason = validate_decision_schema(decision)
        if not valid:
            logger.warning("Invalid decision schema: %s — skipping", reason)
            return

        next_stage = decision.get("next_stage")
        if not next_stage:
            return

        if not can_mutate_state():
            logger.warning("EMERGENCY: transition suppressed for %s", incident_id)
            return

        updated = apply_transition(incident, next_stage)
        updated = await _execute_actions(decision["actions"], updated, health)
        save_incident(updated)

        append_event(
            incident_id=incident_id,
            stage=next_stage,
            decision=claude["action"],
            reason=decision["reason"],
            confidence=decision["confidence"],
            action_id=make_action_id(incident_id, next_stage, "TRANSITION"),
        )

        # Step 7: Escalations
        duration = now - incident.get("started_at", now)

        if not incident.get("voice_sent") and should_escalate_voice(duration, score):
            if can_escalate():
                updated["voice_sent"] = True
                save_incident(updated)
                fire(_notify_once(incident_id, next_stage, "VOICE_ESCALATION",
                                  send_voice_escalation, incident_id, duration, score))

        if not incident.get("secondary_sent") and should_escalate_secondary(duration, score):
            if can_escalate():
                updated["secondary_sent"] = True
                save_incident(updated)
                fire(_notify_once(incident_id, next_stage, "SECONDARY_ESCALATION",
                                  send_secondary_escalation, incident_id, duration, score))


# ── Loop runner ────────────────────────────────────────────────────────────────

async def run_loop() -> None:
    logger.info("📡 Orchestrator loop started (interval=%.0fs)", LOOP_INTERVAL_S)
    while True:
        try:
            await _run_once()
        except Exception as e:
            logger.error("Loop error: %s", e)
        await asyncio.sleep(LOOP_INTERVAL_S)
