from dataclasses import dataclass
from typing import List, Dict, Any


@dataclass
class HardenedDecision:
    status: str
    risk_level: str
    confidence: str
    reasoning: str
    allowed_actions: List[Dict[str, Any]]
    policy_flags: List[str]
    recommended_next_step: str


class PolicyHardener:

    def evaluate(self, alertengine_payload: dict, orchestrator_decision: dict) -> HardenedDecision:
        score = alertengine_payload.get("health_score", 100)
        trend = alertengine_payload.get("trend_direction", "stable")

        flags = []
        allowed_actions = []

        # -------------------------
        # RULE 1: Unknown actions
        # -------------------------
        for action in orchestrator_decision.get("actions", []):
            if action.get("type") not in ["restart", "scale", "alert", "noop"]:
                flags.append("unknown_action_detected")
                action["approved"] = False

        # -------------------------
        # RULE 2: Risk gating
        # -------------------------
        if score < 40:
            status = "stop"
            flags.append("critical_system_state")
        elif score < 60:
            status = "escalate"
        elif trend == "degrading":
            status = "degrade_mode"
        else:
            status = "continue"

        # -------------------------
        # RULE 3: Confidence logic
        # -------------------------
        confidence = orchestrator_decision.get("confidence", "low")

        if score < 50 and confidence != "high":
            flags.append("confidence_too_low")
            confidence = "low"

        # -------------------------
        # RULE 4: Conservative default
        # -------------------------
        if "conflicting_signals" in orchestrator_decision.get("flags", []):
            status = "escalate"
            confidence = "low"

        # -------------------------
        # OUTPUT ACTION FILTERING
        # -------------------------
        for action in orchestrator_decision.get("actions", []):
            if action.get("approved", False):
                allowed_actions.append(action)

        return HardenedDecision(
            status=status,
            risk_level=self._risk(score),
            confidence=confidence,
            reasoning=orchestrator_decision.get("reasoning", ""),
            allowed_actions=allowed_actions,
            policy_flags=flags,
            recommended_next_step=self._next_step(status)
        )

    def _risk(self, score: int) -> str:
        if score < 40:
            return "critical"
        if score < 60:
            return "high"
        if score < 80:
            return "medium"
        return "low"

    def _next_step(self, status: str) -> str:
        return {
            "continue": "monitor",
            "degrade_mode": "reduce load + observe",
            "escalate": "request human approval",
            "stop": "freeze actions + alert operator"
        }.get(status, "monitor")