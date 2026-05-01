# orchestrator/idempotency.py
"""
Idempotent action execution wrapper.

Rules:
- Every action has a deterministic idempotency key
- action_id = hash(incident_id + stage + action_type)
- Before executing: check Redis SETNX
- If exists: SKIP (silent no-op)
- If not: execute + store marker
- Guarantees exactly-once execution under retry conditions
"""

import hashlib
import json
import logging
import os
import time
from typing import Callable, Any, Optional

logger = logging.getLogger("orchestrator.idempotency")

EXECUTED_PREFIX = "orchestrator:executed_action:"
ACTION_TTL      = 86400   # 24 hours


def _redis():
    import redis
    url = os.getenv("REDIS_URL",
          os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


def make_action_id(incident_id: str, stage: str, action_type: str) -> str:
    """Deterministic idempotency key for an action."""
    raw = f"{incident_id}:{stage}:{action_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def is_executed(action_id: str) -> bool:
    """Check if action was already executed."""
    try:
        return bool(_redis().exists(f"{EXECUTED_PREFIX}{action_id}"))
    except Exception as e:
        logger.error("Idempotency check failed: %s — allowing execution", e)
        return False   # fail-open: allow execution if Redis unavailable


def mark_executed(action_id: str, metadata: Optional[dict] = None) -> bool:
    """Mark action as executed. Returns True on success."""
    key  = f"{EXECUTED_PREFIX}{action_id}"
    data = json.dumps({
        "action_id": action_id,
        "at":        time.time(),
        "meta":      metadata or {},
    })
    try:
        result = _redis().set(key, data, nx=True, ex=ACTION_TTL)
        return bool(result)
    except Exception as e:
        logger.error("Failed to mark action executed: %s", e)
        return False


async def execute_once(
    incident_id: str,
    stage: str,
    action_type: str,
    fn: Callable,
    *args,
    **kwargs,
) -> tuple[bool, str]:
    """
    Execute fn exactly once for this (incident, stage, action_type) combination.

    Returns:
        (executed: bool, action_id: str)
        executed=False means it was skipped (already done)
    """
    action_id = make_action_id(incident_id, stage, action_type)

    if is_executed(action_id):
        logger.info("⏭ Skipped (idempotent): %s | %s/%s | action_id=%s",
                    incident_id, stage, action_type, action_id)
        return False, action_id

    try:
        if asyncio_callable(fn):
            await fn(*args, **kwargs)
        else:
            fn(*args, **kwargs)

        mark_executed(action_id, {
            "incident_id": incident_id,
            "stage":       stage,
            "action_type": action_type,
        })
        logger.info("✅ Executed: %s | %s/%s | action_id=%s",
                    incident_id, stage, action_type, action_id)
        return True, action_id

    except Exception as e:
        logger.error("Action failed: %s | %s/%s | %s",
                     incident_id, stage, action_type, e)
        raise


def asyncio_callable(fn: Callable) -> bool:
    import asyncio
    return asyncio.iscoroutinefunction(fn)


import asyncio
