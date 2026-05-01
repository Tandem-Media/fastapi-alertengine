# orchestrator/lock.py
"""
Redis distributed lock for orchestrator leader safety.

Rules:
- Only ONE orchestrator instance processes an incident at a time
- Lock has TTL (auto-release on crash)
- Lock can be renewed during long processing
- Failure to acquire = skip this cycle (no retry storm)
- Redis unavailable = SAFE DEGRADED MODE (read-only)
"""

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger("orchestrator.lock")

LOCK_TTL_S     = int(os.getenv("LOCK_TTL_S", "30"))
LOCK_PREFIX    = "orchestrator:lock:"
WORKER_ID      = str(uuid.uuid4())[:8]   # unique per process


def _redis():
    import redis
    url = os.getenv("REDIS_URL",
          os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


def acquire_lock(incident_id: str, ttl: int = LOCK_TTL_S) -> Optional[str]:
    """
    Attempt to acquire distributed lock for incident_id.
    Returns lock token if acquired, None if already locked or Redis unavailable.
    """
    key   = f"{LOCK_PREFIX}{incident_id}"
    token = f"{WORKER_ID}:{int(time.time())}"
    try:
        r      = _redis()
        result = r.set(key, token, nx=True, ex=ttl)
        if result:
            logger.debug("Lock acquired: %s (token=%s)", incident_id, token)
            return token
        else:
            holder = r.get(key)
            logger.debug("Lock held by %s — skipping %s", holder, incident_id)
            return None
    except Exception as e:
        logger.error("Lock system unavailable: %s — entering SAFE DEGRADED MODE", e)
        return None


def release_lock(incident_id: str, token: str) -> bool:
    """
    Release lock only if we own it (token match).
    Prevents releasing another worker's lock.
    """
    key = f"{LOCK_PREFIX}{incident_id}"
    try:
        r       = _redis()
        current = r.get(key)
        if current == token:
            r.delete(key)
            logger.debug("Lock released: %s", incident_id)
            return True
        else:
            logger.warning("Lock token mismatch for %s — not releasing", incident_id)
            return False
    except Exception as e:
        logger.error("Lock release failed: %s", e)
        return False


def renew_lock(incident_id: str, token: str, ttl: int = LOCK_TTL_S) -> bool:
    """Extend lock TTL if we still own it."""
    key = f"{LOCK_PREFIX}{incident_id}"
    try:
        r       = _redis()
        current = r.get(key)
        if current == token:
            r.expire(key, ttl)
            logger.debug("Lock renewed: %s", incident_id)
            return True
        return False
    except Exception as e:
        logger.error("Lock renewal failed: %s", e)
        return False


def is_locked(incident_id: str) -> bool:
    """Check if incident is currently locked by any worker."""
    try:
        return bool(_redis().exists(f"{LOCK_PREFIX}{incident_id}"))
    except Exception:
        return False


@asynccontextmanager
async def incident_lock(incident_id: str):
    """
    Async context manager for incident locking.

    Usage:
        async with incident_lock(incident_id) as acquired:
            if not acquired:
                return  # skip this cycle
            # safe to process
    """
    token = acquire_lock(incident_id)
    try:
        yield token is not None
    finally:
        if token:
            release_lock(incident_id, token)
