# orchestrator/audit.py
"""
Immutable append-only audit log for incident decisions.

Rules:
- Every state transition is appended to Redis LIST
- No updates — append only
- Used for forensic replay and debugging
- Required for fintech-grade compliance
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("orchestrator.audit")

AUDIT_PREFIX = "orchestrator:audit:"
AUDIT_TTL    = 86400 * 7   # 7 days


def _redis():
    import redis
    url = os.getenv("REDIS_URL",
          os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


def append_event(
    incident_id: str,
    stage: str,
    decision: str,
    reason: str,
    confidence: float,
    actor: str = "pipeline",
    action_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> bool:
    """
    Append an immutable audit event for an incident.
    Returns True on success.
    """
    key   = f"{AUDIT_PREFIX}{incident_id}"
    entry = {
        "timestamp":   time.time(),
        "incident_id": incident_id,
        "stage":       stage,
        "decision":    decision,
        "actor":       actor,
        "reason":      reason,
        "confidence":  confidence,
    }
    if action_id:
        entry["action_id"] = action_id
    if metadata:
        entry["metadata"] = metadata

    try:
        r = _redis()
        r.rpush(key, json.dumps(entry))
        r.expire(key, AUDIT_TTL)
        logger.debug("Audit: %s | %s | %s", incident_id, stage, decision)
        return True
    except Exception as e:
        logger.error("Audit write failed for %s: %s", incident_id, e)
        return False


def get_audit_log(incident_id: str) -> list:
    """Return full audit log for incident."""
    key = f"{AUDIT_PREFIX}{incident_id}"
    try:
        r      = _redis()
        events = r.lrange(key, 0, -1)
        return [json.loads(e) for e in events]
    except Exception as e:
        logger.error("Audit read failed for %s: %s", incident_id, e)
        return []


def get_latest_stage(incident_id: str) -> Optional[str]:
    """Return the most recent stage from audit log."""
    log = get_audit_log(incident_id)
    if not log:
        return None
    return log[-1].get("stage")


def replay_incident_state(incident_id: str) -> Optional[dict]:
    """
    Reconstruct incident state from audit log alone.

    Used for:
    - Redis loss recovery
    - Partial corruption
    - Region failover

    Returns reconstructed incident dict or None if log is empty/corrupt.
    """
    from pipeline import ALLOWED_TRANSITIONS, STAGES

    log = get_audit_log(incident_id)
    if not log:
        logger.warning("No audit log found for %s — cannot replay", incident_id)
        return None

    logger.info("Replaying incident %s from %d audit events", incident_id, len(log))

    # Walk audit log and apply valid transitions
    current_stage = None
    started_at    = log[0].get("timestamp", time.time())
    last_updated  = started_at
    history       = []
    seen_stages   = set()

    for entry in log:
        stage = entry.get("stage")
        if not stage:
            continue

        # Skip duplicates
        if stage in seen_stages:
            logger.debug("Replay: skipping duplicate stage %s", stage)
            continue

        # Validate transition
        expected = ALLOWED_TRANSITIONS.get(current_stage)
        if stage != expected and stage != "RECOVERED":
            logger.warning("Replay: invalid transition %s → %s — skipping",
                           current_stage, stage)
            continue

        current_stage = stage
        last_updated  = entry.get("timestamp", last_updated)
        seen_stages.add(stage)
        history.append({
            "stage": stage,
            "at":    entry.get("timestamp"),
            "meta":  {"replayed": True},
        })

    if not current_stage:
        logger.error("Replay failed: no valid stages found for %s", incident_id)
        return None

    reconstructed = {
        "schema_version": "1.0.0",
        "incident_id":    incident_id,
        "stage":          current_stage,
        "stage_at":       last_updated,
        "started_at":     started_at,
        "last_updated":   last_updated,
        "last_status":    "critical" if current_stage != "RECOVERED" else "healthy",
        "score":          0.0,
        "p95":            0.0,
        "err":            0.0,
        "token":          None,
        "recovery_url":   None,
        "voice_sent":     False,
        "secondary_sent": False,
        "resolved_at":    last_updated if current_stage == "RECOVERED" else None,
        "history":        history,
        "replayed":       True,
    }

    logger.info("Replay complete: %s → stage=%s", incident_id, current_stage)
    return reconstructed
