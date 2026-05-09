"""
Slack notification provider using Incoming Webhooks.
Posts incident updates to a designated Slack channel.

Zero Slack App setup required — just a webhook URL.
Get one at: https://api.slack.com/messaging/webhooks

Tenant config fields:
    slack_webhook_url:  str  — Incoming Webhook URL
    slack_channel:      str  — optional channel override e.g. #dev-alerts

Slack serves as the team transparency channel:
    WhatsApp/Telegram/Sent.dm → individual engineer (action)
    Slack → entire team (visibility)
"""

import logging
import httpx
from .base import NotificationProvider, DeliveryResult
from circuit_breaker import is_open, record_failure, record_success

logger = logging.getLogger("orchestrator.providers.slack")

SLACK_TIMEOUT = 10


def _build_detection_blocks(
    incident_id: str,
    score: float,
    p95: float,
    err: float,
    service_name: str = "API",
) -> list:
    """Build Slack Block Kit blocks for incident detection alert."""
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🔴 Incident Detected — {service_name}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Health Score:*\n{score:.0f}/100"},
                {"type": "mrkdwn", "text": f"*P95 Latency:*\n{p95:.0f}ms"},
                {"type": "mrkdwn", "text": f"*Error Rate:*\n{err*100:.1f}%"},
                {"type": "mrkdwn", "text": f"*Incident ID:*\n`{incident_id}`"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "⏳ Analysing... Recovery link will be sent to the authorized engineer.",
                }
            ],
        },
    ]


def _build_recovery_blocks(
    incident_id: str,
    score: float,
    duration_s: float,
    service_name: str = "API",
) -> list:
    """Build Slack Block Kit blocks for recovery notification."""
    minutes = int(duration_s // 60)
    seconds = int(duration_s % 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"✅ Recovered — {service_name}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Health Score:*\n{score:.0f}/100"},
                {"type": "mrkdwn", "text": f"*Duration:*\n{duration_str}"},
                {"type": "mrkdwn", "text": f"*Incident ID:*\n`{incident_id}`"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "✅ Engineer authorized recovery. System is healthy.",
                }
            ],
        },
    ]


def _build_stage_blocks(
    incident_id: str,
    stage: str,
    reason: str,
    recovery_url: str = "",
) -> list:
    """Build Slack Block Kit blocks for stage transition update."""
    stage_emoji = {
        "DETECTED":   "🔴",
        "PROPOSED":   "🟠",
        "VALIDATED":  "🟡",
        "AUTHORIZED": "🟢",
        "EXECUTED":   "⚙️",
        "RESOLVED":   "✅",
        "RECOVERED":  "✅",
    }.get(stage, "⚪")

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{stage_emoji} *Stage update:* `{stage}`\n{reason}",
            },
        },
    ]

    if recovery_url and stage == "VALIDATED":
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"👆 *Engineer approval required:*\n<{recovery_url}|Tap to authorize fix>",
            },
        })

    return blocks


class SlackProvider(NotificationProvider):
    channel = "slack"

    async def send(
        self,
        tenant: dict,
        incident_id: str,
        message: str,
    ) -> DeliveryResult:
        """
        Send initial incident detection alert to Slack.
        For stage updates use send_stage_update() directly.
        """
        tenant_id   = tenant.get("tenant_id", "global")
        webhook_url = tenant.get("slack_webhook_url")

        if is_open("slack", tenant_id):
            logger.warning("Slack CB open — suppressed: tenant=%s", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error="circuit_breaker_open")

        if not webhook_url:
            logger.warning("Slack webhook not configured: tenant=%s", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error="webhook_not_configured")

        try:
            async with httpx.AsyncClient(timeout=SLACK_TIMEOUT) as client:
                r = await client.post(webhook_url, json={
                    "text":   message,
                    "blocks": [],
                })
            if r.status_code == 200:
                record_success("slack", tenant_id)
                logger.info("Slack message sent: tenant=%s", tenant_id)
                return self._make_result(tenant, incident_id, True)
            else:
                record_failure("slack", tenant_id)
                logger.warning("Slack returned %d: tenant=%s",
                               r.status_code, tenant_id)
                return self._make_result(tenant, incident_id, False,
                                         error=f"http_{r.status_code}")
        except Exception as e:
            record_failure("slack", tenant_id)
            logger.error("Slack failed: tenant=%s error=%s", tenant_id, e)
            return self._make_result(tenant, incident_id, False,
                                     error=str(e))

    async def send_detection(
        self,
        tenant: dict,
        incident_id: str,
        score: float,
        p95: float,
        err: float,
        service_name: str = "API",
    ) -> DeliveryResult:
        """Send formatted detection alert with Block Kit."""
        tenant_id   = tenant.get("tenant_id", "global")
        webhook_url = tenant.get("slack_webhook_url")

        if is_open("slack", tenant_id):
            return self._make_result(tenant, incident_id, False,
                                     error="circuit_breaker_open")
        if not webhook_url:
            return self._make_result(tenant, incident_id, False,
                                     error="webhook_not_configured")
        try:
            blocks = _build_detection_blocks(
                incident_id, score, p95, err, service_name)
            async with httpx.AsyncClient(timeout=SLACK_TIMEOUT) as client:
                r = await client.post(webhook_url, json={
                    "text":   f"🔴 Incident detected — {service_name}",
                    "blocks": blocks,
                })
            if r.status_code == 200:
                record_success("slack", tenant_id)
                return self._make_result(tenant, incident_id, True)
            record_failure("slack", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error=f"http_{r.status_code}")
        except Exception as e:
            record_failure("slack", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error=str(e))

    async def send_recovery(
        self,
        tenant: dict,
        incident_id: str,
        score: float,
        duration_s: float,
        service_name: str = "API",
    ) -> DeliveryResult:
        """Send formatted recovery notification with Block Kit."""
        tenant_id   = tenant.get("tenant_id", "global")
        webhook_url = tenant.get("slack_webhook_url")

        if is_open("slack", tenant_id):
            return self._make_result(tenant, incident_id, False,
                                     error="circuit_breaker_open")
        if not webhook_url:
            return self._make_result(tenant, incident_id, False,
                                     error="webhook_not_configured")
        try:
            blocks = _build_recovery_blocks(
                incident_id, score, duration_s, service_name)
            async with httpx.AsyncClient(timeout=SLACK_TIMEOUT) as client:
                r = await client.post(webhook_url, json={
                    "text":   f"✅ Recovered — {service_name}",
                    "blocks": blocks,
                })
            if r.status_code == 200:
                record_success("slack", tenant_id)
                return self._make_result(tenant, incident_id, True)
            record_failure("slack", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error=f"http_{r.status_code}")
        except Exception as e:
            record_failure("slack", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error=str(e))

    async def send_stage_update(
        self,
        tenant: dict,
        incident_id: str,
        stage: str,
        reason: str,
        recovery_url: str = "",
    ) -> DeliveryResult:
        """Send stage transition update to Slack."""
        tenant_id   = tenant.get("tenant_id", "global")
        webhook_url = tenant.get("slack_webhook_url")

        if is_open("slack", tenant_id):
            return self._make_result(tenant, incident_id, False,
                                     error="circuit_breaker_open")
        if not webhook_url:
            return self._make_result(tenant, incident_id, False,
                                     error="webhook_not_configured")
        try:
            blocks = _build_stage_blocks(
                incident_id, stage, reason, recovery_url)
            async with httpx.AsyncClient(timeout=SLACK_TIMEOUT) as client:
                r = await client.post(webhook_url, json={
                    "text":   f"Stage update: {stage}",
                    "blocks": blocks,
                })
            if r.status_code == 200:
                record_success("slack", tenant_id)
                return self._make_result(tenant, incident_id, True)
            record_failure("slack", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error=f"http_{r.status_code}")
        except Exception as e:
            record_failure("slack", tenant_id)
            return self._make_result(tenant, incident_id, False,
                                     error=str(e))
