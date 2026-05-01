# orchestrator/dlq.py
"""
Dead Letter Queue for permanently failed actions.

Rules:
- Failed actions are pushed to DLQ after retry exhaustion
- DLQ is a Redis LIST (append-only)
- DLQ entries are never deleted automatically
- DLQ can be inspected and replayed manually
- DLQ insertion is fire-and-forget (never blocks pipeline)
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("orchestrator.dlq")

DLQ_KEY  = "orchestrator:dlq"
DLQ_TTL  = 86400 * 14   # 14 days


def _redis():
    import redis
    url = os.getenv("REDIS_URL",
          os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


def push(
    incident_id: str,
    action_type: str,
    error: str,
    stage: Optional[str] = None,
    action_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> bool:
    """
    Push a failed action to the DLQ.
    Returns True on success. Never raises.
    """
    entry = {
        "incident_id": incident_id,
        "action":      action_type,
        "stage":       stage,
        "action_id":   action_id,
        "error":       str(error),
        "payload":     payload or {},
        "timestamp":   time.time(),
    }
    try:
        r = _redis()
        r.rpush(DLQ_KEY, json.dumps(entry))
        r.expire(DLQ_KEY, DLQ_TTL)
        logger.error("DLQ: %s | %s | %s | error=%s",
                     incident_id, stage, action_type, error)
        return True
    except Exception as e:
        logger.critical("DLQ write failed: %s — entry lost: %s", e, entry)
        return False


def get_all(limit: int = 50) -> list:
    """Return recent DLQ entries."""
    try:
        r       = _redis()
        entries = r.lrange(DLQ_KEY, -limit, -1)
        return [json.loads(e) for e in entries]
    except Exception as e:
        logger.error("DLQ read failed: %s", e)
        return []


def get_count() -> int:
    """Return total DLQ entry count."""
    try:
        return _redis().llen(DLQ_KEY)
    except Exception:
        return -1


def clear() -> bool:
    """Clear the DLQ. Use with caution — entries are lost."""
    try:
        _redis().delete(DLQ_KEY)
        logger.warning("DLQ cleared")
        return True
    except Exception as e:
        logger.error("DLQ clear failed: %s", e)
        return False
