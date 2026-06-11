# orchestrator/shadow_mode_api.py
"""
Shadow Mode management endpoints.

Allows per-tenant toggling of shadow mode — the evaluation state where
AlertEngine runs the full pipeline (detect, diagnose, policy gates) but
suppresses all external calls (notifications, token generation, webhooks).
Every suppressed action is logged to the audit trail with actor="shadow_mode".

Endpoints:
    POST /tenant/{tenant_id}/shadow        — enable shadow mode
    DELETE /tenant/{tenant_id}/shadow      — disable shadow mode (go live)
    GET  /tenant/{tenant_id}/shadow        — check current shadow status
    GET  /tenant/{tenant_id}/shadow/report — audit summary of shadow period
"""

import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from tenants import get_tenant, save_tenant
from audit import get_audit_log
logger = logging.getLogger("orchestrator.shadow_mode_api")

router = APIRouter(prefix="/tenant", tags=["shadow-mode"])


# ── Auth helper ───────────────────────────────────────────────────────────────

def _verify_tenant(tenant_id: str, x_tenant_secret: Optional[str]) -> dict:
    """
    Load tenant and verify the request secret matches.
    Raises 404 if tenant not found, 403 if secret mismatch.
    """
    tenant = get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    expected = tenant.get("secret") or tenant.get("alert_secret")
    if not expected or x_tenant_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid tenant secret")
    return tenant


# ── Request / Response models ─────────────────────────────────────────────────

class ShadowStatusResponse(BaseModel):
    tenant_id: str
    shadow_mode: bool
    shadow_enabled_at: Optional[float] = None
    shadow_disabled_at: Optional[float] = None
    message: str


class ShadowReportResponse(BaseModel):
    tenant_id: str
    shadow_mode: bool
    shadow_enabled_at: Optional[float] = None
    total_shadow_events: int
    suppressed_notifications: int
    suppressed_tokens: int
    suppressed_escalations: int
    incidents_observed: int
    summary: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/{tenant_id}/shadow", response_model=ShadowStatusResponse)
async def enable_shadow_mode(
    tenant_id: str,
    x_tenant_secret: Optional[str] = Header(None),
):
    """
    Enable shadow mode for a tenant.

    In shadow mode:
    - Health polling continues normally
    - Incident detection and diagnosis run normally
    - Policy gates evaluate normally
    - All notifications, token generation, and escalations are suppressed
    - Every suppressed action is logged to the audit trail
    - No WhatsApp/Telegram messages are sent
    - No recovery webhooks are called

    Use this for evaluation periods before going live.
    """
    tenant = _verify_tenant(tenant_id, x_tenant_secret)

    if tenant.get("shadow_mode"):
        return ShadowStatusResponse(
            tenant_id=tenant_id,
            shadow_mode=True,
            shadow_enabled_at=tenant.get("shadow_enabled_at"),
            message="Shadow mode already active",
        )

    now = time.time()
    updated = {
        **tenant,
        "shadow_mode":       True,
        "shadow_enabled_at": now,
        "shadow_disabled_at": None,
    }
    save_tenant(updated)

    logger.info("[SHADOW] Enabled for tenant %s at %s", tenant_id, now)

    return ShadowStatusResponse(
        tenant_id=tenant_id,
        shadow_mode=True,
        shadow_enabled_at=now,
        message=(
            "Shadow mode enabled. AlertEngine will run the full pipeline "
            "but suppress all external calls. Every suppressed action is "
            "logged to the audit trail. Call DELETE /{tenant_id}/shadow to go live."
        ),
    )


@router.delete("/{tenant_id}/shadow", response_model=ShadowStatusResponse)
async def disable_shadow_mode(
    tenant_id: str,
    x_tenant_secret: Optional[str] = Header(None),
):
    """
    Disable shadow mode for a tenant — go live.

    After calling this endpoint:
    - Notifications will be sent via WhatsApp/Telegram
    - Recovery tokens will be generated and delivered
    - Escalations will fire on schedule
    - The full production pipeline is active

    Ensure your webhook URL and notification channels are configured
    before calling this endpoint.
    """
    tenant = _verify_tenant(tenant_id, x_tenant_secret)

    if not tenant.get("shadow_mode"):
        return ShadowStatusResponse(
            tenant_id=tenant_id,
            shadow_mode=False,
            message="Shadow mode already disabled — tenant is live",
        )

    now = time.time()
    updated = {
        **tenant,
        "shadow_mode":        False,
        "shadow_disabled_at": now,
    }
    save_tenant(updated)

    logger.info("[SHADOW] Disabled for tenant %s at %s — now LIVE", tenant_id, now)

    return ShadowStatusResponse(
        tenant_id=tenant_id,
        shadow_mode=False,
        shadow_disabled_at=now,
        message=(
            "Shadow mode disabled. Tenant is now LIVE. "
            "Notifications, recovery tokens, and escalations will fire normally."
        ),
    )


@router.get("/{tenant_id}/shadow", response_model=ShadowStatusResponse)
async def get_shadow_status(
    tenant_id: str,
    x_tenant_secret: Optional[str] = Header(None),
):
    """Check current shadow mode status for a tenant."""
    tenant = _verify_tenant(tenant_id, x_tenant_secret)

    shadow_mode = bool(tenant.get("shadow_mode", False))
    message = (
        "Shadow mode active — pipeline running, external calls suppressed"
        if shadow_mode
        else "Live — full pipeline active"
    )

    return ShadowStatusResponse(
        tenant_id=tenant_id,
        shadow_mode=shadow_mode,
        shadow_enabled_at=tenant.get("shadow_enabled_at"),
        shadow_disabled_at=tenant.get("shadow_disabled_at"),
        message=message,
    )


@router.get("/{tenant_id}/shadow/report", response_model=ShadowReportResponse)
async def get_shadow_report(
    tenant_id: str,
    x_tenant_secret: Optional[str] = Header(None),
):
    """
    Return an audit summary of shadow mode activity.

    Shows what AlertEngine would have done during the evaluation period —
    every suppressed notification, token generation, and escalation,
    with incident counts. Use this to demonstrate reliability to
    risk committees before going live.
    """
    tenant = _verify_tenant(tenant_id, x_tenant_secret)

    try:
        events = get_events(tenant_id=tenant_id)
    except Exception as e:
        logger.error("get_events failed for %s: %s", tenant_id, e)
        events = []

    shadow_events = [
        e for e in events
        if e.get("actor") == "shadow_mode"
        or (e.get("metadata") or {}).get("shadow_mode")
    ]

    suppressed_notifications = sum(
        1 for e in shadow_events
        if "notification" in e.get("reason", "").lower()
    )
    suppressed_tokens = sum(
        1 for e in shadow_events
        if "token" in e.get("reason", "").lower()
    )
    suppressed_escalations = sum(
        1 for e in shadow_events
        if "escalat" in e.get("reason", "").lower()
    )

    incident_ids = {
        e.get("incident_id") for e in shadow_events
        if e.get("incident_id")
    }

    total = len(shadow_events)
    incidents = len(incident_ids)

    if total == 0:
        summary = "No shadow activity recorded yet. Ensure shadow mode is enabled and traffic is flowing."
    else:
        summary = (
            f"During shadow evaluation: {incidents} incident(s) observed, "
            f"{suppressed_notifications} notification(s) suppressed, "
            f"{suppressed_tokens} recovery token(s) suppressed, "
            f"{suppressed_escalations} escalation(s) suppressed. "
            f"All actions were logged to the immutable audit trail."
        )

    return ShadowReportResponse(
        tenant_id=tenant_id,
        shadow_mode=bool(tenant.get("shadow_mode", False)),
        shadow_enabled_at=tenant.get("shadow_enabled_at"),
        total_shadow_events=total,
        suppressed_notifications=suppressed_notifications,
        suppressed_tokens=suppressed_tokens,
        suppressed_escalations=suppressed_escalations,
        incidents_observed=incidents,
        summary=summary,
    )
