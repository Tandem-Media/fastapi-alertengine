# orchestrator/memory.py
"""
Redis-backed incident state store.

Rules:
- Single source of truth for all incident state
- Survives orchestrator restarts
- No in-memory dict fallback for active incidents
- TTL: 24 hours per incident key
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("orchestrator.memory")

INCIDENT_TTL  = 86400   # 24 hours
KEY_PREFIX    = "orchestrator:incident:"
ACTIVE_KEY    = "orchestrator:active_incident"


def _redis():
    import redis
    url = os.getenv("REDIS_URL", os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


# ── Write ──────────────────────────────────────────────────────────────────────

def save_incident(incident: dict) -> bool:
    """Persist full incident state to Redis. Returns True on success."""
    incident_id = incident.get("id")
    if not incident_id:
        logger.error("Cannot save incident without id")
        return False
    try:
        r   = _redis()
        key = f"{KEY_PREFIX}{incident_id}"
        r.setex(key, INCIDENT_TTL, json.dumps(incident))
        r.setex(ACTIVE_KEY, INCIDENT_TTL, incident_id)
        logger.debug("Incident saved: %s", incident_id)
        return True
    except Exception as e:
        logger.error("Failed to save incident %s: %s", incident_id, e)
        return False


def resolve_incident(incident_id: str) -> bool:
    """Mark incident as resolved — remove from active key."""
    try:
        r = _redis()
        r.delete(ACTIVE_KEY)
        logger.info("Incident resolved in store: %s", incident_id)
        return True
    except Exception as e:
        logger.error("Failed to resolve incident %s: %s", incident_id, e)
        return False


# ── Read ───────────────────────────────────────────────────────────────────────

def get_incident(incident_id: str) -> Optional[dict]:
    """Load incident state by ID. Returns None if not found."""
    try:
        r    = _redis()
        key  = f"{KEY_PREFIX}{incident_id}"
        data = r.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.error("Failed to load incident %s: %s", incident_id, e)
        return None


def get_active_incident() -> Optional[dict]:
    """Load the currently active incident. Returns None if no active incident."""
    try:
        r           = _redis()
        incident_id = r.get(ACTIVE_KEY)
        if not incident_id:
            return None
        return get_incident(incident_id)
    except Exception as e:
        logger.error("Failed to load active incident: %s", e)
        return None


def get_active_incident_id() -> Optional[str]:
    """Return active incident ID only."""
    try:
        return _redis().get(ACTIVE_KEY)
    except Exception as e:
        logger.error("Failed to get active incident id: %s", e)
        return None


def list_recent_incidents(limit: int = 20) -> list[dict]:
    """Return recent incidents ordered by start time descending."""
    try:
        r    = _redis()
        keys = r.keys(f"{KEY_PREFIX}*")
        incidents = []
        for key in keys:
            data = r.get(key)
            if data:
                try:
                    incidents.append(json.loads(data))
                except Exception:
                    continue
        incidents.sort(key=lambda x: x.get("started_at", 0), reverse=True)
        return incidents[:limit]
    except Exception as e:
        logger.error("Failed to list incidents: %s", e)
        return []


def incident_exists(incident_id: str) -> bool:
    try:
        return bool(_redis().exists(f"{KEY_PREFIX}{incident_id}"))
    except Exception:
        return False


# ── Audit append ───────────────────────────────────────────────────────────────

def append_audit(incident_id: str, entry: dict) -> bool:
    """Append an audit entry to incident history."""
    incident = get_incident(incident_id)
    if not incident:
        return False
    incident.setdefault("history", [])
    incident["history"].append({**entry, "at": time.time()})
    return save_incident(incident)
