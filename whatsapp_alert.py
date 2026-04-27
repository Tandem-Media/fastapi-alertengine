import os
import logging
from typing import Optional

logger = logging.getLogger("alertengine.channels")


def _client():
    from twilio.rest import Client
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")

    if not sid or not token:
        raise RuntimeError("Twilio credentials missing")

    return Client(sid, token)


# ─────────────────────────────────────────────
# WhatsApp
# ─────────────────────────────────────────────

def send_critical_alert(health_score, p95_ms, error_rate, trend, confirm_url):
    try:
        client = _client()
        msg = client.messages.create(
            from_=os.getenv("TWILIO_WHATSAPP_FROM"),
            to=os.getenv("TWILIO_WHATSAPP_TO"),
            body=(
                f"🚨 CRITICAL\n\n"
                f"Score: {health_score:.0f}/100\n"
                f"P95: {p95_ms:.0f}ms\n"
                f"Error: {error_rate*100:.1f}%\n\n"
                f"{confirm_url}"
            )
        )
        logger.info("WhatsApp alert sent %s", msg.sid)
        return True
    except Exception as e:
        logger.error("WhatsApp failed: %s", e)
        return False


def send_recovery_message(health_score):
    try:
        client = _client()
        client.messages.create(
            from_=os.getenv("TWILIO_WHATSAPP_FROM"),
            to=os.getenv("TWILIO_WHATSAPP_TO"),
            body=f"✅ RECOVERED — Score {health_score:.0f}/100"
        )
        return True
    except Exception as e:
        logger.error("Recovery send failed: %s", e)
        return False


# ─────────────────────────────────────────────
# 📞 Voice Call (Stage 2)
# ─────────────────────────────────────────────

def send_voice_call(incident_id, duration, score):
    try:
        client = _client()

        message = (
            f"Critical system failure. "
            f"Incident {incident_id}. "
            f"Duration {duration} seconds. "
            f"Health score {score}. "
            f"Immediate action required."
        )

        call = client.calls.create(
            to=os.getenv("PRIMARY_PHONE"),
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
            twiml=f"<Response><Say>{message}</Say></Response>"
        )

        logger.warning("📞 Voice call triggered %s", call.sid)
        return True

    except Exception as e:
        logger.error("Voice call failed: %s", e)
        return False


# ─────────────────────────────────────────────
# 👤 Second Engineer (Stage 3)
# ─────────────────────────────────────────────

def notify_secondary_engineer(incident_id, duration, score):
    try:
        client = _client()

        body = (
            f"🚨 ESCALATION LEVEL 2\n\n"
            f"Incident: {incident_id}\n"
            f"Duration: {duration//60} min\n"
            f"Score: {score}\n\n"
            f"Primary engineer unresponsive."
        )

        msg = client.messages.create(
            from_=os.getenv("TWILIO_WHATSAPP_FROM"),
            to=os.getenv("SECONDARY_WHATSAPP"),
            body=body
        )

        logger.error("👤 Secondary engineer notified %s", msg.sid)
        return True

    except Exception as e:
        logger.error("Secondary notify failed: %s", e)
        return False