# orchestrator/onboard.py
"""
Standard onboarding flow with phone verification.

Use this for production tenants who need WhatsApp/Telegram
phone verification before activation.

Endpoints:
    POST /onboard   — Register tenant, send verification codes
    POST /verify    — Verify phone number, activate tenant
    GET  /tenant/{id}          — Get tenant status
    GET  /tenant/{id}/contacts — Get contact verification status
    POST /tenant/{id}/test     — Trigger test incident
"""

import asyncio
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tenants import (
    create_tenant,
    get_tenant,
    get_contacts,
    get_verified_numbers,
    generate_verification_code,
    verify_phone,
    mark_phone_verified,
    find_tenant_by_phone,
)
from plans import get_plan, get_tenant_plan, incident_quota_remaining

logger = logging.getLogger("orchestrator.onboard")

router = APIRouter()

TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")


# ── Request models ─────────────────────────────────────────────────────────────

class OnboardRequest(BaseModel):
    service_name:           str
    health_url:             str
    whatsapp_numbers:       list[str] = []
    notification_channel:   str = "whatsapp"
    plan:                   str = "solo"
    telegram_bot_token:     Optional[str] = None
    telegram_chat_id:       Optional[str] = None
    twilio_account_sid:     Optional[str] = None
    twilio_auth_token:      Optional[str] = None
    twilio_whatsapp_from:   Optional[str] = None
    sent_api_key:           Optional[str] = None
    sent_phone_id:          Optional[str] = None
    slack_webhook_url:      Optional[str] = None
    slack_channel:          Optional[str] = None


class VerifyRequest(BaseModel):
    phone: str
    code:  str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _send_verification_whatsapp(phone: str, code: str) -> bool:
    """Send verification code via WhatsApp."""
    try:
        from twilio.rest import Client
        sid   = os.getenv("TWILIO_ACCOUNT_SID")
        token = os.getenv("TWILIO_AUTH_TOKEN")
        if not sid or not token or not TWILIO_FROM:
            logger.warning("Twilio not configured — skipping WhatsApp verification send")
            return False
        body = (
            f"⚡ AlertEngine verification\n\n"
            f"Your code: *{code}*\n\n"
            f"Expires in 5 minutes."
        )
        client = Client(sid, token)
        msg    = client.messages.create(body=body, from_=TWILIO_FROM, to=phone)
        logger.info("Verification sent to %s: %s", phone, msg.sid)
        return True
    except Exception as e:
        logger.error("Failed to send verification to %s: %s", phone, e)
        return False


async def _send_welcome_message(tenant: dict, phone: str) -> None:
    """Send a welcome message via the tenant's configured notification provider."""
    try:
        from notifications import dispatch

        message = (
            "✅ AlertEngine connected successfully.\n\n"
            f"Service: {tenant.get('service_name', '')}\n"
            f"Tenant: {tenant.get('tenant_id', '')}\n"
            f"Health URL: {tenant.get('health_url', '')}\n"
            f"Notification: {tenant.get('notification_channel', '')}\n\n"
            "Receiving live telemetry now.\n\n"
            "You will only be contacted when intervention \n"
            "may be required.\n\n"
            "— AlertEngine"
        )

        welcome_tenant = dict(tenant)
        channel = tenant.get("notification_channel", "whatsapp")
        if channel in ("whatsapp", "sent"):
            welcome_tenant["whatsapp_numbers"] = [phone]

        await dispatch(
            tenant=welcome_tenant,
            incident_id=f"welcome-{tenant.get('tenant_id', 'unknown')}",
            message=message,
        )
    except Exception as exc:
        logger.warning("Welcome message send failed for %s: %s", phone, exc)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/onboard")
def onboard(req: OnboardRequest):
    """
    Register a new tenant.
    Sends verification codes to all WhatsApp numbers.
    """
    if req.notification_channel in ("whatsapp", "sent") and not req.whatsapp_numbers:
        raise HTTPException(status_code=400, detail="At least one WhatsApp number required")

    if not req.health_url.startswith("http"):
        raise HTTPException(status_code=400, detail="health_url must be a valid URL")

    if req.notification_channel == "telegram":
        if not req.telegram_bot_token or not req.telegram_chat_id:
            raise HTTPException(
                status_code=400,
                detail="telegram_bot_token and telegram_chat_id are required for Telegram channel",
            )

    effective_channel = req.notification_channel

    numbers = []
    for n in req.whatsapp_numbers:
        if not n.startswith("whatsapp:"):
            n = f"whatsapp:{n}"
        numbers.append(n)

    tenant = create_tenant(
        service_name=req.service_name,
        health_url=req.health_url,
        whatsapp_numbers=numbers,
        plan=req.plan,
        notification_channel=effective_channel,
        telegram_bot_token=req.telegram_bot_token,
        telegram_chat_id=req.telegram_chat_id,
        twilio_account_sid=req.twilio_account_sid,
        twilio_auth_token=req.twilio_auth_token,
        twilio_whatsapp_from=req.twilio_whatsapp_from,
        sent_api_key=req.sent_api_key,
        sent_phone_id=req.sent_phone_id,
        slack_webhook_url=req.slack_webhook_url,
        slack_channel=req.slack_channel,
    )

    sent    = []
    failed  = []
    if effective_channel in ("whatsapp", "sent"):
        for number in numbers:
            code = generate_verification_code(number)
            ok   = _send_verification_whatsapp(number, code)
            if ok:
                sent.append(number)
            else:
                failed.append(number)
                logger.warning("Verification code for %s: %s (send failed — log only)", number, code)

    if req.sent_api_key or effective_channel == "sent":
        notification_config = "sent"
    elif req.twilio_account_sid:
        notification_config = "custom_twilio"
    else:
        notification_config = "shared"

    return {
        "tenant_id":             tenant["tenant_id"],
        "service_name":          tenant["service_name"],
        "notification_channel":  effective_channel,
        "status":                tenant["status"],
        "plan":                  req.plan,
        "contacts_pending":      len(numbers),
        "verification_sent":     sent,
        "verification_failed":   failed,
        "notification_config":   notification_config,
        "slack_configured":      bool(req.slack_webhook_url),
        "slack_available":       get_plan(req.plan).has_slack,
        "next_step":             "POST /verify with your phone and code" if effective_channel in ("whatsapp", "sent") else "Tenant is active. Configure your bot and start monitoring.",
    }


@router.post("/verify")
def verify(req: VerifyRequest):
    """
    Verify a WhatsApp number with the code that was sent.
    When all contacts for a tenant are verified, tenant becomes active.
    """
    phone = req.phone
    if not phone.startswith("whatsapp:"):
        phone = f"whatsapp:{phone}"

    tenant_id = find_tenant_by_phone(phone)
    if not tenant_id:
        raise HTTPException(status_code=404, detail="Phone number not found in any tenant")

    valid = verify_phone(phone, req.code)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    mark_phone_verified(tenant_id, phone)

    tenant   = get_tenant(tenant_id)
    contacts = get_contacts(tenant_id)
    pending  = [c["phone"] for c in contacts if not c.get("verified")]

    if not pending and tenant:
        try:
            asyncio.create_task(_send_welcome_message(tenant, phone))
        except Exception as exc:
            logger.warning("Failed to schedule welcome message for %s: %s", phone, exc)

    return {
        "tenant_id":       tenant_id,
        "phone":           phone,
        "verified":        True,
        "tenant_status":   tenant.get("status"),
        "remaining":       len(pending),
        "message":         "Tenant active!" if not pending else f"{len(pending)} number(s) still pending",
    }


@router.get("/tenant/{tenant_id}")
def get_tenant_status(tenant_id: str):
    tenant = get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.get("/tenant/{tenant_id}/contacts")
def get_tenant_contacts(tenant_id: str):
    tenant = get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    contacts = get_contacts(tenant_id)
    return {
        "tenant_id": tenant_id,
        "contacts":  contacts,
        "verified":  sum(1 for c in contacts if c.get("verified")),
        "pending":   sum(1 for c in contacts if not c.get("verified")),
    }


@router.post("/tenant/{tenant_id}/test")
async def test_incident(tenant_id: str):
    """
    Trigger a simulated critical incident for a tenant.
    Runs through the full pipeline — real notifications fire.
    """
    tenant = get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if tenant.get("status") != "active":
        raise HTTPException(
            status_code=400,
            detail=f"Tenant not active (status={tenant.get('status')}). Verify all contacts first."
        )

    plan  = get_tenant_plan(tenant)

    if not plan.has_claude_decision:
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "AI diagnosis not available on Hobby plan. Upgrade to Developer or higher.",
                "code": "PLAN_FEATURE_UNAVAILABLE",
            }
        )

    quota = incident_quota_remaining(tenant)
    if quota == 0:
        raise HTTPException(
            status_code=402,
            detail=f"Incident quota exhausted for {plan.name} plan. Upgrade to continue."
        )

    synthetic_health = {
        "health_score": {
            "score":  20.0,
            "status": "critical",
            "trend":  "degrading",
        },
        "metrics": {
            "overall_p95_ms": 2500.0,
            "error_rate":     0.75,
        },
        "alerts": [
            {
                "type":              "test_incident",
                "severity":          "critical",
                "triggered_by":      "manual_test",
                "reason_for_trigger": "Test incident triggered via /test endpoint",
            }
        ],
    }

    from pipeline import open_incident, decide_new_incident, validate_decision_schema
    from memory import save_incident, get_active_incident
    from notifications import fire, send_detection
    from action_generator import generate_recovery_token
    import asyncio

    existing = get_active_incident(tenant_id=tenant_id)
    if existing and existing.get("tenant_id") == tenant_id:
        raise HTTPException(status_code=409, detail="Active incident already exists for this tenant")

    incident_id = f"test-{tenant_id}-{int(time.time())}"
    decision    = decide_new_incident(incident_id, 20.0, 2500.0, 0.75, 0.95)

    valid, reason = validate_decision_schema(decision)
    if not valid:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {reason}")

    incident_record = open_incident(incident_id, 20.0, 2500.0, 0.75)
    incident_record["tenant_id"] = tenant_id
    save_incident(incident_record)

    from audit import append_event
    append_event(
        incident_id=incident_id,
        stage="DETECTED",
        decision="escalate",
        reason="Test incident triggered via /test endpoint",
        confidence=0.95,
        tenant_id=tenant_id,
    )

    verified = get_verified_numbers(tenant_id)
    base_url = os.getenv("ACTION_BASE_URL", os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000"))
    token    = generate_recovery_token(incident_id, tenant_id=tenant_id)
    url      = f"{base_url}/action/recover?token={token}"

    from notifications import dispatch
    asyncio.create_task(dispatch(
        tenant=tenant,
        incident_id=incident_id,
        message=(
            f"🚨 Test incident detected\n\n"
            f"Score: 20/100\n"
            f"P95: 2500ms\n"
            f"Errors: 75%\n\n"
            f"Incident: {incident_id}\n"
            f"Recovery URL: {url}"
        ),
    ))

    return {
        "incident_id":     incident_id,
        "tenant_id":       tenant_id,
        "status":          "triggered",
        "notified":        verified,
        "recovery_url":    url,
        "message":         "Test incident fired. Check WhatsApp.",
    }
