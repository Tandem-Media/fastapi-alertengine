# orchestrator/notifications.py
"""
Notification system with circuit breaker and fallback channel.

Rules:
- All sending is async-safe (executor-wrapped)
- Circuit breaker: 3 failures → 60s cooldown
- Fallback webhook fires when primary (WhatsApp) fails
- Never blocks the orchestrator loop
- Never raises — logs and continues
"""

import asyncio
import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger("orchestrator.notifications")

# ── Circuit breaker ────────────────────────────────────────────────────────────

_CB = {
    "failures":    0,
    "disabled_at": 0.0,
    "threshold":   3,
    "cooldown_s":  60,
}


def cb_open() -> bool:
    if _CB["failures"] >= _CB["threshold"]:
        if time.time() - _CB["disabled_at"] < _CB["cooldown_s"]:
            return True
        # Cooldown expired — reset
        _CB["failures"]    = 0
        _CB["disabled_at"] = 0.0
        logger.info("🔌 Notification circuit breaker reset")
    return False


def cb_record(success: bool) -> None:
    if success:
        _CB["failures"] = 0
    else:
        _CB["failures"] += 1
        if _CB["failures"] >= _CB["threshold"]:
            _CB["disabled_at"] = time.time()
            logger.warning("🔌 Circuit breaker OPEN — suppressing for %ds", _CB["cooldown_s"])


def cb_status() -> dict:
    return {
        "open":        cb_open(),
        "failures":    _CB["failures"],
        "disabled_at": _CB["disabled_at"],
    }


# ── Fallback webhook ───────────────────────────────────────────────────────────

def _send_fallback(subject: str, body: str) -> bool:
    """
    Fallback channel — fires when WhatsApp fails.
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

def _send_with_fallback(subject: str, body: str) -> bool:
    """
    Try WhatsApp first. If circuit breaker is open or send fails,
    fall through to fallback webhook. Silence is never acceptable.
    """
    if cb_open():
        logger.warning("WhatsApp suppressed (CB open) — using fallback")
        result = _send_fallback(subject, body)
        return result

    ok = _whatsapp_send(body)
    cb_record(ok)

    if not ok:
        logger.warning("WhatsApp failed — falling back to webhook")
        _send_fallback(subject, body)

    return ok


# ── Notification task wrapper ──────────────────────────────────────────────────

def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as e:
        logger.error("🔥 Notification task failed: %s", e)


def fire(coro) -> None:
    """Schedule a notification coroutine as a non-blocking background task."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_handle_task_result)


async def _run_in_executor(fn: Callable, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ── Public notification API ────────────────────────────────────────────────────

async def send_detection(incident_id: str, score: float, p95: float, err: float) -> None:
    """Message 1 — DETECTED. No recovery link."""
    body = (
        f"🚨 API critical. Analysing...\n\n"
        f"Score: {score:.0f}/100\n"
        f"P95: {p95:.0f}ms\n"
        f"Errors: {err*100:.0f}%\n\n"
        f"Incident: {incident_id}"
    )
    await _run_in_executor(_send_with_fallback, "API Critical", body)


async def send_validation(incident_id: str, score: float, p95: float, confirm_url: str) -> None:
    """Message 2 — VALIDATED. Contains recovery link."""
    body = (
        f"⚡ Restart recommended.\n\n"
        f"Score: {score:.0f}/100\n"
        f"P95: {p95:.0f}ms\n\n"
        f"Tap to authorise:\n{confirm_url}"
    )
    await _run_in_executor(_send_with_fallback, "Action Required", body)


async def send_recovery(incident_id: str, score: float, duration_s: float) -> None:
    """Message 3 — RESOLVED."""
    minutes = int(duration_s // 60)
    seconds = int(duration_s % 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    body = (
        f"✅ Recovered. Score: {score:.0f}/100\n"
        f"Duration: {duration_str}"
    )
    await _run_in_executor(_send_with_fallback, "Recovered", body)


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
