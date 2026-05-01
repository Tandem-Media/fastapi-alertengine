# whatsapp_alert.py
"""
Notification channels — 3 messages max per incident.
No noise. No groups feel.
"""

import os
import logging

logger = logging.getLogger("alertengine.channels")


def _client():
    from twilio.rest import Client
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        raise RuntimeError("Twilio credentials missing")
    return Client(sid, token)


def _send(body: str) -> bool:
    from_  = os.getenv("TWILIO_WHATSAPP_FROM")
    to_    = os.getenv("TWILIO_WHATSAPP_TO")
    if not from_ or not to_:
        logger.warning("Twilio WhatsApp not configured")
        return False
    try:
        msg = _client().messages.create(body=body, from_=from_, to=to_)
        logger.info("WhatsApp sent: %s", msg.sid)
        return True
    except Exception as e:
        logger.error("WhatsApp failed: %s", e)
        return False


# ── Message 1: DETECTED ────────────────────────────────────────────────────────

def send_critical_alert(health_score, p95_ms, error_rate, trend, confirm_url, **kwargs) -> bool:
    """Sent at DETECTED stage. No link yet."""
    body = f"🚨 API critical. Analysing...\n\nScore: {health_score}/100\nP95: {p95_ms}ms\nErrors: {error_rate*100:.0f}%"
    return _send(body)


# ── Message 2: VALIDATED ───────────────────────────────────────────────────────

def send_validation_alert(health_score, p95_ms, confirm_url) -> bool:
    """Sent at VALIDATED stage. Contains the recovery link."""
    body = (
        f"⚡ Restart recommended.\n\n"
        f"Score: {health_score}/100\n"
        f"P95: {p95_ms}ms\n\n"
        f"Tap to authorise:\n{confirm_url}"
    )
    return _send(body)


# ── Message 3: RECOVERED ───────────────────────────────────────────────────────

def send_recovery_message(health_score, **kwargs) -> bool:
    """Sent when system returns to healthy."""
    body = f"✅ Recovered. Score: {health_score}/100"
    return _send(body)


# ── Escalation: Voice call ─────────────────────────────────────────────────────

def send_voice_call(incident_id, duration, score) -> bool:
    to_   = os.getenv("PRIMARY_PHONE")
    from_ = os.getenv("TWILIO_PHONE_NUMBER")
    if not to_ or not from_:
        logger.warning("Voice call not configured")
        return False
    minutes = duration // 60
    twiml = (
        f"<Response><Say>"
        f"Critical alert. Incident {incident_id}. "
        f"Duration {minutes} minutes. Score {score}. "
        f"Immediate action required."
        f"</Say></Response>"
    )
    try:
        call = _client().calls.create(to=to_, from_=from_, twiml=twiml)
        logger.warning("Voice call: %s", call.sid)
        return True
    except Exception as e:
        logger.error("Voice call failed: %s", e)
        return False


# ── Escalation: Secondary engineer ────────────────────────────────────────────

def notify_secondary_engineer(incident_id, duration, score) -> bool:
    from_  = os.getenv("TWILIO_WHATSAPP_FROM")
    to_    = os.getenv("SECONDARY_WHATSAPP")
    if not from_ or not to_:
        logger.warning("Secondary engineer not configured")
        return False
    minutes = duration // 60
    body = (
        f"🚨 Escalation.\n\n"
        f"Incident: {incident_id}\n"
        f"Duration: {minutes} min\n"
        f"Score: {score}/100\n\n"
        f"Primary unresponsive."
    )
    try:
        msg = _client().messages.create(body=body, from_=from_, to=to_)
        logger.error("Secondary notified: %s", msg.sid)
        return True
    except Exception as e:
        logger.error("Secondary notify failed: %s", e)
        return False
