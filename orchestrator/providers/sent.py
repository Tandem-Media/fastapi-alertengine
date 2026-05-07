"""
Sent.dm notification provider.
Uses the official sentdm Python SDK.
Zero-friction WhatsApp — no A2P 10DLC registration required.
Recommended for Solo tier tenants.

Tenant config fields required:
    sent_api_key:     str  — Sent.dm API key
    sent_phone_id:    str  — Sent.dm sender phone ID
    whatsapp_numbers: list — verified recipient numbers
"""

import asyncio
import logging
import os

from .base import NotificationProvider, DeliveryResult
from circuit_breaker import is_open, record_failure, record_success

logger = logging.getLogger("orchestrator.providers.sent")


class SentProvider(NotificationProvider):
    channel = "sent"

    async def send(
        self,
        tenant: dict,
        incident_id: str,
        message: str,
    ) -> DeliveryResult:
        tenant_id = tenant.get("tenant_id", "global")
        api_key = tenant.get("sent_api_key") or os.getenv("SENT_API_KEY")
        phone_id = tenant.get("sent_phone_id") or os.getenv("SENT_PHONE_ID")
        numbers = tenant.get("whatsapp_numbers", [])

        if is_open("sent", tenant_id):
            logger.warning("Sent CB open — suppressed: tenant=%s", tenant_id)
            return self._make_result(
                tenant, incident_id, False, error="circuit_breaker_open"
            )

        if not api_key or not phone_id:
            logger.warning("Sent.dm credentials not configured: tenant=%s", tenant_id)
            return self._make_result(tenant, incident_id, False, error="credentials_missing")

        clean_numbers = []
        for number in numbers:
            if isinstance(number, str):
                clean = number.replace("whatsapp:", "").strip()
                if clean:
                    clean_numbers.append(clean)

        if not clean_numbers:
            logger.warning("No WhatsApp numbers configured: tenant=%s", tenant_id)
            return self._make_result(tenant, incident_id, False, error="no_recipients")

        try:
            import sentdm

            def _send_all() -> str | None:
                client = sentdm.Sent(api_key=api_key)
                message_id = None
                for number in clean_numbers:
                    response = client.messages.send(
                        phone_number_id=phone_id,
                        to=number,
                        message=message,
                    )
                    if message_id is None:
                        response_id = getattr(response, "id", None)
                        if response_id is not None:
                            message_id = str(response_id)
                return message_id

            loop = asyncio.get_running_loop()
            message_id = await loop.run_in_executor(None, _send_all)
            record_success("sent", tenant_id)
            logger.info(
                "Sent.dm message sent: tenant=%s recipients=%d",
                tenant_id,
                len(clean_numbers),
            )
            return self._make_result(tenant, incident_id, True, message_id=message_id)

        except Exception as e:
            record_failure("sent", tenant_id)
            logger.error("Sent.dm failed: tenant=%s error=%s", tenant_id, e)
            return self._make_result(tenant, incident_id, False, error=str(e))
