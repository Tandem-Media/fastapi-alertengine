"""
WhatsApp provider via Twilio.
Wraps existing _whatsapp_send logic from notifications.py.
Uses Redis-backed circuit breaker from circuit_breaker.py.
"""

import logging
import os
from .base import NotificationProvider, DeliveryResult
from circuit_breaker import is_open, record_failure, record_success

logger = logging.getLogger("orchestrator.providers.whatsapp")


class WhatsAppProvider(NotificationProvider):
    channel = "whatsapp"

    async def send(
        self,
        tenant: dict,
        incident_id: str,
        message: str,
    ) -> DeliveryResult:
        tenant_id = tenant.get("tenant_id", "global")

        if is_open("whatsapp", tenant_id):
            logger.warning("WhatsApp CB open — suppressed: tenant=%s", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error="circuit_breaker_open")

        from_ = tenant.get("twilio_whatsapp_from") or os.getenv("TWILIO_WHATSAPP_FROM")
        to_   = os.getenv("TWILIO_WHATSAPP_TO")
        sid   = tenant.get("twilio_account_sid") or os.getenv("TWILIO_ACCOUNT_SID")
        token = tenant.get("twilio_auth_token") or os.getenv("TWILIO_AUTH_TOKEN")

        if not from_ or not to_ or not sid or not token:
            logger.warning("WhatsApp credentials not configured")
            return self._make_result(tenant, incident_id, False,
                                     error="credentials_missing")
        try:
            from twilio.rest import Client
            msg   = Client(sid, token).messages.create(
                body=message, from_=from_, to=to_
            )
            record_success("whatsapp", tenant_id)
            logger.info("WhatsApp sent: %s", msg.sid)
            return self._make_result(tenant, incident_id, True,
                                     message_id=msg.sid)
        except Exception as e:
            record_failure("whatsapp", tenant_id)
            logger.error("WhatsApp failed: %s", e)
            return self._make_result(tenant, incident_id, False,
                                     error=str(e))
