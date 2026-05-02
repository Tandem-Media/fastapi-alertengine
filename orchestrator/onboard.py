# orchestrator/onboard.py
"""
Tenant onboarding API — FastAPI router.

Endpoints:
    POST /onboard                      Register new tenant
    POST /verify                       Verify WhatsApp number
    GET  /tenant/{tenant_id}           Get tenant status
    GET  /tenant/{tenant_id}/contacts  Get contact verification status
    POST /tenant/{tenant_id}/test      Trigger test incident
"""

import logging
import os
import time

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

logger = logging.getLogger("orchestrator.onboard")

router = APIRouter()

TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")


# ── Request models ─────────────────────────────────────────────────────────────

class OnboardRequest(BaseModel):
    service_name:      str
    health_url:        str
    whatsapp_numbers:  list[str]


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


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/onboard")
def onboard(req: OnboardRequest):
    """
    Register a new tenant.
    Sends verification codes to all WhatsApp numbers.
    """
    if not req.whatsapp_numbers:
        raise HTTPException(status_code=400, detail="At least one WhatsApp number required")

    if not req.health_url.startswith("http"):
        raise HTTPException(status_code=400, detail="health_url must be a valid URL")

    # Normalise numbers to whatsapp: prefix
    numbers = []
    for n in req.whatsapp_numbers:
        if not n.startswith("whatsapp:"):
            n = f"whatsapp:{n}"
        numbers.append(n)

    tenant = create_tenant(
        service_name=req.service_name,
        health_url=req.health_url,
        whatsapp_numbers=numbers,
    )

    # Send verification codes
    sent    = []
    failed  = []
    for number in numbers:
        code = generate_verification_code(number)
        ok   = _send_verification_whatsapp(number, code)
        if ok:
            sent.append(number)
        else:
            failed.append(number)
            logger.warning("Verification code for %s: %s (send failed — log only)", number, code)

    return {
        "tenant_id":          tenant["tenant_id"],
        "service_name":       tenant["service_name"],
        "status":             "pending_verification",
        "contacts_pending":   len(numbers),
        "verification_sent":  sent,
        "verification_failed": failed,
        "next_step":          "POST /verify with your phone and code",
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

    # Find tenant
    tenant_id = find_tenant_by_phone(phone)
    if not tenant_id:
        raise HTTPException(status_code=404, detail="Phone number not found in any tenant")

    # Verify code
    valid = verify_phone(phone, req.code)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    # Mark verified
    mark_phone_verified(tenant_id, phone)

    # Check tenant status
    tenant   = get_tenant(tenant_id)
    contacts = get_contacts(tenant_id)
    pending  = [c["phone"] for c in contacts if not c.get("verified")]

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

    # Inject a synthetic critical health payload
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

    # Run through pipeline directly
    from pipeline import open_incident, decide_new_incident, validate_decision_schema
    from memory import save_incident, get_active_incident
    from notifications import fire, send_detection
    from action_generator import generate_recovery_token
    import asyncio

    # Don't overwrite real incident
    existing = get_active_incident()
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

    # Send to all verified numbers
    verified = get_verified_numbers(tenant_id)
    base_url = os.getenv("ACTION_BASE_URL", os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000"))
    token    = generate_recovery_token(incident_id)
    url      = f"{base_url}/action/recover?token={token}"

    from notifications import send_validation
    fire(send_detection(incident_id, 20.0, 2500.0, 0.75))

    return {
        "incident_id":     incident_id,
        "tenant_id":       tenant_id,
        "status":          "triggered",
        "notified":        verified,
        "recovery_url":    url,
        "message":         "Test incident fired. Check WhatsApp.",
    }
