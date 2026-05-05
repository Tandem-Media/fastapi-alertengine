# fastapi_alertengine/actions/recovery.py
"""
Health → Action Suggestion Engine

Translates health scores into structured ActionSuggestion objects.
NOTHING is auto-executed. Suggestions are text-only descriptions.

Pipeline:
    detect → evaluate → suggest

Rule table (from config, with defaults):
    health_score < 25  → suggest recovery action (CRITICAL)
    health_score < 40  → suggest alert + escalate (HIGH)
    health_score < 60  → suggest warning notification (MEDIUM)
    health_score >= 60 → no action suggested (OK)

Each ActionSuggestion includes:
- The specific action recommended (text only)
- The reason derived from current metrics
- Priority level
- auto_permitted is always False — nothing is executed
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ActionSuggestion:
    """
    A suggested recovery action — never auto-executed.

    suggestion_id: unique ID for this suggestion
    action:        "restart" | "scale" | "alert" | "notify" | "investigate"
    priority:      "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    reason:        human-readable explanation derived from metrics
    auto_permitted: always False
    token:         always None (execution tokens have been removed)
    expires_at:    always None
    health_score:  score at time of suggestion
    triggered_by:  which rule fired
    """
    suggestion_id:  str
    action:         str
    priority:       str
    reason:         str
    auto_permitted: bool
    token:          Optional[str]
    expires_at:     Optional[float]
    health_score:   float
    triggered_by:   str
    created_at:     float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "suggestion_id":  self.suggestion_id,
            "action":         self.action,
            "priority":       self.priority,
            "reason":         self.reason,
            "auto_permitted": self.auto_permitted,
            "token":          self.token,
            "expires_at":     self.expires_at,
            "health_score":   round(self.health_score, 1),
            "triggered_by":   self.triggered_by,
            "created_at":     self.created_at,
        }


def suggest_actions(
    health_score: float,
    status:       str,
    service:      str,
    metrics:      dict,
    alerts:       List[dict],
) -> List[ActionSuggestion]:
    """
    Map current health to a list of ActionSuggestions.

    Rules (evaluated in order, all matching rules fire):
    - score < 25  → suggest "restart"  (CRITICAL)
    - score < 40  → suggest "alert"    (HIGH)  
    - score < 60  → suggest "notify"   (MEDIUM)

    Returns empty list when score >= 60 (healthy).
    Never raises.
    """
    suggestions = []

    if health_score >= 60:
        return suggestions

    p95   = metrics.get("overall_p95_ms", 0)
    err   = metrics.get("error_rate", 0)

    if health_score < 25:
        sid = str(uuid.uuid4())
        suggestions.append(ActionSuggestion(
            suggestion_id  = sid,
            action         = "restart",
            priority       = "CRITICAL",
            reason         = (
                f"System health has fallen to {health_score:.0f}/100. "
                f"P95 latency is {p95:.0f}ms with a {err:.1%} error rate. "
                "Service restart is recommended to restore baseline performance."
            ),
            auto_permitted = False,
            token          = None,
            expires_at     = None,
            health_score   = health_score,
            triggered_by   = "health_score < 25",
        ))

    if health_score < 40:
        sid = str(uuid.uuid4())
        suggestions.append(ActionSuggestion(
            suggestion_id  = sid,
            action         = "alert",
            priority       = "HIGH",
            reason         = (
                f"Health score {health_score:.0f}/100 indicates critical degradation. "
                f"On-call escalation recommended. "
                f"Primary signal: P95={p95:.0f}ms, errors={err:.1%}."
            ),
            auto_permitted = False,
            token          = None,
            expires_at     = None,
            health_score   = health_score,
            triggered_by   = "health_score < 40",
        ))

    if health_score < 60:
        sid = str(uuid.uuid4())
        suggestions.append(ActionSuggestion(
            suggestion_id  = sid,
            action         = "notify",
            priority       = "MEDIUM",
            reason         = (
                f"Health score {health_score:.0f}/100 — system is degraded but operational. "
                f"Engineering awareness recommended. "
                f"Current P95: {p95:.0f}ms."
            ),
            auto_permitted = False,
            token          = None,
            expires_at     = None,
            health_score   = health_score,
            triggered_by   = "health_score < 60",
        ))

    return suggestions
