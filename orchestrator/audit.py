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


import redis as _redis_module

_audit_redis_client = None

def _redis():
    global _audit_redis_client
    if _audit_redis_client is None:
        url = os.getenv("REDIS_URL",
              os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
        _audit_redis_client = _redis_module.Redis.from_url(url, decode_responses=True)
    return _audit_redis_client


def append_event(
    incident_id: str,
    stage: str,
    decision: str,
    reason: str,
    confidence: float,
    actor: str = "pipeline",
    action_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    tenant_id: Optional[str] = None,
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
        "tenant_id":   tenant_id,
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


def get_audit_log_for_tenant(tenant_id: str) -> list:
    """
    Return all audit events across all incidents for a given tenant.

    Audit events are stored per-incident (orchestrator:audit:{incident_id}),
    with no separate tenant index. This scans current audit keys and filters
    by the tenant_id recorded on each event. Audit keys expire after
    AUDIT_TTL (7 days), so the keyspace this scans stays naturally bounded —
    fine at current incident volume.

    If incident volume grows enough that this scan becomes slow, replace
    with a maintained per-tenant index (a Redis SET of incident_ids per
    tenant, updated inside append_event) rather than scanning at read time.
    """
    try:
        r = _redis()
        events = []
        for key in r.scan_iter(match=f"{AUDIT_PREFIX}*"):
            raw_events = r.lrange(key, 0, -1)
            for raw in raw_events:
                try:
                    entry = json.loads(raw)
                except Exception:
                    continue
                if entry.get("tenant_id") == tenant_id:
                    events.append(entry)
        events.sort(key=lambda e: e.get("timestamp", 0))
        return events
    except Exception as e:
        logger.error("get_audit_log_for_tenant failed for %s: %s", tenant_id, e)
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

    # Forensics systems never discard events.
    # Replay strictly from the log — if the log is corrupt, mark it.
    # seen_stages removed: duplicate stages are evidence of bugs or retries,
    # not noise to be filtered.
    replay_warnings = []

    for entry in log:
        stage = entry.get("stage")
        if not stage:
            continue

        # Validate transition — warn but do NOT skip
        expected = ALLOWED_TRANSITIONS.get(current_stage)
        if stage != expected and stage not in ("RECOVERED", "FAILED", "EXPIRED", "WEBHOOK_FAILED"):
            warn = f"Unexpected transition {current_stage} → {stage} (expected {expected})"
            logger.warning("Replay: %s", warn)
            replay_warnings.append(warn)
            # Continue anyway — record what actually happened

        current_stage = stage
        last_updated  = entry.get("timestamp", last_updated)
        history.append({
            "stage":    stage,
            "at":       entry.get("timestamp"),
            "actor":    entry.get("actor", "unknown"),
            "meta":     {"replayed": True},
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
        "replay_warnings": replay_warnings,
        "replay_integrity": "clean" if not replay_warnings else "warnings",
    }

    logger.info("Replay complete: %s → stage=%s", incident_id, current_stage)
    return reconstructed
