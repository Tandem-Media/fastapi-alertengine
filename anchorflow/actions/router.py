# anchorflow/actions/router.py
"""
FastAPI router for AnchorFlow remote infrastructure actions.

Endpoints
---------
GET /action/confirm?token=<jwt>
    Render an HTML confirmation page so the user consciously approves the
    action before it is executed.

GET /action/restart?token=<jwt>
    Verify the action token, enforce single-use (replay protection), call
    restart_container, audit-log the result, and return a structured JSON
    response.

Security
--------
* Every request requires a valid, non-expired HS256 JWT signed with
  ``ACTION_SECRET_KEY``.
* Expired or tampered tokens yield HTTP 403.
* Tokens include a ``jti`` (JWT ID); replaying a consumed token yields 403.
* Malformed / missing query parameters yield HTTP 400.
* User-authorisation is checked via ``_is_authorised``; replace the stub
  with your real ACL / RBAC layer.
"""

import logging

import jwt
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from anchorflow.actions.audit import log_action
from anchorflow.actions.replay import is_token_used, mark_token_used
from anchorflow.actions.services import restart_container
from anchorflow.actions.tokens import verify_action_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/action", tags=["remote-actions"])


# ── Authorisation stub ────────────────────────────────────────────────────────


def _is_authorised(user_id: str, action: str, service: str) -> bool:
    """
    Return True if *user_id* is permitted to perform *action* on *service*.

    Replace this stub with your real ACL / RBAC implementation.
    """
    # Stub: all non-empty user IDs are permitted.
    return bool(user_id)


# ── Confirmation page ─────────────────────────────────────────────────────────


@router.get("/confirm", response_class=HTMLResponse)
async def confirm_action(
    token: str = Query(..., description="Signed JWT action token"),
) -> HTMLResponse:
    """
    Render an HTML confirmation page for the requested infrastructure action.

    The user must click the "Confirm" button to execute the action.  This
    prevents accidental or automated triggering via link-preview crawlers.

    Returns
    -------
    HTMLResponse
        200 with a confirmation form, or 403 for an invalid/expired token.
    """
    try:
        payload = verify_action_token(token)
    except Exception:
        return HTMLResponse(
            "<h3>Invalid or expired token</h3>",
            status_code=403,
        )

    action = payload.get("action", "")
    service = payload.get("service", "")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Confirm Action — AnchorFlow</title>
</head>
<body>
  <h2>Confirm Infrastructure Action</h2>
  <p>Action: <strong>{action}</strong></p>
  <p>Service: <strong>{service}</strong></p>
  <form method="get" action="/action/{action}">
    <input type="hidden" name="token" value="{token}">
    <button type="submit">Confirm {action}</button>
  </form>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── Execution endpoint ────────────────────────────────────────────────────────


@router.get("/restart")
async def action_restart(
    token: str = Query(..., description="Signed JWT action token"),
) -> dict:
    """
    Execute a service restart authorised by a signed JWT action token.

    The token must have been created by ``generate_action_token`` with
    ``action="restart"`` and must not have expired.

    Returns
    -------
    JSON with ``status``, ``service``, and ``action`` on success.

    Raises
    ------
    403 Forbidden
        Token is expired, has an invalid signature, or the user is not
        authorised to perform the action.
    400 Bad Request
        Token query parameter is missing or structurally malformed.
    """
    # ── 1. Verify token ───────────────────────────────────────────────────────
    try:
        payload = verify_action_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=403, detail="Action token has expired.")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=403, detail=f"Invalid action token: {exc}")
    except RuntimeError as exc:
        # Signing secret not configured — server-side misconfiguration.
        logger.error("ACTION_SECRET_KEY not configured: %s", exc)
        raise HTTPException(status_code=500, detail="Server configuration error.")

    # ── 2. Extract payload fields ─────────────────────────────────────────────
    action = payload.get("action", "")
    service = payload.get("service", "")
    user_id = payload.get("user_id", "")
    jti = payload.get("jti", "")

    if not action or not service or not user_id:
        raise HTTPException(status_code=400, detail="Malformed token payload.")

    if not jti:
        raise HTTPException(status_code=400, detail="Missing token ID (jti).")

    # ── 3. Replay protection ──────────────────────────────────────────────────
    if is_token_used(jti):
        raise HTTPException(status_code=403, detail="Token has already been used.")

    # ── 4. Validate action type ───────────────────────────────────────────────
    if action != "restart":
        raise HTTPException(
            status_code=400,
            detail=f"This endpoint only handles 'restart' actions; got '{action}'.",
        )

    # ── 5. Authorisation check ────────────────────────────────────────────────
    if not _is_authorised(user_id, action, service):
        log_action(
            user_id=user_id,
            action=action,
            service=service,
            result="failure",
            detail="Authorisation denied.",
        )
        raise HTTPException(status_code=403, detail="Action not authorised.")

    # ── 6. Consume token (replay protection) ──────────────────────────────────
    # Mark before execution so that a transient handler failure still
    # prevents a second attempt with the same token.
    mark_token_used(jti)

    # ── 7. Execute action ─────────────────────────────────────────────────────
    try:
        detail = await restart_container(service)
        log_action(
            user_id=user_id,
            action=action,
            service=service,
            result="success",
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001
        log_action(
            user_id=user_id,
            action=action,
            service=service,
            result="failure",
            detail=str(exc),
        )
        raise HTTPException(status_code=500, detail="Action execution failed.") from exc

    return {"status": "success", "service": service, "action": action}
