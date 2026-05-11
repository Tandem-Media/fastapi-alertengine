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
        sid   = tenant.get("twilio_account_sid") or os.getenv("TWILIO_ACCOUNT_SID")
        token = tenant.get("twilio_auth_token") or os.getenv("TWILIO_AUTH_TOKEN")
        numbers = tenant.get("whatsapp_numbers", [])

        if not from_ or not sid or not token:
            logger.warning("WhatsApp credentials not configured")
            return self._make_result(tenant, incident_id, False,
                                     error="credentials_missing")

        if not numbers:
            logger.warning("No WhatsApp numbers configured: tenant=%s", tenant_id)
            return self._make_result(tenant, incident_id, False, error="no_recipients")

        from twilio.rest import Client
        client = Client(sid, token)
        successes = []
        failures = []
        for raw_number in numbers:
            # Normalize number to whatsapp:+ format
            normalized = (raw_number if raw_number.startswith("whatsapp:")
                          else f"whatsapp:{raw_number.lstrip('+')}")
            try:
                msg = client.messages.create(body=message, from_=from_, to=normalized)
                record_success("whatsapp", tenant_id)
                logger.info("WhatsApp sent: %s to %s", msg.sid, normalized)
                successes.append(normalized)
            except Exception as e:
                record_failure("whatsapp", tenant_id)
                logger.error("WhatsApp failed for %s: %s", normalized, e)
                failures.append({"number": normalized, "error": str(e)})
        ok = bool(successes)
        result = self._make_result(tenant, incident_id, ok, message_id=successes, error=(failures if failures else None))
        return result

