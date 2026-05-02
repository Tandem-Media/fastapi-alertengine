# orchestrator/action_generator.py
"""
Tenant-scoped JWT recovery token generation and validation.

Token payload includes:
    tenant_id, incident_id, action, expiry

Validation:
    - rejects cross-tenant execution
    - rejects reused tokens (Redis SET NX)
    - rejects expired tokens
"""

import hashlib
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("orchestrator.actions")

SECRET          = os.getenv("ALERT_SECRET", "change-this-in-prod")
TTL_S           = int(os.getenv("RECOVERY_TOKEN_TTL_S", "300"))
USED_KEY_PREFIX = "orchestrator:used_token:"
USED_TTL        = 600


def generate_recovery_token(
    incident_id: str,
    tenant_id:   str = "default",
    action:      str = "restart",
    ttl:         Optional[int] = None,
) -> str:
    """Generate a signed JWT recovery token scoped to tenant + incident."""
    import jwt
    ttl = ttl or TTL_S
    payload = {
        "incident_id": incident_id,
        "tenant_id":   tenant_id,
        "action":      action,
        "iat":         int(time.time()),
        "exp":         int(time.time()) + ttl,
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    logger.info("Token generated: incident=%s tenant=%s action=%s TTL=%ds",
                incident_id, tenant_id, action, ttl)
    return token


def verify_recovery_token(token: str) -> Optional[dict]:
    """Verify JWT signature and expiry. Returns payload or None."""
    try:
        import jwt
        return jwt.decode(token, SECRET, algorithms=["HS256"])
    except Exception as e:
        logger.warning("Token verification failed: %s", e)
        return None


def consume_token(token: str, expected_tenant_id: Optional[str] = None) -> tuple[bool, str]:
    """
    Full token validation:
    1. Verify signature + expiry
    2. Check tenant_id matches (cross-tenant rejection)
    3. Atomic SET NX — replay protection

    Returns (valid: bool, reason: str)
    """
    payload = verify_recovery_token(token)
    if not payload:
        return False, "Invalid or expired token"

    # Cross-tenant check
    if expected_tenant_id and payload.get("tenant_id") != expected_tenant_id:
        logger.warning("Cross-tenant token rejected: token=%s expected=%s",
                       payload.get("tenant_id"), expected_tenant_id)
        return False, "Token belongs to different tenant"

    # Replay protection
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    key        = f"{USED_KEY_PREFIX}{token_hash}"

    try:
        import redis
        r      = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        result = r.set(key, "1", nx=True, ex=USED_TTL)
        if result:
            logger.info("Token consumed: %s... tenant=%s",
                        token_hash, payload.get("tenant_id"))
            return True, "ok"
        else:
            logger.warning("Replay blocked: %s...", token_hash)
            return False, "Token already used"
    except Exception as e:
        logger.error("Token store failed: %s — failing open", e)
        return True, "ok"   # fail-open for resilience


def validate_and_consume(
    token: str,
    expected_tenant_id: Optional[str] = None,
) -> tuple[bool, Optional[dict], str]:
    """
    Full pipeline: verify → tenant check → consume.
    Returns (valid, payload, reason).
    """
    payload = verify_recovery_token(token)
    if not payload:
        return False, None, "Invalid or expired token"

    valid, reason = consume_token(token, expected_tenant_id)
    if not valid:
        return False, None, reason

    return True, payload, "ok"
