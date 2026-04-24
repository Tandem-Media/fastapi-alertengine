# whatsapp_alert.py
"""
Twilio WhatsApp Alert Sender

Sends WhatsApp messages when AlertEngine detects a critical health event.
Includes the tap-to-recover link directly in the message.

Setup:
    pip install twilio python-dotenv

Environment variables (.env):
    TWILIO_ACCOUNT_SID      — from Twilio console
    TWILIO_AUTH_TOKEN       — from Twilio console
    TWILIO_WHATSAPP_FROM    — whatsapp:+14155238886 (sandbox number)
    TWILIO_WHATSAPP_TO      — whatsapp:+2637XXXXXXXX (your number)
    BASE_URL                — https://your-ngrok-or-railway-url.com

Twilio WhatsApp Sandbox setup:
    1. Twilio Console → Messaging → Try it out → WhatsApp Sandbox
    2. Send "join <code>" from your phone to +14155238886
    3. You can now receive messages from the sandbox
"""

import os
import logging
from typing import Optional

logger = logging.getLogger("alertengine.whatsapp")


def _client():
    """Lazy Twilio client — only imported when needed."""
    from twilio.rest import Client
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        raise RuntimeError(
            "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set."
        )
    return Client(sid, token)


def send_critical_alert(
    health_score:  float,
    p95_ms:        float,
    error_rate:    float,
    trend:         str,
    confirm_url:   str,
    service:       str = "payments-api",
) -> bool:
    """
    Send a WhatsApp critical alert with a tap-to-recover link.

    Returns True on success, False on failure (fail-silent).
    """
    from_  = os.getenv("TWILIO_WHATSAPP_FROM")
    to_    = os.getenv("TWILIO_WHATSAPP_TO")

    if not from_ or not to_:
        logger.warning("TWILIO_WHATSAPP_FROM / TO not set — skipping WhatsApp alert.")
        return False

    body = (
        f"🚨 *CRITICAL — {service}*\n"
        f"\n"
        f"Health score: *{health_score:.0f}/100*\n"
        f"Trend: *{trend}*\n"
        f"P95 latency: *{p95_ms:.0f}ms*\n"
        f"Error rate: *{error_rate * 100:.1f}%*\n"
        f"\n"
        f"Recommended action: *Restart*\n"
        f"\n"
        f"👉 Tap to recover:\n"
        f"{confirm_url}\n"
        f"\n"
        f"⏱ Link expires in 90 seconds."
    )

    try:
        client = _client()
        msg = client.messages.create(body=body, from_=from_, to=to_)
        logger.info("WhatsApp alert sent: SID=%s", msg.sid)
        return True
    except Exception as exc:
        logger.error("WhatsApp alert failed: %s", exc)
        return False


def send_recovery_message(
    health_score: float,
    service:      str = "payments-api",
) -> bool:
    """
    Send a WhatsApp recovery confirmation.

    Returns True on success, False on failure (fail-silent).
    """
    from_  = os.getenv("TWILIO_WHATSAPP_FROM")
    to_    = os.getenv("TWILIO_WHATSAPP_TO")

    if not from_ or not to_:
        return False

    body = (
        f"✅ *RECOVERED — {service}*\n"
        f"\n"
        f"System has stabilised.\n"
        f"Health score: *{health_score:.0f}/100*\n"
        f"\n"
        f"Monitoring continues."
    )

    try:
        client = _client()
        msg = client.messages.create(body=body, from_=from_, to=to_)
        logger.info("WhatsApp recovery message sent: SID=%s", msg.sid)
        return True
    except Exception as exc:
        logger.error("WhatsApp recovery message failed: %s", exc)
        return False
