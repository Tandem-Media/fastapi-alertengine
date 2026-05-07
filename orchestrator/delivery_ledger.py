"""
Delivery ledger — persists every notification attempt to Redis.
Provides proof of delivery and fallback audit trail.
No silent failures: every attempt is recorded regardless of outcome.

Redis key: orchestrator:delivery:{incident_id}
Structure: Redis LIST of JSON DeliveryResult entries
TTL: 7 days
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("orchestrator.delivery_ledger")

LEDGER_PREFIX = "orchestrator:delivery:"
LEDGER_TTL    = 86400 * 7   # 7 days


def _redis():
    import redis
    url = os.getenv("REDIS_URL",
          os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


def record(
    incident_id: str,
    provider: str,
    channel: str,
    tenant_id: str,
    success: bool,
    error: Optional[str] = None,
    message_id: Optional[str] = None,
) -> bool:
    """
    Record a notification delivery attempt.
    Returns True on success. Never raises.
    """
    entry = {
        "incident_id":  incident_id,
        "provider":     provider,
        "channel":      channel,
        "tenant_id":    tenant_id,
        "success":      success,
        "error":        error,
        "message_id":   message_id,
        "attempted_at": time.time(),
    }
    try:
        r   = _redis()
        key = f"{LEDGER_PREFIX}{incident_id}"
        r.rpush(key, json.dumps(entry))
        r.expire(key, LEDGER_TTL)
        status = "✓" if success else "✗"
        logger.debug("Delivery ledger [%s] %s/%s tenant=%s",
                     status, provider, channel, tenant_id)
        return True
    except Exception as e:
        logger.error("Delivery ledger write failed: %s — entry lost: %s",
                     e, entry)
        return False


def record_from_result(result) -> bool:
    """
    Record a DeliveryResult from providers/base.py.
    Convenience wrapper.
    """
    return record(
        incident_id=result.incident_id,
        provider=result.provider,
        channel=result.channel,
        tenant_id=result.tenant_id,
        success=result.success,
        error=result.error,
        message_id=result.message_id,
    )


def get_delivery_log(incident_id: str) -> list:
    """Return full delivery log for an incident."""
    try:
        r       = _redis()
        key     = f"{LEDGER_PREFIX}{incident_id}"
        entries = r.lrange(key, 0, -1)
        return [json.loads(e) for e in entries]
    except Exception as e:
        logger.error("Delivery ledger read failed: %s", e)
        return []


def all_failed(incident_id: str) -> bool:
    """
    Returns True if ALL delivery attempts for an incident failed.
    Used to detect total notification blackout.
    """
    log = get_delivery_log(incident_id)
    if not log:
        return False
    return all(not e.get("success") for e in log)
