# token_utils.py
"""
Thin wrapper around AlertEngine's existing JWT action token system.

AlertEngine already provides:
- HS256-signed tokens
- Configurable TTL (default 90s)
- JTI replay protection via Redis
- IP binding (optional)

This module exposes a clean interface for demo_app.py.
"""

import os
import logging

logger = logging.getLogger("demo.tokens")

SECRET = os.getenv("ACTION_SECRET_KEY", os.getenv("ALERT_SECRET", "dev-secret-change-this-in-prod"))

# In-memory replay protection (Redis-backed in production via AlertEngine)
_USED_TOKENS: set = set()


def generate_recovery_token(incident_id: str, ttl_seconds: int = 90) -> str:
    """Generate a signed, expiring recovery token tied to an incident ID."""
    try:
        from fastapi_alertengine.actions.tokens import generate_action_token
        token = generate_action_token(
            action="restart",
            service="payments-api",
            secret=SECRET,
            ttl=ttl_seconds,
            extra={"incident_id": incident_id},
        )
        logger.info("Token generated for incident %s (TTL=%ds)", incident_id, ttl_seconds)
        return token
    except Exception as e:
        logger.warning("AlertEngine token failed (%s) — falling back to PyJWT", e)
        return _fallback_token(incident_id, ttl_seconds)


def verify_recovery_token(token: str) -> dict | None:
    """Verify token signature and expiry. Returns payload or None."""
    try:
        from fastapi_alertengine.actions.tokens import verify_action_token
        payload = verify_action_token(token, secret=SECRET)
        return payload
    except Exception:
        return _fallback_verify(token)


def consume_token(token: str) -> bool:
    """
    Mark token as used. Returns False if already consumed (replay attack).
    Uses AlertEngine's Redis-backed JTI store when available.
    """
    try:
        from fastapi_alertengine.actions.replay_store import check_and_consume_jti
        payload = verify_recovery_token(token)
        if not payload:
            return False
        jti = payload.get("jti")
        if not jti:
            # No JTI — fall back to in-memory
            if token in _USED_TOKENS:
                return False
            _USED_TOKENS.add(token)
            return True
        return check_and_consume_jti(jti)
    except Exception:
        # Fallback: in-memory set
        if token in _USED_TOKENS:
            return False
        _USED_TOKENS.add(token)
        return True


# ── PyJWT fallback (if AlertEngine token API changes) ─────────────────────────

def _fallback_token(incident_id: str, ttl: int) -> str:
    import time, jwt
    payload = {
        "action":      "restart",
        "incident_id": incident_id,
        "exp":         int(time.time()) + ttl,
        "iat":         int(time.time()),
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _fallback_verify(token: str) -> dict | None:
    try:
        import jwt
        return jwt.decode(token, SECRET, algorithms=["HS256"])
    except Exception:
        return None
