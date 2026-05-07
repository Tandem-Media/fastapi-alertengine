"""
Telegram provider via Bot API.
Wraps existing send_telegram logic from telegram_notifier.py.
Uses Redis-backed circuit breaker from circuit_breaker.py.
"""

import logging
import httpx
from .base import NotificationProvider, DeliveryResult
from circuit_breaker import is_open, record_failure, record_success

logger = logging.getLogger("orchestrator.providers.telegram")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramProvider(NotificationProvider):
    channel = "telegram"

    async def send(
        self,
        tenant: dict,
        incident_id: str,
        message: str,
    ) -> DeliveryResult:
        tenant_id = tenant.get("tenant_id", "global")
        bot_token = tenant.get("telegram_bot_token")
        chat_id   = tenant.get("telegram_chat_id")

        if is_open("telegram", tenant_id):
            logger.warning("Telegram CB open — suppressed: tenant=%s", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error="circuit_breaker_open")

        if not bot_token or not chat_id:
            logger.warning("Telegram credentials not configured: tenant=%s",
                           tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error="credentials_missing")
        try:
            url = TELEGRAM_API.format(token=bot_token)
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json={
                    "chat_id":    chat_id,
                    "text":       message,
                    "parse_mode": "Markdown",
                })
            if r.status_code == 200:
                data   = r.json()
                msg_id = str(data.get("result", {}).get("message_id", ""))
                record_success("telegram", tenant_id)
                logger.info("Telegram sent: chat_id=%s msg_id=%s",
                            chat_id, msg_id)
                return self._make_result(tenant, incident_id, True,
                                         message_id=msg_id)
            else:
                record_failure("telegram", tenant_id)
                logger.warning("Telegram API %d: %s", r.status_code, r.text)
                return self._make_result(tenant, incident_id, False,
                                         error=f"http_{r.status_code}")
        except Exception as e:
            record_failure("telegram", tenant_id)
            logger.error("Telegram failed: %s", e)
            return self._make_result(tenant, incident_id, False,
                                     error=str(e))
