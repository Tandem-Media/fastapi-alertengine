# orchestrator/audit.py
"""
Orchestrator Audit Logger

Every decision cycle is logged as a structured JSON entry.
Written to stdout (Railway picks it up) and optionally to a file.

Schema:
{
    "ts":              float,
    "cycle":           int,
    "health_score":    float,
    "health_status":   str,
    "action_proposed": str,
    "action_risk":     str,
    "policy_permitted": bool,
    "policy_reason":   str,
    "confidence":      str,
    "stop_condition":  str,
    "confirm_url":     str | null,
    "diagnosis":       str,
    "synthesis":       str
}
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger("orchestrator.audit")


def log_cycle(
    cycle:          int,
    health:         dict,
    decision,       # OrchestratorDecision | None
    policy,         # PolicyDecision | None
    action_result:  Optional[dict],
) -> dict:
    """
    Log one complete orchestrator cycle. Returns the audit entry dict.
    """
    hs    = health.get("health_score", {})
    score = hs.get("score", 0) if isinstance(hs, dict) else 0
    status = hs.get("status", "unknown") if isinstance(hs, dict) else "unknown"

    entry = {
        "ts":              time.time(),
        "cycle":           cycle,
        "health_score":    score,
        "health_status":   status,
        "action_proposed": decision.action_type   if decision else None,
        "action_risk":     policy.risk            if policy   else None,
        "policy_permitted": policy.permitted      if policy   else None,
        "policy_reason":   policy.reason          if policy   else None,
        "confidence":      decision.confidence    if decision else None,
        "stop_condition":  decision.stop_condition if decision else None,
        "confirm_url":     action_result.get("confirm_url") if action_result else None,
        "diagnosis":       decision.root_cause    if decision else None,
        "synthesis":       decision.synthesis     if decision else None,
        "requires_human_approval": policy.requires_human_approval if policy else None,
    }

    # Emit as structured JSON line (Railway log aggregation friendly)
    logger.info("CYCLE_AUDIT %s", json.dumps(entry))
    return entry
