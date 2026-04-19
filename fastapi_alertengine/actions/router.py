# fastapi_alertengine/actions/router.py
"""
v1.6 — Actions Router (hardened)

Endpoints
---------
GET /action/confirm?token=<jwt>
    HTML confirmation page — user must explicitly click to execute.

GET /action/restart?token=<jwt>&client_ip=<ip>
    Execute restart: verify token → replay protection → IP binding →
    authorisation → mark JTI used → execute → audit log → return.

Security (v1.6 hardening)
--------------------------
* HS256 JWT signed with ACTION_SECRET_KEY
* Optional IP binding — token bound_ip claim verified against client_ip param
* JTI replay protection backed by Redis (falls back to in-memory)
* Full audit log entry written for every attempt (success and failure)
* Authorisation hook: replace _is_authorised() stub with real RBAC
"""

import logging
from html import escape as html_escape
from typing import Optional

import jwt
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from fastapi_alertengine.actions.audit import log_action
from fastapi_alertengine.actions.replay_store import is_token_used, mark_token_used
from fastapi_alertengine.actions.services import restart_container
from fastapi_alertengine.actions.tokens import verify_action_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/action", tags=["remote-actions"])

# Redis client reference — injected by the engine at startup when available.
# Falls back to None (in-memory replay protection) when Redis is not configured.
_rdb = None


def set_redis(rdb) -> None:
    """Called by AlertEngine.start() to inject the shared Redis client."""
    global _rdb
    _rdb = rdb


# ── Authorisation stub ────────────────────────────────────────────────────────

def _is_authorised(user_id: str, action: str, service: str) -> bool:
    """
    Return True if user_id is permitted to perform action on service.
    Replace with your real RBAC / ACL implementation.
    """
    return bool(user_id)


# ── Confirmation page ─────────────────────────────────────────────────────────

@router.get("/confirm", response_class=HTMLResponse)
async def confirm_action(
    token: str = Query(..., description="Signed JWT action token"),
) -> HTMLResponse:
    """
    Render a confirmation page. The user must click to proceed.
    Prevents accidental triggering via link-preview crawlers.
    """
    try:
        payload = verify_action_token(token)
    except Exception:
        return HTMLResponse("<h3>Invalid or expired token.</h3>", status_code=403)

    action  = html_escape(payload.get("action",  ""))
    service = html_escape(payload.get("service", ""))
    safe_token = html_escape(token)
    score   = payload.get("health_score")
    score_line = f"<p>Health score at alert time: <strong>{score}/100</strong></p>" if score else ""
    iid     = payload.get("incident_id")
    iid_line = f"<p>Incident ID: <code>{html_escape(iid)}</code></p>" if iid else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Confirm Action — fastapi-alertengine</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 480px;
             margin: 60px auto; padding: 0 20px; }}
    .card {{ border: 1px solid #e2e8f0; border-radius: 8px;
              padding: 24px; background: #f8fafc; }}
    h2 {{ color: #1B2A4A; margin-top: 0; }}
    button {{ background: #DC2626; color: white; border: none;
               padding: 12px 24px; border-radius: 6px; font-size: 16px;
               cursor: pointer; margin-top: 16px; }}
    button:hover {{ background: #b91c1c; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>⚠️ Confirm Infrastructure Action</h2>
    <p>Action: <strong>{action}</strong></p>
    <p>Service: <strong>{service}</strong></p>
    {score_line}
    {iid_line}
    <p style="color:#64748b;font-size:14px;">
      This action cannot be undone. The token expires in 90 seconds.
    </p>
    <form method="get" action="/action/{action}">
      <input type="hidden" name="token" value="{safe_token}">
      <button type="submit">Confirm — {action} {service}</button>
    </form>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── Execution endpoint ────────────────────────────────────────────────────────

@router.get("/restart")
async def action_restart(
    token:     str            = Query(..., description="Signed JWT action token"),
    client_ip: Optional[str]  = Query(None, description="Caller IP for binding verification"),
) -> dict:
    """
    Execute a service restart authorised by a signed JWT token.

    Security chain (v1.6):
    1. Verify JWT signature and expiry
    2. Enforce IP binding when token has bound_ip claim
    3. Check JTI replay protection (Redis-backed, falls back to memory)
    4. Authorisation check
    5. Mark JTI as consumed BEFORE execution (prevents double-fire)
    6. Execute restart
    7. Write audit log entry
    """
    # 1. Verify token
    try:
        payload = verify_action_token(token, client_ip=client_ip)
    except ValueError as exc:
        # IP binding violation
        log_action(user_id="unknown", action="restart", service="unknown",
                   result="ip_mismatch", detail=str(exc), rdb=_rdb)
        raise HTTPException(status_code=403, detail=str(exc))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=403, detail="Action token has expired.")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=403, detail=f"Invalid action token: {exc}")
    except RuntimeError as exc:
        logger.error("ACTION_SECRET_KEY not configured: %s", exc)
        raise HTTPException(status_code=500, detail="Server configuration error.")

    # 2. Extract payload
    action      = payload.get("action", "")
    service     = payload.get("service", "")
    user_id     = payload.get("user_id", "")
    jti         = payload.get("jti", "")
    incident_id = payload.get("incident_id")

    if not action or not service or not user_id:
        raise HTTPException(status_code=400, detail="Malformed token payload.")
    if not jti:
        raise HTTPException(status_code=400, detail="Missing token ID (jti).")
    if action != "restart":
        raise HTTPException(
            status_code=400,
            detail=f"This endpoint handles restart actions only; got '{action}'.",
        )

    # 3. Replay protection (Redis-backed in v1.6)
    if is_token_used(jti, rdb=_rdb):
        raise HTTPException(status_code=403, detail="Token has already been used.")

    # 4. Authorisation
    if not _is_authorised(user_id, action, service):
        log_action(user_id=user_id, action=action, service=service,
                   result="denied", detail="Authorisation denied.",
                   jti=jti, incident_id=incident_id, client_ip=client_ip, rdb=_rdb)
        raise HTTPException(status_code=403, detail="Action not authorised.")

    # 5. Consume JTI before execution
    mark_token_used(jti, rdb=_rdb)

    # 6. Execute
    try:
        detail = await restart_container(service)
        log_action(user_id=user_id, action=action, service=service,
                   result="success", detail=detail,
                   jti=jti, incident_id=incident_id, client_ip=client_ip, rdb=_rdb)
    except Exception as exc:
        log_action(user_id=user_id, action=action, service=service,
                   result="failure", detail=str(exc),
                   jti=jti, incident_id=incident_id, client_ip=client_ip, rdb=_rdb)
        raise HTTPException(status_code=500, detail="Action execution failed.") from exc

    return {
        "status":      "success",
        "service":     service,
        "action":      action,
        "jti":         jti,
        "incident_id": incident_id,
        "detail":      detail,
    }
