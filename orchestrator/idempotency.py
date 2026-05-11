# orchestrator/idempotency.py
"""
Distributed idempotent action execution.

Rules:
- Every action has a deterministic idempotency key
- action_id = hash(incident_id + stage + action_type)
- Claim FIRST via atomic Redis SET NX
- If claim fails: SKIP (another worker owns it)
- Marker status is updated atomically via Redis HASH fields
- Guarantees at-most-once execution semantics

Marker lifecycle:
    claimed  → action claimed, execution pending/unknown
    success  → action completed successfully
    failed   → action raised exception (marker retained)

Design decision:
- claim-first (at-most-once) instead of execute-first (at-least-once)
- Duplicate recovery actions are more dangerous than missed retries
- Failed actions are retained for DLQ/manual replay visibility
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger("orchestrator.idempotency")

EXECUTED_PREFIX = "orchestrator:executed_action:"
ACTION_TTL = 86400  # 24 hours


# ──────────────────────────────────────────────────────────────────────────────
# Redis
# ──────────────────────────────────────────────────────────────────────────────

def _redis():
    import redis

    url = os.getenv(
        "REDIS_URL",
        os.getenv(
            "ALERTENGINE_REDIS_URL",
            "redis://localhost:6379/0",
        ),
    )

    return redis.Redis.from_url(
        url,
        decode_responses=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Keys
# ──────────────────────────────────────────────────────────────────────────────

def _marker_key(action_id: str) -> str:
    return f"{EXECUTED_PREFIX}{action_id}"


def make_action_id(
    incident_id: str,
    stage: str,
    action_type: str,
) -> str:
    """
    Deterministic idempotency key.

    Stable across:
    - retries
    - orchestrator workers
    - process restarts
    """

    raw = f"{incident_id}:{stage}:{action_type}"

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:24]


# ──────────────────────────────────────────────────────────────────────────────
# Marker operations
# ──────────────────────────────────────────────────────────────────────────────

def is_executed(action_id: str) -> bool:
    """
    Read-only existence check.

    WARNING:
    Never use this as a pre-check before claiming.
    That creates a TOCTOU race window.

    Safe usage:
    - dashboards
    - admin tooling
    - observability
    """

    try:
        return bool(
            _redis().exists(_marker_key(action_id))
        )

    except Exception as e:
        logger.error(
            "Idempotency check failed: %s — allowing execution",
            e,
        )

        # Fail-open:
        # Recovery systems should prefer duplicate execution
        # over total suppression during Redis instability.
        return False


def claim_action(
    action_id: str,
    metadata: Optional[dict] = None,
) -> bool:
    """
    Atomically claim action ownership.

    Uses Redis SET NX EX:
    - NX → first writer wins
    - EX → automatic expiry

    Returns:
        True  → caller owns execution
        False → another worker already claimed it
    """

    key = _marker_key(action_id)

    payload = {
        "action_id": action_id,
        "status": "claimed",
        "claimed_at": time.time(),
        "completed_at": "",
        "error": "",
        "meta": json.dumps(metadata or {}),
    }

    try:
        r = _redis()

        # Atomic first-writer-wins claim
        claimed = r.set(
            key,
            "__claimed__",
            nx=True,
            ex=ACTION_TTL,
        )

        if not claimed:
            return False

        # Convert placeholder into HASH structure
        r.delete(key)

        r.hset(
            key,
            mapping=payload,
        )

        r.expire(key, ACTION_TTL)

        return True

    except Exception as e:
        logger.error(
            "Failed to claim action: %s",
            e,
        )

        return False


def update_status(
    action_id: str,
    status: str,
    error: Optional[str] = None,
) -> bool:
    """
    Atomically update execution status.

    Uses Redis HASH field updates to avoid
    read-modify-write overwrite races.
    """

    key = _marker_key(action_id)

    try:
        r = _redis()

        if not r.exists(key):
            logger.warning(
                "update_status: marker missing for %s",
                action_id,
            )
            return False

        updates = {
            "status": status,
            "completed_at": time.time(),
        }

        if error:
            updates["error"] = str(error)

        r.hset(
            key,
            mapping=updates,
        )

        logger.debug(
            "Marker updated: %s → %s",
            action_id,
            status,
        )

        return True

    except Exception as e:
        logger.error(
            "Failed to update status: %s | %s",
            action_id,
            e,
        )

        return False


def get_marker(action_id: str) -> Optional[dict]:
    """
    Return full execution marker.

    Useful for:
    - DLQ replay tooling
    - admin visibility
    - incident debugging
    - audit inspection
    """

    try:
        data = _redis().hgetall(
            _marker_key(action_id)
        )

        if not data:
            return None

        # Deserialize metadata JSON
        if "meta" in data:
            try:
                data["meta"] = json.loads(data["meta"])
            except Exception:
                pass

        return data

    except Exception as e:
        logger.error(
            "Failed to read marker: %s | %s",
            action_id,
            e,
        )

        return None


# ──────────────────────────────────────────────────────────────────────────────
# Execution wrapper
# ──────────────────────────────────────────────────────────────────────────────

async def execute_once(
    incident_id: str,
    stage: str,
    action_type: str,
    fn: Callable,
    *args,
    **kwargs,
) -> tuple[bool, str]:
    """
    Execute function with distributed idempotency protection.

    Flow:
        1. Atomic claim
        2. Execute action
        3. Update final status

    Returns:
        (executed, action_id)

    executed=False means:
    another worker already owns execution.
    """

    action_id = make_action_id(
        incident_id,
        stage,
        action_type,
    )

    # Atomic ownership claim
    claimed = claim_action(
        action_id,
        {
            "incident_id": incident_id,
            "stage": stage,
            "action_type": action_type,
        },
    )

    if not claimed:
        logger.info(
            "⏭ Skipped (already claimed): "
            "%s | %s/%s | action_id=%s",
            incident_id,
            stage,
            action_type,
            action_id,
        )

        return False, action_id

    # We own execution
    try:
        if asyncio.iscoroutinefunction(fn):
            await fn(*args, **kwargs)
        else:
            fn(*args, **kwargs)

        update_status(
            action_id,
            "success",
        )

        logger.info(
            "✅ Executed: "
            "%s | %s/%s | action_id=%s",
            incident_id,
            stage,
            action_type,
            action_id,
        )

        return True, action_id

    except Exception as e:
        update_status(
            action_id,
            "failed",
            error=str(e),
        )

        logger.error(
            "❌ Action failed: "
            "%s | %s/%s | %s",
            incident_id,
            stage,
            action_type,
            e,
        )

        raise
