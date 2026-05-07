"""
Generic webhook provider.
Wraps existing _send_fallback logic from notifications.py.
"""

import json
import logging
import os
import urllib.request
from .base import NotificationProvider, DeliveryResult

logger = logging.getLogger("orchestrator.providers.webhook")


class WebhookProvider(NotificationProvider):
    channel = "webhook"

    async def send(
        self,
        tenant: dict,
        incident_id: str,
        message: str,
    ) -> DeliveryResult:
        tenant_id = tenant.get("tenant_id", "global")
        url = os.getenv("FALLBACK_WEBHOOK_URL")

        if not url:
            logger.warning("No FALLBACK_WEBHOOK_URL — webhook suppressed")
            return self._make_result(tenant, incident_id, False,
                                     error="no_webhook_url")
        try:
            payload = json.dumps({"text": message}).encode()
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                ok = resp.status < 400
                if ok:
                    logger.info("Webhook sent: tenant=%s", tenant_id)
                    return self._make_result(tenant, incident_id, True)
                logger.warning("Webhook %d: tenant=%s", resp.status, tenant_id)
                return self._make_result(tenant, incident_id, False,
                                         error=f"http_{resp.status}")
        except Exception as e:
            logger.error("Webhook failed: %s", e)
            return self._make_result(tenant, incident_id, False, error=str(e))
