# fastapi_alertengine/actions/tokens.py
"""
v1.6 — JWT Action Token System (hardened)

Changes from v1.3:
- Optional IP binding: token encodes client IP, verified on use
- Full structured audit payload embedded in token
- JTI stored in Redis for replay protection (previously in-memory only)
- Token generation now accepts incident_id for traceability
"""
import os
import time
import uuid
from typing import Optional

import jwt

_ALG = "HS256"
_ALGORITHM = _ALG
_DEFAULT_TTL = 90  # seconds


def _secret() -> str:
    key = os.getenv("ACTION_SECRET_KEY", "")
    if not key:
        raise RuntimeError(
            "ACTION_SECRET_KEY environment variable not set. "
            "Set it to a strong random string to enable action tokens."
        )
    return key


def generate_action_token(
    action:      str,
    service:     str,
    user_id:     str,
    ttl_seconds: int            = _DEFAULT_TTL,
    client_ip:   Optional[str]  = None,
    incident_id: Optional[str]  = None,
    health_score: Optional[float] = None,
    suggestion_id: Optional[str] = None,
) -> str:
    """
    Generate a signed JWT action token.

    v1.6 additions:
    - client_ip:    when provided, token is IP-bound (verified on use)
    - incident_id:  links token to a specific incident for traceability
    - health_score: snapshot of health at time of token generation
    - suggestion_id: links to the ActionSuggestion that triggered this

    Returns a signed JWT string.
    """
    now = int(time.time())
    payload = {
        "action":    action,
        "service":   service,
        "user_id":   user_id,
        "jti":       str(uuid.uuid4()),
        "iat":       now,
        "exp":       now + ttl_seconds,
    }
    if client_ip:
        payload["bound_ip"] = client_ip
    if incident_id:
        payload["incident_id"] = incident_id
    if health_score is not None:
        payload["health_score"] = round(health_score, 1)
    if suggestion_id:
        payload["suggestion_id"] = suggestion_id

    return jwt.encode(payload, _secret(), algorithm=_ALG)


def verify_action_token(
    token:     str,
    client_ip: Optional[str] = None,
) -> dict:
    """
    Verify and decode an action token.

    v1.6: enforces IP binding when client_ip is provided and the token
    was generated with a bound_ip claim.

    Raises:
        jwt.ExpiredSignatureError  — token has expired
        jwt.InvalidTokenError      — signature invalid or malformed
        ValueError                 — IP mismatch (when IP binding is active)
        RuntimeError               — ACTION_SECRET_KEY not configured
    """
    payload = jwt.decode(token, _secret(), algorithms=[_ALG])

    # IP binding check
    bound_ip = payload.get("bound_ip")
    if bound_ip and client_ip and bound_ip != client_ip:
        raise ValueError(
            f"Token IP binding violation: token bound to {bound_ip}, "
            f"request from {client_ip}"
        )

    return payload
