# orchestrator/incident_policy.py
"""
Incident policy definitions — single source of truth for all thresholds.

These values were previously hardcoded in:
  - pipeline.py: score > 70 and err < 0.05
  - claude_engine.py: DECISION_PROMPT thresholds
  - diagnostic_council.py: divergence threshold

Moving them here means:
  - One place to change behaviour
  - Per-tenant policy overrides possible (future)
  - Policy version tracked in audit log

Policy is immutable at runtime. Changes require a version bump.
"""

import os

POLICY_VERSION = "1.0.0"

POLICY = {
    # Recovery thresholds — system auto-detects health restoration
    "recover_score":        float(os.getenv("POLICY_RECOVER_SCORE",      "70")),
    "recover_error_rate":   float(os.getenv("POLICY_RECOVER_ERROR_RATE", "0.05")),

    # Validation thresholds — when to send recovery link
    "validate_score":       float(os.getenv("POLICY_VALIDATE_SCORE",      "40")),
    "validate_error_rate":  float(os.getenv("POLICY_VALIDATE_ERROR_RATE", "0.20")),

    # Confidence gate — below this, AI diagnosis is suppressed
    "suppress_confidence":  float(os.getenv("POLICY_SUPPRESS_CONFIDENCE", "0.60")),

    # Incident open threshold — below this score, open an incident
    "alert_score":          float(os.getenv("POLICY_MIN_SCORE_TO_ALERT",  "70")),

    # Council divergence — below this similarity, fire dissent alert
    "council_divergence":   float(os.getenv("COUNCIL_DIVERGENCE_THRESHOLD", "0.6")),
}


def should_recover(score: float, error_rate: float) -> bool:
    """Return True if metrics indicate the system has recovered."""
    return score > POLICY["recover_score"] and error_rate < POLICY["recover_error_rate"]


def should_validate(score: float, error_rate: float) -> bool:
    """Return True if metrics warrant sending a recovery link."""
    return score < POLICY["validate_score"] and error_rate > POLICY["validate_error_rate"]


def should_suppress(confidence: float) -> bool:
    """Return True if AI confidence is too low to act on."""
    return confidence < POLICY["suppress_confidence"]


def should_alert(score: float, error_rate: float) -> bool:
    """Return True if metrics warrant opening an incident."""
    return score < POLICY["alert_score"]


def policy_summary() -> str:
    """One-line summary for logging and audit."""
    return (
        f"policy_v{POLICY_VERSION} "
        f"recover>{POLICY['recover_score']} "
        f"validate<{POLICY['validate_score']} "
        f"suppress_conf<{POLICY['suppress_confidence']}"
    )
