# fastapi_alertengine/actions/tokens.py
"""
JWT action-token helpers for the fastapi-alertengine remote-action system.

Tokens are short-lived (90 seconds by default) HS256 JWTs that authorise
a single infrastructure action.  The signing secret is read from the
``ACTION_SECRET_KEY`` environment variable; a missing or empty value raises
``RuntimeError`` at call time so mis-configuration is caught immediately
rather than silently falling back to a weak default.

Usage::

    token = generate_action_token("restart", "payments-api", "user-42")
    payload = verify_action_token(token)   # raises on expired / bad sig
"""

import os
import time
from typing import Any
from uuid import uuid4

import jwt

# Token lifetime in seconds.  Short enough to limit replay attacks while
# still giving a WhatsApp delivery a realistic window to be acted upon.
_TOKEN_TTL_SECONDS: int = 90

_ALGORITHM = "HS256"


def _secret() -> str:
    """Return the signing secret, raising if it is absent or empty."""
    secret = os.getenv("ACTION_SECRET_KEY", "")
    if not secret:
        raise RuntimeError(
            "ACTION_SECRET_KEY environment variable is not set. "
            "Set it to a long random string before starting the server."
        )
    return secret


def generate_action_token(action: str, service: str, user_id: str) -> str:
    """
    Create a signed JWT that authorises *action* on *service* for *user_id*.

    Parameters
    ----------
    action:
        The infrastructure action to authorise (e.g. ``"restart"``).
    service:
        The target service or container name (e.g. ``"payments-api"``).
    user_id:
        Opaque identifier of the requesting user.

    Returns
    -------
    str
        Compact URL-safe JWT string, valid for ``_TOKEN_TTL_SECONDS`` seconds.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "jti": str(uuid4()),
        "action": action,
        "service": service,
        "user_id": user_id,
        "iat": now,
        "exp": now + _TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def verify_action_token(token: str) -> dict[str, Any]:
    """
    Decode and validate *token*, returning the payload on success.

    Raises
    ------
    jwt.ExpiredSignatureError
        When the token has expired (``exp`` is in the past).
    jwt.InvalidTokenError
        For any other JWT problem (bad signature, malformed token, etc.).
    """
    return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
