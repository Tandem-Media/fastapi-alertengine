# orchestrator/loop.py
"""
Multi-tenant stateless polling executor.

Per loop tick:
1. Fetch all active tenants from Redis
2. For each tenant:
   a. Acquire distributed lock
   b. Fetch health from tenant's health_url
   c. Load tenant incident from Redis
   d. Call Claude for decision
   e. Execute actions via pipeline
   f. Notify tenant contacts
   g. Release lock

No decision logic here. No global state. No blocking.
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
    save_incident,
    resolve_incident,
    append_audit,
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
from policy import (
    should_alert,
    should_escalate_voice,
    should_escalate_secondary,
    should_open_new_incident,
)
from lock import incident_lock, renew_lock
from idempotency import execute_once, make_action_id, is_executed, mark_executed
from audit import append_event
from dlq import push as dlq_push
from degraded import (
    current_mode, can_mutate_state, can_escalate,
    can_send_notifications, record_redis_failure,
    record_notify_failure, record_success,
    record_health_fetch_failure,
)
from tenants import list_active_tenants, get_verified_numbers, save_tenant
from plans import (
    get_tenant_plan,
    can_monitor_more_services,
    incident_quota_remaining,
    increment_incident_count,
)

logger = logging.getLogger("orchestrator.loop")

ACTION_BASE_URL   = os.getenv("ACTION_BASE_URL", os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000"))
LOOP_INTERVAL_S   = float(os.getenv("LOOP_INTERVAL_S", "5"))
VOICE_AFTER_S     = float(os.getenv("VOICE_AFTER_S", "180"))
SECONDARY_AFTER_S = float(os.getenv("SECONDARY_AFTER_S", "300"))


# ── Tenant health fetch ────────────────────────────────────────────────────────

async def _fetch_health(health_url: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(health_url)
            if r.status_code == 200:
                record_success()
                return r.json()
            logger.warning("Health fetch %s returned %d", health_url, r.status_code)
    except Exception as e:
        logger.error("Health fetch failed %s: %s", health_url, e)
        record_health_fetch_failure()
    return None


# ── Tenant incident key ────────────────────────────────────────────────────────

def _get_tenant_incident(tenant_id: str) -> dict | None:
    """Load active incident for a specific tenant."""
    try:
        import redis, json, os
        r   = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        key = f"orchestrator:active_incident:{tenant_id}"
        incident_id = r.get(key)
        if not incident_id:
            return None
        data = r.get(f"orchestrator:incident:{incident_id}")
        return json.loads(data) if data else None
    except Exception as e:
        logger.error("_get_tenant_incident failed: %s", e)
        return None


def _save_tenant_active(tenant_id: str, incident_id: str) -> None:
    try:
        import redis, os
        r = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        r.setex(f"orchestrator:active_incident:{tenant_id}", 86400, incident_id)
    except Exception as e:
        logger.error("_save_tenant_active failed: %s", e)


def _clear_tenant_active(tenant_id: str) -> None:
    try:
        import redis, os
        r = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        r.delete(f"orchestrator:active_incident:{tenant_id}")
    except Exception as e:
        logger.error("_clear_tenant_active failed: %s", e)


# ── Notification dispatcher (tenant-aware) ────────────────────────────────────

async def _notify_tenant(
    tenant_id: str,
    incident_id: str,
    stage: str,
    action_type: str,
    coro_fn,
    *args,
    **kwargs,
) -> None:
    """Send notification to all verified contacts for a tenant."""
    action_id = make_action_id(incident_id, stage, action_type)
    try:
        executed, _ = await execute_once(
            incident_id, stage, action_type, coro_fn, *args, **kwargs
        )
        if not executed:
            logger.info("Notification skipped (idempotent): %s | %s", incident_id, action_type)
    except Exception as e:
        record_notify_failure()
        dlq_push(incident_id=incident_id, action_type=action_type,
                 error=str(e), stage=stage, action_id=action_id)
        logger.error("Notification failed → DLQ: %s | %s | %s", incident_id, action_type, e)


# ── Tenant-aware notification router ──────────────────────────────────────────

async def _send_tenant_notification(
    tenant: dict,
    notification_type: str,
    incident_id: str,
    score: float,
    p95: float = 0.0,
    err: float = 0.0,
    recovery_url: str = "",
    duration_s: float = 0.0,
) -> bool:
    """
    Route notification based on tenant channel and type.
    """
    channel = tenant.get("notification_channel", "whatsapp")

    if channel == "telegram":
        from telegram_notifier import (
            send_telegram_detection,
            send_telegram_validation,
            send_telegram_recovery,
        )
        bot_token = tenant.get("telegram_bot_token")
        chat_id   = tenant.get("telegram_chat_id")

        if notification_type == "DETECTION":
            return await send_telegram_detection(
                bot_token, chat_id, incident_id, score, p95, err)
        elif notification_type == "VALIDATION":
            return await send_telegram_validation(
                bot_token, chat_id, incident_id, score, p95, recovery_url)
        elif notification_type == "RECOVERY":
            return await send_telegram_recovery(
                bot_token, chat_id, incident_id, score, duration_s)
        return False

    # WhatsApp — use existing functions
    if notification_type == "DETECTION":
        return await send_detection(incident_id, score, p95, err)
    elif notification_type == "VALIDATION":
        return await send_validation(incident_id, score, p95, recovery_url)
    elif notification_type == "RECOVERY":
        return await send_recovery(incident_id, score, duration_s)
    return False


# ── Action executor ────────────────────────────────────────────────────────────

async def _execute_actions(
    actions: list,
    incident: dict,
    health: dict,
    tenant: dict,
) -> dict:
    tenant_id   = tenant["tenant_id"]
    incident_id = incident.get("incident_id", "unknown")
    stage       = incident.get("stage", "UNKNOWN")
    score       = health.get("health_score", {}).get("score", 100)
    p95         = health.get("metrics", {}).get("overall_p95_ms", 0)
    err         = health.get("metrics", {}).get("error_rate", 0)

    for action in actions:
        action_type = action.get("type")

        if action_type == "SEND_NOTIFICATION":
            if not can_send_notifications():
                logger.warning("EMERGENCY: notification suppressed | %s", incident_id)
                continue
            notif_type = action.get("payload", {}).get("type")
            if notif_type == "CRITICAL":
                fire(_notify_tenant(tenant_id, incident_id, stage, "SEND_DETECTION",
                                    _send_tenant_notification, tenant, "DETECTION",
                                    incident_id, score, p95, err))
            elif notif_type == "VALIDATION":
                url = incident.get("recovery_url", "")
                fire(_notify_tenant(tenant_id, incident_id, stage, "SEND_VALIDATION",
                                    _send_tenant_notification, tenant, "VALIDATION",
                                    incident_id, score, p95, 0.0, url))
            elif notif_type == "RECOVERY":
                duration = time.time() - incident.get("started_at", time.time())
                fire(_notify_tenant(tenant_id, incident_id, stage, "SEND_RECOVERY",
                                    _send_tenant_notification, tenant, "RECOVERY",
                                    incident_id, score, duration_s=duration))

        elif action_type == "GENERATE_TOKEN":
            if not can_mutate_state():
                continue
            token = generate_recovery_token(incident_id, tenant_id=tenant_id)
            url   = f"{ACTION_BASE_URL}/action/recover?token={token}"
            incident = {**incident, "token": token, "recovery_url": url}

        elif action_type == "ESCALATE":
            plan = get_tenant_plan(tenant)
            if not plan.has_voice_escalation:
                logger.warning("[%s] Voice escalation not available on plan %s",
                               tenant_id, tenant.get("plan", "solo"))
                continue
            if not can_escalate():
                continue
            duration = time.time() - incident.get("started_at", time.time())
            fire(send_voice_escalation(incident_id, duration, score))

    return incident


# ── Single tenant processing ───────────────────────────────────────────────────

async def _process_tenant(tenant: dict) -> None:
    tenant_id  = tenant["tenant_id"]
    health_url = tenant["health_url"]
    mode       = current_mode()

    # Fetch health from tenant's own endpoint
    health = await _fetch_health(health_url)
    if not health:
        return

    hs     = health.get("health_score", {})
    m      = health.get("metrics", {})
    status = hs.get("status", "healthy")
    score  = hs.get("score", 100)
    p95    = m.get("overall_p95_ms", 0)
    err    = m.get("error_rate", 0)
    now    = time.time()

    logger.info("[%s] Health: %s | score=%.0f | mode=%s",
                tenant_id, status, score, mode)

    incident = _get_tenant_incident(tenant_id)

    # New critical incident
    if status == "critical" and incident is None:
        if not can_mutate_state():
            return

        creation_lock_key = f"creating-{tenant_id}"
        async with incident_lock(creation_lock_key, ttl=10) as token:
            if not token:
                return

            existing_incident = _get_tenant_incident(tenant_id)
            if not should_open_new_incident(existing_incident):
                return

            incident_id = f"inc-{tenant_id}-{int(now)}"
            creation_key = make_action_id(
                incident_id, "DETECTED", "OPEN_INCIDENT"
            )
            if is_executed(creation_key):
                logger.info("Incident creation already executed: %s", incident_id)
                return

            mark_executed(creation_key, {
                "tenant_id": tenant_id,
                "incident_id": incident_id,
            })

            if not should_alert(score, err):
                return

            # Plan gate 1: service limit
            if not can_monitor_more_services(tenant):
                logger.warning("[%s] Service limit reached for plan %s — incident suppressed",
                               tenant_id, tenant.get("plan", "solo"))
                return

            # Plan gate 2: incident quota
            quota = incident_quota_remaining(tenant)
            if quota == 0:
                logger.warning("[%s] Incident quota exhausted for plan %s — incident suppressed",
                               tenant_id, tenant.get("plan", "solo"))
                return

            # Plan gate 3: Claude decision feature flag
            plan = get_tenant_plan(tenant)
            if not plan.has_claude_decision:
                logger.warning("[%s] Claude decisions not available on plan %s",
                               tenant_id, tenant.get("plan", "solo"))
                return

            if not renew_lock(creation_lock_key, token):
                logger.warning("Lock renewal failed for %s — skipping cycle", creation_lock_key)
                return

            claude = await claude_decide(health, incident=None)
            if claude["action"] not in ("escalate", "validate"):
                return

            decision        = decide_new_incident(incident_id, score, p95, err, claude["confidence"])
            valid, reason   = validate_decision_schema(decision)
            if not valid:
                logger.error("[%s] Invalid schema: %s", tenant_id, reason)
                return

            incident_record = open_incident(incident_id, score, p95, err)
            incident_record["tenant_id"] = tenant_id
            if not save_incident(incident_record):
                logger.error("[%s] save_incident failed — aborting incident creation", tenant_id)
                return
            _save_tenant_active(tenant_id, incident_id)
            updated_tenant = increment_incident_count(tenant)
            save_tenant(updated_tenant)

            append_event(incident_id=incident_id, stage="DETECTED",
                         decision=claude["action"], reason=decision["reason"],
                         confidence=decision["confidence"],
                         tenant_id=tenant.get("tenant_id"))

            await _execute_actions(decision["actions"], incident_record, health, tenant)
        return

    if incident is None:
        return

    incident_id = incident["incident_id"]

    # Acquire lock per tenant incident
    async with incident_lock(incident_id) as token:
        if not token:
            return

        if not renew_lock(incident_id, token):
            logger.warning("Lock renewal failed for %s — skipping cycle", incident_id)
            return

        # Recovery
        if status in ("healthy", "degraded") and incident.get("stage") != "RECOVERED":
            claude   = await claude_decide(health, incident=incident)
            decision = decide(incident, health, claude)
            valid, reason = validate_decision_schema(decision)
            if not valid:
                return

            if decision.get("next_stage") == "RECOVERED":
                if not can_mutate_state():
                    return
                updated = apply_transition(incident, "RECOVERED")
                save_incident(updated)
                resolve_incident(incident_id)
                _clear_tenant_active(tenant_id)
                append_event(incident_id=incident_id, stage="RECOVERED",
                             decision=claude["action"], reason=decision["reason"],
                             confidence=decision["confidence"],
                             tenant_id=tenant.get("tenant_id"))
                await _execute_actions(decision["actions"], updated, health, tenant)
            return

        # Pipeline advance
        claude   = await claude_decide(health, incident=incident)
        decision = decide(incident, health, claude)
        valid, reason = validate_decision_schema(decision)
        if not valid:
            return

        next_stage = decision.get("next_stage")
        if not next_stage or not can_mutate_state():
            return

        updated = apply_transition(incident, next_stage)
        updated = await _execute_actions(decision["actions"], updated, health, tenant)
        save_incident(updated)
        append_event(incident_id=incident_id, stage=next_stage,
                     decision=claude["action"], reason=decision["reason"],
                     confidence=decision["confidence"],
                     action_id=make_action_id(incident_id, next_stage, "TRANSITION"),
                     tenant_id=tenant.get("tenant_id"))

        # Escalations
        duration = now - incident.get("started_at", now)
        if not incident.get("voice_sent") and should_escalate_voice(duration, score):
            if can_escalate():
                updated["voice_sent"] = True
                save_incident(updated)
                fire(_notify_tenant(tenant_id, incident_id, next_stage, "VOICE",
                                    send_voice_escalation, incident_id, duration, score))

        if not incident.get("secondary_sent") and should_escalate_secondary(duration, score):
            if can_escalate():
                updated["secondary_sent"] = True
                save_incident(updated)
                fire(_notify_tenant(tenant_id, incident_id, next_stage, "SECONDARY",
                                    send_secondary_escalation, incident_id, duration, score))


# ── Main loop ──────────────────────────────────────────────────────────────────

async def _run_once() -> None:
    tenants = list_active_tenants()
    if not tenants:
        logger.debug("No active tenants")
        return

    # Process all tenants concurrently
    await asyncio.gather(
        *[_process_tenant(t) for t in tenants],
        return_exceptions=True,
    )


async def run_loop() -> None:
    logger.info("📡 Multi-tenant loop started (interval=%.0fs)", LOOP_INTERVAL_S)
    while True:
        try:
            await _run_once()
        except Exception as e:
            logger.error("Loop error: %s", e)
        await asyncio.sleep(LOOP_INTERVAL_S)
