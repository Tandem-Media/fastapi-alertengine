# fastapi_alertengine/actions/recovery.py
"""
v1.6 — Health → Action Mapping Engine

Translates health scores into structured ActionSuggestion objects.
NOTHING is auto-executed. The engine suggests; humans authorize.

Pipeline:
    detect → evaluate → suggest → authorize → log

Rule table (from config, with defaults):
    health_score < 25  → suggest recovery action (CRITICAL)
    health_score < 40  → suggest alert + escalate (HIGH)
    health_score < 60  → suggest warning notification (MEDIUM)
    health_score >= 60 → no action suggested (OK)

Each ActionSuggestion includes:
- A signed JWT action token (if ACTION_SECRET_KEY is set)
- The specific action recommended
- The reason derived from current metrics
- Priority level
- Whether auto-execution is permitted (always False in v1.6)
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

    suggestion_id: unique ID for this suggestion (used in token linking)
    action:        "restart" | "scale" | "alert" | "notify" | "investigate"
    priority:      "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    reason:        human-readable explanation derived from metrics
    auto_permitted: always False in v1.6
    token:         signed JWT — present when ACTION_SECRET_KEY is configured
    expires_at:    when the token expires (unix ts), None if no token
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
    health_score:    float,
    status:          str,
    service:         str,
    metrics:         dict,
    alerts:          List[dict],
    user_id:         str         = "system",
    client_ip:       Optional[str] = None,
    incident_id:     Optional[str] = None,
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

    # Try to generate a signed token — fails silently if key not configured
    def _make_token(action: str, sid: str) -> tuple:
        try:
            from fastapi_alertengine.actions.tokens import generate_action_token
            import time as _t
            token = generate_action_token(
                action=action, service=service, user_id=user_id,
                client_ip=client_ip, incident_id=incident_id,
                health_score=health_score, suggestion_id=sid,
            )
            return token, _t.time() + 90
        except Exception:
            return None, None

    if health_score < 25:
        sid = str(uuid.uuid4())
        tok, exp = _make_token("restart", sid)
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
            token          = tok,
            expires_at     = exp,
            health_score   = health_score,
            triggered_by   = "health_score < 25",
        ))

    if health_score < 40:
        sid = str(uuid.uuid4())
        tok, exp = _make_token("alert", sid)
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
            token          = tok,
            expires_at     = exp,
            health_score   = health_score,
            triggered_by   = "health_score < 40",
        ))

    if health_score < 60:
        sid = str(uuid.uuid4())
        tok, exp = _make_token("notify", sid)
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
            token          = tok,
            expires_at     = exp,
            health_score   = health_score,
            triggered_by   = "health_score < 60",
        ))

    return suggestions
