# orchestrator/notifications.py
"""
Notification system with circuit breaker and fallback channel.

Rules:
- All sending is async-safe (executor-wrapped)
- Circuit breaker: per-provider per-tenant, 3 failures → 60s cooldown
- Fallback webhook fires when primary (WhatsApp) fails
- Never blocks the orchestrator loop
- Never raises — logs and continues
"""

import asyncio
import logging
import os
import time
from typing import Callable, Optional

import circuit_breaker as _cb_module

logger = logging.getLogger("orchestrator.notifications")

# ── Circuit breaker ────────────────────────────────────────────────────────────
# Per-provider per-tenant — a noisy neighbor on one tenant cannot trip
# the breaker for other tenants. Redis-backed, shared across all workers.

def cb_open(provider: str = "whatsapp", tenant_id: str = "global") -> bool:
    return _cb_module.is_open(provider, tenant_id)


def cb_record(success: bool, provider: str = "whatsapp", tenant_id: str = "global") -> None:
    if success:
        _cb_module.record_success(provider, tenant_id)
    else:
        _cb_module.record_failure(provider, tenant_id)


def cb_status(provider: str = "whatsapp", tenant_id: str = "global") -> dict:
    return {
        "open":      cb_open(provider, tenant_id),
        "provider":  provider,
        "tenant_id": tenant_id,
    }


# ── Fallback webhook ───────────────────────────────────────────────────────────

def _send_fallback(subject: str, body: str) -> bool:
    """
    Fallback channel — fires when primary channel fails.
    Configurable via FALLBACK_WEBHOOK_URL env var.
    Supports generic HTTP POST (Slack, Teams, PagerDuty, custom).
    """
    url = os.getenv("FALLBACK_WEBHOOK_URL")
    if not url:
        logger.warning("No FALLBACK_WEBHOOK_URL set — fallback suppressed")
        return False
    try:
        import urllib.request, json
        payload = json.dumps({"text": f"*{subject}*\n{body}"}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            ok = resp.status < 400
            if ok:
                logger.info("Fallback webhook sent: %s", subject)
            return ok
    except Exception as e:
        logger.error("Fallback webhook failed: %s", e)
        return False


# ── Twilio WhatsApp sender ─────────────────────────────────────────────────────

def _twilio_client():
    from twilio.rest import Client
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")
    return Client(sid, token)


def _whatsapp_send(body: str) -> bool:
    from_  = os.getenv("TWILIO_WHATSAPP_FROM")
    to_    = os.getenv("TWILIO_WHATSAPP_TO")
    if not from_ or not to_:
        logger.warning("WhatsApp credentials not configured")
        return False
    try:
        msg = _twilio_client().messages.create(body=body, from_=from_, to=to_)
        logger.info("WhatsApp sent: %s", msg.sid)
        return True
    except Exception as e:
        logger.error("WhatsApp failed: %s", e)
        return False


# ── Core send with fallback ────────────────────────────────────────────────────

def _send_with_fallback(subject: str, body: str, tenant_id: str = "global") -> bool:
    """
    Try WhatsApp first. If circuit breaker is open or send fails,
    fall through to fallback webhook. Silence is never acceptable.
    Circuit breaker is scoped per-provider per-tenant.
    """
    if cb_open("whatsapp", tenant_id):
        logger.warning(
            "WhatsApp suppressed (CB open) tenant=%s — using fallback", tenant_id)
        return _send_fallback(subject, body)

    ok = _whatsapp_send(body)
    cb_record(ok, "whatsapp", tenant_id)

    if not ok:
        logger.warning("WhatsApp failed tenant=%s — falling back to webhook", tenant_id)
        _send_fallback(subject, body)

    return ok


# ── Notification task wrapper ──────────────────────────────────────────────────

def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as e:
        logger.error("Notification task failed: %s", e)


def fire(coro) -> None:
    """Schedule a notification coroutine as a non-blocking background task."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_handle_task_result)


async def _run_in_executor(fn: Callable, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ── Public notification API ────────────────────────────────────────────────────

async def send_detection(incident_id: str, score: float, p95: float, err: float, tenant_id: str = "global") -> None:
    """Message 1 — DETECTED. No recovery link."""
    body = (
        f"🚨 API critical. Analysing...\n\n"
        f"Score: {score:.0f}/100\n"
        f"P95: {p95:.0f}ms\n"
        f"Errors: {err*100:.0f}%\n\n"
        f"Incident: {incident_id}"
    )
    await _run_in_executor(_send_with_fallback, "API Critical", body, tenant_id)


async def send_validation(incident_id: str, score: float, p95: float, confirm_url: str, tenant_id: str = "global") -> None:
    """Message 2 — VALIDATED. Contains recovery link."""
    body = (
        f"⚡ Restart recommended.\n\n"
        f"Score: {score:.0f}/100\n"
        f"P95: {p95:.0f}ms\n\n"
        f"Tap to authorise:\n{confirm_url}"
    )
    await _run_in_executor(_send_with_fallback, "Action Required", body, tenant_id)


async def send_recovery(incident_id: str, score: float, duration_s: float, tenant_id: str = "global") -> None:
    """Message 3 — RESOLVED."""
    minutes = int(duration_s // 60)
    seconds = int(duration_s % 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    body = (
        f"✅ Recovered. Score: {score:.0f}/100\n"
        f"Duration: {duration_str}"
    )
    await _run_in_executor(_send_with_fallback, "Recovered", body, tenant_id)


async def send_voice_escalation(incident_id: str, duration_s: float, score: float) -> None:
    """Voice call escalation — fires after VOICE_S seconds."""
    to_   = os.getenv("PRIMARY_PHONE")
    from_ = os.getenv("TWILIO_PHONE_NUMBER")
    if not to_ or not from_:
        logger.warning("Voice escalation not configured")
        return
    minutes = int(duration_s // 60)
    twiml = (
        f"<Response><Say>"
        f"Critical alert. Incident {incident_id}. "
        f"Duration {minutes} minutes. Score {score:.0f}. "
        f"Immediate action required."
        f"</Say></Response>"
    )
    def _call():
        try:
            call = _twilio_client().calls.create(to=to_, from_=from_, twiml=twiml)
            logger.warning("Voice call: %s", call.sid)
            return True
        except Exception as e:
            logger.error("Voice call failed: %s", e)
            return False
    await _run_in_executor(_call)


async def send_secondary_escalation(incident_id: str, duration_s: float, score: float) -> None:
    """Secondary engineer notification."""
    from_  = os.getenv("TWILIO_WHATSAPP_FROM")
    to_    = os.getenv("SECONDARY_WHATSAPP")
    if not from_ or not to_:
        logger.warning("Secondary engineer not configured")
        return
    minutes = int(duration_s // 60)
    body = (
        f"🚨 Escalation.\n\n"
        f"Incident: {incident_id}\n"
        f"Duration: {minutes} min\n"
        f"Score: {score:.0f}/100\n\n"
        f"Primary unresponsive."
    )
    def _send():
        try:
            msg = _twilio_client().messages.create(body=body, from_=from_, to=to_)
            logger.error("Secondary notified: %s", msg.sid)
            return True
        except Exception as e:
            logger.error("Secondary notify failed: %s", e)
            _send_fallback("Escalation", body)
            return False
    await _run_in_executor(_send)


# ── Provider-based dispatch ────────────────────────────────────────────────────

async def dispatch(
    tenant: dict,
    incident_id: str,
    message: str,
) -> bool:
    """
    Route and deliver notification via tenant's configured channel.
    Records every attempt in the delivery ledger.
    Falls back to webhook if primary fails.
    Never raises.
    """
    from tenants import get_verified_numbers
    if not tenant.get("whatsapp_numbers"):
        verified = get_verified_numbers(tenant.get("tenant_id", ""))
        if verified:
            tenant = {**tenant, "whatsapp_numbers": verified}

    from providers import (
        WhatsAppProvider, TelegramProvider,
        WebhookProvider, SentProvider, SlackProvider,
    )
    from delivery_ledger import record_from_result

    channel = tenant.get("notification_channel", "whatsapp")
    tenant_id = tenant.get("tenant_id", "global")

    if channel == "telegram":
        primary = TelegramProvider()
    elif channel == "sent":
        primary = SentProvider()
    else:
        primary = WhatsAppProvider()

    # Check per-tenant circuit breaker before sending
    if cb_open(channel, tenant_id):
        logger.warning(
            "CB open for %s tenant=%s — skipping primary", channel, tenant_id)
        result = None
    else:
        result = await primary.send(tenant, incident_id, message)
        record_from_result(result)
        cb_record(result.success if result else False, channel, tenant_id)

    if tenant.get("slack_webhook_url"):
        from plans import get_tenant_plan
        plan = get_tenant_plan(tenant)
        if getattr(plan, "has_slack", False):
            slack = SlackProvider()
            slack_result = await slack.send(tenant, incident_id, message)
            record_from_result(slack_result)

    if result and result.success:
        return True

    logger.warning("Primary failed (%s) — trying webhook fallback", channel)
    fallback        = WebhookProvider()
    fallback_result = await fallback.send(tenant, incident_id, message)
    record_from_result(fallback_result)

    if not fallback_result.success:
        logger.critical(
            "ALL notifications failed for incident=%s tenant=%s",
            incident_id, tenant_id,
        )

    return fallback_result.success


# ── Channel-aware routing ──────────────────────────────────────────────────────

async def send_via_channel(
    tenant: dict,
    subject: str,
    body: str,
) -> bool:
    """Route notification to the tenant's configured channel."""
    channel = tenant.get("notification_channel", "whatsapp")
    tenant_id = tenant.get("tenant_id", "global")

    if channel == "telegram":
        from telegram_notifier import send_telegram
        bot_token = tenant.get("telegram_bot_token")
        chat_id   = tenant.get("telegram_chat_id")
        full_message = f"*{subject}*\n\n{body}"
        return await send_telegram(bot_token, chat_id, full_message)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _send_with_fallback(subject, body, tenant_id)
    )
