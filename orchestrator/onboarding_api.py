# orchestrator/onboarding_api.py
"""
Onboarding API routes.

Endpoints:
    POST /onboarding/test-connection  Test health URL reachability
    POST /onboarding/test-alert       Send WhatsApp test message
    POST /onboarding/activate         Register tenant in Redis
    GET  /onboarding/status           Current onboarding config
"""

import logging
import os
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tenants import create_tenant, get_tenant, list_active_tenants

logger = logging.getLogger("orchestrator.onboarding")

router = APIRouter(prefix="/onboarding")

TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")


# ── Request models ─────────────────────────────────────────────────────────────

class TestConnectionRequest(BaseModel):
    base_url: str


class TestAlertRequest(BaseModel):
    phone_numbers: list[str]


class EngineerInput(BaseModel):
    name:  str
    phone: str


class ActivateRequest(BaseModel):
    service_name: str
    base_url:     str
    engineers:    list[EngineerInput]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalise_phone(phone: str) -> str:
    """Ensure phone has whatsapp: prefix."""
    phone = phone.strip()
    if not phone.startswith("whatsapp:"):
        phone = f"whatsapp:{phone}"
    return phone


def _send_whatsapp(to: str, body: str) -> bool:
    """Send a WhatsApp message via Twilio. Returns True on success."""
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token or not TWILIO_FROM:
        logger.warning("Twilio not configured — skipping send")
        return False
    try:
        from twilio.rest import Client
        msg = Client(sid, token).messages.create(
            body=body, from_=TWILIO_FROM, to=to
        )
        logger.info("WhatsApp sent to %s: %s", to, msg.sid)
        return True
    except Exception as e:
        logger.error("WhatsApp send failed to %s: %s", to, e)
        return False


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/test-connection")
async def test_connection(req: TestConnectionRequest):
    """
    Verify that the provided base_url exposes a valid /health/alerts endpoint.
    """
    url = req.base_url.rstrip("/") + "/health/alerts"

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url)

        if r.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Endpoint returned {r.status_code}. Expected 200."
            )

        data  = r.json()
        score = data.get("health_score", {}).get("score")
        status_val = data.get("health_score", {}).get("status", "unknown")

        return {
            "status":       "connected",
            "health_score": score,
            "health_status": status_val,
            "url_tested":   url,
        }

    except httpx.ConnectError:
        raise HTTPException(status_code=400, detail="Could not connect. Check your URL.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=400, detail="Connection timed out after 8 seconds.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/test-alert")
async def test_alert(req: TestAlertRequest):
    """
    Send a test WhatsApp message to all provided phone numbers.
    """
    if not req.phone_numbers:
        raise HTTPException(status_code=400, detail="At least one phone number required")

    body = (
        "⚡ AlertEngine test alert\n\n"
        "Your monitoring is almost set up.\n"
        "When your API degrades, you'll receive an alert like this — "
        "with a link to recover in one tap.\n\n"
        "No action needed. Just confirming delivery."
    )

    sent   = []
    failed = []

    for phone in req.phone_numbers:
        normalised = _normalise_phone(phone)
        ok = _send_whatsapp(normalised, body)
        if ok:
            sent.append(phone)
        else:
            failed.append(phone)

    if not sent and failed:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send to all numbers. Check Twilio configuration."
        )

    return {
        "status": "sent",
        "sent":   sent,
        "failed": failed,
        "message": f"Test alert sent to {len(sent)} number(s).",
    }


@router.post("/activate")
async def activate(req: ActivateRequest):
    """
    Register tenant in Redis. Tenant starts as active immediately
    (test alert already confirmed WhatsApp delivery).
    """
    if not req.engineers:
        raise HTTPException(status_code=400, detail="At least one engineer required")

    if not req.base_url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid base_url")

    phones = [_normalise_phone(e.phone) for e in req.engineers]

    try:
        tenant = create_tenant(
            service_name=req.service_name,
            health_url=req.base_url.rstrip("/") + "/health/alerts",
            whatsapp_numbers=phones,
        )

        # Since test alert was sent and confirmed, activate immediately
        from tenants import activate_tenant, save_contacts, get_contacts
        contacts = get_contacts(tenant["tenant_id"])
        for c in contacts:
            c["verified"]    = True
            c["verified_at"] = time.time()
        save_contacts(tenant["tenant_id"], contacts)
        activate_tenant(tenant["tenant_id"])

        tenant_id = tenant["tenant_id"]
        logger.info("Tenant activated: %s (%s)", tenant_id, req.service_name)

        # Send confirmation
        confirmation = (
            f"✅ You're live on AlertEngine.\n\n"
            f"Service: *{req.service_name}*\n"
            f"Monitoring: active\n\n"
            f"You'll be alerted here when your API degrades.\n"
            f"One tap to recover."
        )
        for phone in phones:
            _send_whatsapp(phone, confirmation)

        return {
            "status":        "active",
            "tenant_id":     tenant_id,
            "service_name":  req.service_name,
            "health_url":    tenant["health_url"],
            "engineers":     len(req.engineers),
            "message":       "Monitoring is now active. Check WhatsApp for confirmation.",
        }

    except Exception as e:
        logger.error("Activation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Activation failed: {str(e)}")


@router.get("/status")
def onboarding_status():
    """Return current active tenants summary."""
    try:
        tenants = list_active_tenants()
        return {
            "active_tenants": len(tenants),
            "tenants": [
                {
                    "tenant_id":    t.get("tenant_id"),
                    "service_name": t.get("service_name"),
                    "status":       t.get("status"),
                    "created_at":   t.get("created_at"),
                }
                for t in tenants
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
