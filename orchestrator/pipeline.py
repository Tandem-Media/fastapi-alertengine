# orchestrator/pipeline.py
"""
Pure state machine. No I/O. No HTTP. No side effects.

Incident lifecycle:
    DETECTED → PROPOSED → VALIDATED → AUTHORIZED → EXECUTED → RESOLVED

Rules:
- Each transition is explicit and logged
- State is a dict — caller owns persistence
- Returns structured transition result
- Never raises — returns error state on failure
"""

import time
import logging
from typing import Optional

logger = logging.getLogger("orchestrator.pipeline")

# Valid stages in order
STAGES = [
    "detected",
    "proposed",
    "validated",
    "authorized",
    "executed",
    "resolved",
]

# Minimum seconds before each transition (demo-tunable)
STAGE_GATES = {
    "detected":  0,    # immediate
    "proposed":  5,    # 5s after detected
    "validated": 8,    # 8s after proposed
    "authorized": 0,   # immediate on tap
    "executed":  0,    # immediate on authorized
    "resolved":  0,    # immediate on recovery
}


def new_incident(incident_id: str, score: float, p95: float, err: float) -> dict:
    """Create a fresh incident state dict."""
    now = time.time()
    return {
        "id":              incident_id,
        "stage":           "detected",
        "stage_at":        now,
        "started_at":      now,
        "score":           score,
        "p95":             p95,
        "err":             err,
        "token":           None,
        "recovery_url":    None,
        "voice_sent":      False,
        "secondary_sent":  False,
        "resolved_at":     None,
        "history":         [{"stage": "detected", "at": now}],
    }


def can_transition(incident: dict, target_stage: str) -> tuple[bool, str]:
    """
    Check if transition to target_stage is allowed.
    Returns (allowed: bool, reason: str).
    """
    current = incident.get("stage")

    if current == target_stage:
        return False, f"Already in {target_stage}"

    if target_stage not in STAGES:
        return False, f"Unknown stage: {target_stage}"

    current_idx = STAGES.index(current) if current in STAGES else -1
    target_idx  = STAGES.index(target_stage)

    # Allow forward transitions only (except resolved which can come from anywhere)
    if target_stage != "resolved" and target_idx != current_idx + 1:
        return False, f"Cannot jump from {current} to {target_stage}"

    # Check stage gate
    gate = STAGE_GATES.get(target_stage, 0)
    age  = time.time() - incident.get("stage_at", time.time())
    if age < gate:
        return False, f"Stage gate not met: {age:.1f}s < {gate}s"

    return True, "ok"


def transition(incident: dict, target_stage: str, metadata: Optional[dict] = None) -> dict:
    """
    Apply a stage transition to incident.
    Returns updated incident dict.
    Caller is responsible for persisting the result.
    """
    allowed, reason = can_transition(incident, target_stage)
    if not allowed:
        logger.debug("Transition blocked %s → %s: %s",
                     incident.get("stage"), target_stage, reason)
        return incident

    now = time.time()
    prev = incident.get("stage")

    incident = {**incident}  # shallow copy — don't mutate caller's dict
    incident["stage"]    = target_stage
    incident["stage_at"] = now

    if target_stage == "resolved":
        incident["resolved_at"] = now

    entry = {"stage": target_stage, "at": now}
    if metadata:
        entry["meta"] = metadata
    incident["history"] = incident.get("history", []) + [entry]

    logger.info("Pipeline: %s → %s (%s)", prev, target_stage, incident["id"])
    return incident


def is_terminal(incident: dict) -> bool:
    """Returns True if incident is in a terminal state."""
    return incident.get("stage") in ("resolved",)


def stage_age(incident: dict) -> float:
    """Seconds since entering current stage."""
    return time.time() - incident.get("stage_at", time.time())


def incident_duration(incident: dict) -> float:
    """Total incident duration in seconds."""
    end = incident.get("resolved_at") or time.time()
    return end - incident.get("started_at", end)


def next_required_stage(incident: dict) -> Optional[str]:
    """
    Returns the next stage this incident should transition to,
    based on current stage and gate timings.
    Returns None if not ready or already terminal.
    """
    current = incident.get("stage")
    if not current or current == "resolved":
        return None

    try:
        idx = STAGES.index(current)
    except ValueError:
        return None

    if idx + 1 >= len(STAGES):
        return None

    next_stage = STAGES[idx + 1]

    # Don't auto-advance past validated — requires human authorization
    if next_stage in ("authorized", "executed"):
        return None

    gate = STAGE_GATES.get(next_stage, 0)
    if stage_age(incident) >= gate:
        return next_stage

    return None
