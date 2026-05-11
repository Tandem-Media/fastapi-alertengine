# orchestrator/lock.py
"""
Redis distributed lease lock for orchestrator leader safety.

Guarantees:
- Only ONE orchestrator instance processes an incident at a time
- Ownership-safe release via Lua compare-and-delete
- Automatic lease renewal while processing
- Lease-loss detection during long-running operations
- TTL auto-release on crash
- Redis unavailable = SAFE DEGRADED MODE (fail closed)

Design:
- Lock = renewable lease, not a one-shot mutex
- Renewal task runs in background every ttl/3 seconds
- If lease renewal fails, lease.valid becomes False
- Caller should check lease.valid before dangerous mutations
- Ownership token is cryptographically random UUID
"""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("orchestrator.lock")

LOCK_TTL_S  = int(os.getenv("LOCK_TTL_S", "30"))
LOCK_PREFIX = "orchestrator:lock:"
WORKER_ID   = str(uuid.uuid4())[:8]

# Singleton Redis client — avoids reconnecting on every operation
_REDIS = None


RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


RENEW_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


@dataclass
class Lease:
    """
    Active distributed lease state.

    valid:
        True  → lease is held and current
        False → lease was lost (renewal failed or released)

    Callers should check lease.valid before executing
    dangerous or irreversible actions during long operations.
    """
    incident_id: str
    token:       str
    valid:       bool = True

    def invalidate(self) -> None:
        self.valid = False


def _redis():
    """
    Shared Redis client singleton.
    Avoids reconnecting on every lock operation.
    """
    global _REDIS

    if _REDIS is None:
        import redis

        url = os.getenv(
            "REDIS_URL",
            os.getenv(
                "ALERTENGINE_REDIS_URL",
                "redis://localhost:6379/0",
            ),
        )

        _REDIS = redis.Redis.from_url(
            url,
            decode_responses=True,
        )

    return _REDIS


def acquire_lock(
    incident_id: str,
    ttl: int = LOCK_TTL_S,
) -> Optional[Lease]:
    """
    Acquire distributed lease for incident_id.

    Returns:
        Lease object if acquired
        None if already locked or Redis unavailable
    """
    key   = f"{LOCK_PREFIX}{incident_id}"
    token = str(uuid.uuid4())

    try:
        r = _redis()

        acquired = r.set(
            key,
            token,
            nx=True,
            ex=ttl,
        )

        if not acquired:
            holder = r.get(key)
            logger.debug(
                "Lease held by another worker | incident=%s holder=%s",
                incident_id, holder,
            )
            return None

        logger.debug(
            "Lease acquired | incident=%s worker=%s",
            incident_id, WORKER_ID,
        )

        return Lease(
            incident_id=incident_id,
            token=token,
        )

    except Exception as e:
        logger.error(
            "Lease system unavailable: %s — SAFE DEGRADED MODE", e,
        )
        return None


def release_lock(lease: Lease) -> bool:
    """
    Release lease only if still owned by this worker.
    Uses atomic Lua compare-and-delete — prevents releasing
    another worker's lease after TTL expiry.
    """
    key = f"{LOCK_PREFIX}{lease.incident_id}"

    try:
        result = _redis().eval(
            RELEASE_LOCK_SCRIPT,
            1,
            key,
            lease.token,
        )

        if result == 1:
            logger.debug(
                "Lease released | incident=%s", lease.incident_id,
            )
            return True

        logger.warning(
            "Lease release rejected (ownership lost) | incident=%s",
            lease.incident_id,
        )
        return False

    except Exception as e:
        logger.error(
            "Lease release failed | incident=%s error=%s",
            lease.incident_id, e,
        )
        return False


def renew_lock(
    lease: Lease,
    ttl: int = LOCK_TTL_S,
) -> bool:
    """
    Extend lease TTL if we still own it.
    Uses atomic Lua compare-and-expire.

    Returns:
        True  → renewed successfully
        False → lease lost or Redis failure
                lease.valid is set to False on failure
    """
    key = f"{LOCK_PREFIX}{lease.incident_id}"

    try:
        result = _redis().eval(
            RENEW_LOCK_SCRIPT,
            1,
            key,
            lease.token,
            ttl,
        )

        if result == 1:
            logger.debug(
                "Lease renewed | incident=%s", lease.incident_id,
            )
            return True

        logger.error(
            "Lease renewal rejected (ownership lost) | incident=%s",
            lease.incident_id,
        )
        lease.invalidate()
        return False

    except Exception as e:
        logger.error(
            "Lease renewal failed | incident=%s error=%s",
            lease.incident_id, e,
        )
        lease.invalidate()
        return False


def is_locked(incident_id: str) -> bool:
    """
    Check if incident currently has an active lease.
    Read-only — does not acquire or modify anything.
    """
    try:
        return bool(
            _redis().exists(f"{LOCK_PREFIX}{incident_id}")
        )
    except Exception:
        return False


async def _auto_renew_loop(
    lease: Lease,
    ttl: int,
) -> None:
    """
    Background lease renewal task.

    Renews every ttl/3 seconds until:
    - context manager exits (task cancelled)
    - renewal fails (lease lost or Redis down)
    - lease is already invalidated

    On renewal failure, lease.valid is set False
    so callers can detect lease loss mid-processing.
    """
    interval = max(ttl / 3, 1)

    try:
        while lease.valid:
            await asyncio.sleep(interval)

            if not lease.valid:
                return

            renewed = renew_lock(lease, ttl)

            if not renewed:
                logger.error(
                    "Lease lost during processing | incident=%s",
                    lease.incident_id,
                )
                return

    except asyncio.CancelledError:
        logger.debug(
            "Lease renewer cancelled | incident=%s",
            lease.incident_id,
        )
        raise

    except Exception as e:
        logger.error(
            "Lease renewer crashed | incident=%s error=%s",
            lease.incident_id, e,
        )
        lease.invalidate()


@asynccontextmanager
async def incident_lock(
    incident_id: str,
    ttl: int = LOCK_TTL_S,
):
    """
    Distributed renewable lease context manager.

    Yields:
        Lease object if acquired (check lease.valid during processing)
        None if not acquired (another worker holds it)

    Usage:
        async with incident_lock(incident_id) as lease:
            if not lease:
                return  # another worker owns this

            # ... do work ...

            if not lease.valid:
                return  # lease lost mid-processing, abort mutation

    The background auto-renewer runs every ttl/3 seconds.
    If renewal fails, lease.valid becomes False automatically.
    Callers should check lease.valid before irreversible actions.
    """
    lease = acquire_lock(incident_id, ttl=ttl)
    renewer_task = None

    if lease:
        renewer_task = asyncio.create_task(
            _auto_renew_loop(lease, ttl)
        )

    try:
        yield lease

    finally:
        if renewer_task:
            renewer_task.cancel()
            try:
                await renewer_task
            except asyncio.CancelledError:
                pass

        if lease:
            release_lock(lease)
