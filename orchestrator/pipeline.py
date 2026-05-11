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

import os
import time
import logging
from typing import Optional

logger = logging.getLogger("orchestrator.pipeline")

# Valid stages in order — uppercase throughout
STAGES = [
    "DETECTED",
    "PROPOSED",
    "VALIDATED",
    "AUTHORIZED",
    "EXECUTED",
    "RESOLVED",
]

# Policy-driven stage gate timings — default 0 (immediate) for production
STAGE_GATES = {
    "DETECTED":   int(os.getenv("GATE_DETECTED_S",   "0")),
    "PROPOSED":   int(os.getenv("GATE_PROPOSED_S",   "0")),
    "VALIDATED":  int(os.getenv("GATE_VALIDATED_S",  "0")),
    "AUTHORIZED": int(os.getenv("GATE_AUTHORIZED_S", "0")),
    "EXECUTED":   int(os.getenv("GATE_EXECUTED_S",   "0")),
    "RESOLVED":   int(os.getenv("GATE_RESOLVED_S",   "0")),
}

# Allowed transitions for replay and audit validation
ALLOWED_TRANSITIONS = {
    None:          "DETECTED",
    "DETECTED":    "PROPOSED",
    "PROPOSED":    "VALIDATED",
    "VALIDATED":   "AUTHORIZED",
    "AUTHORIZED":  "EXECUTED",
    "EXECUTED":    "RESOLVED",
    "RECOVERED":   None,
    "RESOLVED":    None,
}


def new_incident(incident_id: str, score: float, p95: float,
                 err: float) -> dict:
    """Create a fresh incident state dict."""
    now = time.time()
    return {
        "incident_id":    incident_id,
        "id":             incident_id,   # backward compat alias
        "stage":          "DETECTED",
        "stage_at":       now,
        "started_at":     now,
        "score":          score,
        "p95":            p95,
        "err":            err,
        "token":          None,
        "recovery_url":   None,
        "voice_sent":     False,
        "secondary_sent": False,
        "resolved_at":    None,
        "history":        [{"stage": "DETECTED", "at": now}],
    }


# Alias used by loop.py
open_incident = new_incident


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

    if target_stage != "RESOLVED" and target_idx != current_idx + 1:
        return False, f"Cannot jump from {current} to {target_stage}"

    gate = STAGE_GATES.get(target_stage, 0)
    age  = time.time() - incident.get("stage_at", time.time())
    if age < gate:
        return False, f"Stage gate not met: {age:.1f}s < {gate}s"

    return True, "ok"


def transition(incident: dict, target_stage: str,
               metadata: Optional[dict] = None) -> dict:
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

    now  = time.time()
    prev = incident.get("stage")

    incident = {**incident}
    incident["stage"]    = target_stage
    incident["stage_at"] = now

    if target_stage == "RESOLVED":
        incident["resolved_at"] = now

    entry = {"stage": target_stage, "at": now}
    if metadata:
        entry["meta"] = metadata
    incident["history"] = incident.get("history", []) + [entry]

    logger.info("Pipeline: %s → %s (%s)",
                prev, target_stage,
                incident.get("incident_id", incident.get("id")))
    return incident


# Alias used by loop.py
apply_transition = transition


def decide(incident: dict, health: dict, claude: dict) -> dict:
    """
    Produce a pipeline decision from current incident state,
    health data, and Claude's recommendation.
    Returns a decision dict with next_stage, actions, reason,
    confidence.
    Never raises.
    """
    try:
        stage      = incident.get("stage", "DETECTED")
        action     = claude.get("action", "suppress")
        confidence = float(claude.get("confidence", 0.0))
        reason     = claude.get("reason", "")
        score      = health.get("health_score", {}).get("score", 100)
        err        = health.get("metrics", {}).get("error_rate", 0)

        # Recovery check
        if score > 70 and err < 0.05:
            return {
                "next_stage":  "RECOVERED",
                "actions":     [{"type": "SEND_NOTIFICATION",
                                 "payload": {"type": "RECOVERY"}}],
                "reason":      reason or "System metrics recovered",
                "confidence":  confidence,
            }

        next_stage = ALLOWED_TRANSITIONS.get(stage)
        if not next_stage:
            return _noop(reason, confidence)

        actions = []

        if next_stage == "PROPOSED":
            actions.append({"type": "SEND_NOTIFICATION",
                            "payload": {"type": "CRITICAL"}})

        if next_stage == "VALIDATED":
            actions.append({"type": "GENERATE_TOKEN"})
            actions.append({"type": "SEND_NOTIFICATION",
                            "payload": {"type": "VALIDATION"}})

        if next_stage == "AUTHORIZED":
            actions.append({"type": "ESCALATE"})

        return {
            "next_stage":  next_stage,
            "actions":     actions,
            "reason":      reason,
            "confidence":  confidence,
        }
    except Exception as e:
        logger.error("decide() failed: %s", e)
        return _noop("decide() error", 0.0)


def decide_new_incident(
    incident_id: str,
    score: float,
    p95: float,
    err: float,
    confidence: float,
) -> dict:
    """
    Produce the initial decision for a brand-new incident.
    Returns a decision dict with actions and metadata.
    Never raises.
    """
    try:
        actions = [
            {"type": "SEND_NOTIFICATION", "payload": {"type": "CRITICAL"}},
        ]
        return {
            "next_stage":  "DETECTED",
            "actions":     actions,
            "reason":      f"New incident: score={score:.0f} err={err:.1%}",
            "confidence":  confidence,
        }
    except Exception as e:
        logger.error("decide_new_incident() failed: %s", e)
        return _noop("decide_new_incident() error", 0.0)


def validate_decision_schema(decision: dict) -> tuple[bool, str]:
    """
    Validate that a decision dict has the required fields.
    Returns (valid: bool, reason: str).
    """
    required = {"next_stage", "actions", "reason", "confidence"}
    missing  = required - set(decision.keys())
    if missing:
        return False, f"Missing fields: {missing}"
    if not isinstance(decision["actions"], list):
        return False, "actions must be a list"
    if not isinstance(decision["confidence"], (int, float)):
        return False, "confidence must be numeric"
    return True, "ok"


def _noop(reason: str, confidence: float) -> dict:
    return {
        "next_stage":  None,
        "actions":     [],
        "reason":      reason,
        "confidence":  confidence,
    }


def is_terminal(incident: dict) -> bool:
    return incident.get("stage") in ("RESOLVED", "RECOVERED")


def stage_age(incident: dict) -> float:
    return time.time() - incident.get("stage_at", time.time())


def incident_duration(incident: dict) -> float:
    end = incident.get("resolved_at") or time.time()
    return end - incident.get("started_at", end)


def next_required_stage(incident: dict) -> Optional[str]:
    current = incident.get("stage")
    if not current or current in ("RESOLVED", "RECOVERED"):
        return None
    try:
        idx = STAGES.index(current)
    except ValueError:
        return None
    if idx + 1 >= len(STAGES):
        return None
    next_stage = STAGES[idx + 1]
    if next_stage in ("AUTHORIZED", "EXECUTED"):
        return None
    gate = STAGE_GATES.get(next_stage, 0)
    if stage_age(incident) >= gate:
        return next_stage
    return None
