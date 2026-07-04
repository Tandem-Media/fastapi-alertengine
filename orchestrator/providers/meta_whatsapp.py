"""
WhatsApp provider via Meta Cloud API — no BSP required.
"""
import logging
import os
import httpx
from .base import NotificationProvider, DeliveryResult
from circuit_breaker import is_open, record_failure, record_success

logger = logging.getLogger("orchestrator.providers.meta_whatsapp")
GRAPH_API_VERSION = "v19.0"
GRAPH_API_URL = "https://graph.facebook.com/{version}/{phone_number_id}/messages"

def _normalize_recipient(raw_number: str) -> str:
    number = raw_number.replace("whatsapp:", "")
    return number.lstrip("+")

class MetaDirectProvider(NotificationProvider):
    channel = "whatsapp_meta"

    async def send(self, tenant: dict, incident_id: str, message: str) -> DeliveryResult:
        tenant_id = tenant.get("tenant_id", "global")
        if is_open("whatsapp_meta", tenant_id):
            return self._make_result(tenant, incident_id, False, error="circuit_breaker_open")
        access_token = tenant.get("meta_access_token") or os.getenv("META_ACCESS_TOKEN")
        phone_number_id = tenant.get("meta_phone_number_id") or os.getenv("META_PHONE_NUMBER_ID")
        numbers = tenant.get("whatsapp_numbers", [])
        if not access_token or not phone_number_id:
            logger.warning("Meta WhatsApp credentials not configured: tenant=%s", tenant_id)
            return self._make_result(tenant, incident_id, False, error="credentials_missing")
        if not numbers:
            return self._make_result(tenant, incident_id, False, error="no_recipients")
        url = GRAPH_API_URL.format(version=GRAPH_API_VERSION, phone_number_id=phone_number_id)
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        successes = []
        failures = []
        async with httpx.AsyncClient(timeout=10) as client:
            for raw_number in numbers:
                to = _normalize_recipient(raw_number)
                payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}}
                try:
                    r = await client.post(url, headers=headers, json=payload)
                    if r.status_code == 200:
                        data = r.json()
                        msg_id = (data.get("messages") or [{}])[0].get("id", "")
                        record_success("whatsapp_meta", tenant_id)
                        logger.info("Meta WhatsApp sent: %s to %s", msg_id, to)
                        successes.append(msg_id or to)
                    else:
                        record_failure("whatsapp_meta", tenant_id)
                        logger.error("Meta WhatsApp failed for %s: http_%d %s", to, r.status_code, r.text[:300])
                        failures.append({"number": to, "error": f"http_{r.status_code}"})
                except Exception as e:
                    record_failure("whatsapp_meta", tenant_id)
                    failures.append({"number": to, "error": str(e)})
        ok = bool(successes)
        return self._make_result(tenant, incident_id, ok, message_id=successes, error=(failures if failures else None))