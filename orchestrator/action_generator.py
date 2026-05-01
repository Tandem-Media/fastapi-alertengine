# orchestrator/action_generator.py
"""
JWT recovery token generation and validation.
Token is tied to incident ID, expires in TTL seconds, single-use via Redis.
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger("orchestrator.actions")

SECRET  = os.getenv("ALERT_SECRET", "change-this-in-prod")
TTL_S   = int(os.getenv("RECOVERY_TOKEN_TTL_S", "300"))   # 5 minutes
USED_KEY_PREFIX = "orchestrator:used_token:"
USED_TTL        = 600   # 10 min expiry on used-token keys


def generate_recovery_token(incident_id: str, ttl: Optional[int] = None) -> str:
    """Generate a signed JWT recovery token tied to incident_id."""
    import jwt
    ttl = ttl or TTL_S
    payload = {
        "incident_id": incident_id,
        "iat":         int(time.time()),
        "exp":         int(time.time()) + ttl,
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    logger.info("Token generated for %s (TTL=%ds)", incident_id, ttl)
    return token


def verify_recovery_token(token: str) -> Optional[dict]:
    """Verify JWT signature and expiry. Returns payload or None."""
    try:
        import jwt
        return jwt.decode(token, SECRET, algorithms=["HS256"])
    except Exception as e:
        logger.warning("Token verification failed: %s", e)
        return None


def consume_token(token: str) -> bool:
    """
    Mark token as used. Redis-backed — survives restarts.
    Returns True if first use, False if replay.
    """
    import hashlib
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    key = f"{USED_KEY_PREFIX}{token_hash}"

    try:
        import redis
        url = os.getenv("REDIS_URL", os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
        r   = redis.Redis.from_url(url, decode_responses=True)

        # SET NX — atomic check-and-set
        result = r.set(key, "1", nx=True, ex=USED_TTL)
        if result:
            logger.info("Token consumed: %s...", token_hash)
            return True
        else:
            logger.warning("Replay blocked: %s...", token_hash)
            return False
    except Exception as e:
        logger.error("Token store failed: %s — falling back to allow", e)
        # Fail-open for demo resilience
        return True


def validate_and_consume(token: str) -> tuple[bool, Optional[dict]]:
    """
    Full validation pipeline: verify → consume → return payload.
    Returns (valid: bool, payload: dict | None).
    """
    payload = verify_recovery_token(token)
    if not payload:
        return False, None

    if not consume_token(token):
        return False, None

    return True, payload
