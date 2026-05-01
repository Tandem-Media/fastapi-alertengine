# orchestrator/policy.py
"""
Policy enforcement layer.

Rules:
- Hard constraints only — no soft heuristics
- Called by loop.py before any action
- Returns bool — caller decides what to do
- Never raises
"""

import logging
import os

logger = logging.getLogger("orchestrator.policy")

# Thresholds — override via env for different environments
MIN_SCORE_TO_ALERT      = float(os.getenv("POLICY_MIN_SCORE_TO_ALERT",   "70"))
MIN_ERR_RATE_TO_ALERT   = float(os.getenv("POLICY_MIN_ERR_RATE_TO_ALERT", "0.1"))
VOICE_AFTER_S           = float(os.getenv("VOICE_AFTER_S",                "180"))
SECONDARY_AFTER_S       = float(os.getenv("SECONDARY_AFTER_S",            "300"))
MIN_CONFIDENCE_TO_ACT   = float(os.getenv("POLICY_MIN_CONFIDENCE",        "0.6"))


def should_alert(score: float, err_rate: float) -> bool:
    """
    Returns True if system state warrants opening an incident and alerting.
    Fintech rule: only alert on strong signal.
    """
    if score > MIN_SCORE_TO_ALERT:
        logger.debug("Policy: score %.0f above threshold — no alert", score)
        return False
    if err_rate < MIN_ERR_RATE_TO_ALERT:
        logger.debug("Policy: err_rate %.1f%% below threshold — no alert", err_rate * 100)
        return False
    return True


def should_escalate_voice(duration_s: float, score: float) -> bool:
    """Returns True if voice escalation is warranted."""
    return duration_s >= VOICE_AFTER_S and score < MIN_SCORE_TO_ALERT


def should_escalate_secondary(duration_s: float, score: float) -> bool:
    """Returns True if secondary engineer notification is warranted."""
    return duration_s >= SECONDARY_AFTER_S and score < MIN_SCORE_TO_ALERT


def should_act_on_decision(decision: dict) -> bool:
    """
    Returns True if Claude's decision meets minimum confidence threshold.
    Prevents low-confidence AI decisions from triggering production actions.
    """
    confidence = decision.get("confidence", 0)
    if confidence < MIN_CONFIDENCE_TO_ACT:
        logger.warning("Policy: Claude confidence %.0f%% below threshold — suppressing action",
                       confidence * 100)
        return False
    return True


def is_suppressed_action(action: str) -> bool:
    """
    Returns True if this action type is globally suppressed.
    Use env var SUPPRESSED_ACTIONS=recover,escalate to disable actions.
    """
    suppressed = os.getenv("SUPPRESSED_ACTIONS", "").split(",")
    suppressed = [s.strip() for s in suppressed if s.strip()]
    if action in suppressed:
        logger.warning("Policy: action '%s' is suppressed", action)
        return True
    return False


def validate_decision(decision: dict) -> tuple[bool, str]:
    """
    Full policy check on a Claude decision.
    Returns (allowed: bool, reason: str).
    """
    action = decision.get("action", "")

    if is_suppressed_action(action):
        return False, f"Action '{action}' is suppressed by policy"

    if not should_act_on_decision(decision):
        return False, f"Confidence too low: {decision.get('confidence', 0):.0%}"

    allowed_actions = {"escalate", "validate", "suppress", "recover"}
    if action not in allowed_actions:
        return False, f"Unknown action: {action}"

    return True, "ok"
