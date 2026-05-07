"""
Redis-backed distributed circuit breaker.

State is shared across all workers and replicas via Redis.
Breaker state is scoped per provider and tenant.

Redis key: orchestrator:cb:{provider}:{tenant_id}
Fields: failure_count (int), disabled_until (float timestamp)

Providers: "whatsapp", "telegram", "webhook"
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger("orchestrator.circuit_breaker")

CB_PREFIX = "orchestrator:cb:"
CB_TTL = 3600  # 1 hour — auto-expire stale breaker state
CB_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))
CB_COOLDOWN_S = int(os.getenv("CB_COOLDOWN_S", "60"))


def _redis():
    import redis

    url = os.getenv(
        "REDIS_URL", os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0")
    )
    return redis.Redis.from_url(url, decode_responses=True)


def _key(provider: str, tenant_id: str = "global") -> str:
    return f"{CB_PREFIX}{provider}:{tenant_id}"


def is_open(provider: str, tenant_id: str = "global") -> bool:
    """
    Returns True if the circuit breaker is open (suppressing).
    Auto-resets if cooldown has expired.
    Never raises.
    """
    try:
        r = _redis()
        key = _key(provider, tenant_id)
        raw = r.hgetall(key)
        if not raw:
            return False

        failure_count = int(raw.get("failure_count", 0))
        disabled_until = float(raw.get("disabled_until", 0.0))

        if failure_count < CB_THRESHOLD:
            return False

        if time.time() < disabled_until:
            return True

        # Cooldown expired — reset
        r.delete(key)
        logger.info(
            "CB reset (cooldown expired): provider=%s tenant=%s", provider, tenant_id
        )
        return False

    except Exception as e:
        logger.error("CB check failed: %s — failing closed (allow send)", e)
        return False  # fail-open: allow send if Redis unavailable


def record_failure(provider: str, tenant_id: str = "global") -> int:
    """
    Increment failure count. Opens breaker if threshold reached.
    Returns current failure count. Never raises.
    """
    try:
        r = _redis()
        key = _key(provider, tenant_id)
        count = r.hincrby(key, "failure_count", 1)
        r.expire(key, CB_TTL)

        if count >= CB_THRESHOLD:
            disabled_until = time.time() + CB_COOLDOWN_S
            r.hset(key, "disabled_until", disabled_until)
            logger.warning(
                "CB OPEN: provider=%s tenant=%s failures=%d cooldown=%ds",
                provider,
                tenant_id,
                count,
                CB_COOLDOWN_S,
            )

        return count
    except Exception as e:
        logger.error("CB record_failure failed: %s", e)
        return 0


def record_success(provider: str, tenant_id: str = "global") -> None:
    """
    Reset circuit breaker on successful send. Never raises.
    """
    try:
        r = _redis()
        key = _key(provider, tenant_id)
        existing: Optional[dict] = r.hgetall(key)
        if existing:
            r.delete(key)
            logger.info("CB reset on success: provider=%s tenant=%s", provider, tenant_id)
    except Exception as e:
        logger.error("CB record_success failed: %s", e)
