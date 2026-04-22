# orchestrator/claude_engine.py
"""
Claude Reasoning Engine

Calls the Anthropic API with the orchestrator system prompt.
Enforces strict JSON output parsing with retry on invalid schema.
Returns a structured OrchestratorDecision or None on failure.

Output schema (what Claude must return):
{
    "ANALYSIS":   { "issues": [...], "metric_interpretation": "..." },
    "DIAGNOSIS":  { "root_cause": "...", "hypothesis": "..." },
    "DELEGATION": { "agent": null | "codex" | "gemini", "purpose": "..." },
    "SYNTHESIS":  "...",
    "ACTION":     { "type": "...", "detail": "...", "reversible": true },
    "CONFIDENCE": "High" | "Medium" | "Low",
    "STOP_CONDITION": "Continue" | "Stop" | "Escalate"
}
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger("orchestrator.claude")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL             = "claude-sonnet-4-5"
MAX_TOKENS        = 1_000
MAX_RETRIES       = 3

SYSTEM_PROMPT = """You are an AI Systems Orchestrator operating inside a production-grade observability and recovery loop.

Your role is to:
1. Interpret real system health data from AlertEngine
2. Form a grounded diagnosis based ONLY on provided metrics
3. Propose safe, minimal, reversible recovery actions

HARD RULES:
- NEVER hallucinate system state
- NEVER invent metrics or conditions not present in the input
- NEVER suggest destructive or irreversible actions
- If uncertain → set CONFIDENCE to Low and STOP_CONDITION to Escalate
- Nothing auto-executes — you are advisory only

DECISION LOGIC:
- health_score < 50  → CRITICAL → prioritize stabilization
- health_score 50-74 → DEGRADED → investigate and optimize
- health_score >= 75 → STABLE   → stop unless trend is worsening

VALID ACTION TYPES:
- "restart"    → suggest service restart (HIGH RISK — requires human approval)
- "alert"      → escalate to on-call (MEDIUM RISK)
- "notify"     → send awareness notification (LOW RISK)
- "investigate" → request more data (NO RISK)
- "none"       → system stable, no action needed

You MUST respond in valid JSON matching this exact schema:
{
    "ANALYSIS": {
        "issues": ["<issue 1>", "<issue 2>"],
        "metric_interpretation": "<one sentence>"
    },
    "DIAGNOSIS": {
        "root_cause": "<hypothesis>",
        "hypothesis": "<supporting reasoning>"
    },
    "DELEGATION": {
        "agent": null,
        "purpose": null
    },
    "SYNTHESIS": "<final combined reasoning in one paragraph>",
    "ACTION": {
        "type": "<restart|alert|notify|investigate|none>",
        "detail": "<specific instruction>",
        "reversible": true
    },
    "CONFIDENCE": "<High|Medium|Low>",
    "STOP_CONDITION": "<Continue|Stop|Escalate>"
}

Respond with JSON only. No preamble. No explanation outside the JSON."""


@dataclass
class OrchestratorDecision:
    issues:               list
    metric_interpretation: str
    root_cause:           str
    hypothesis:           str
    synthesis:            str
    action_type:          str   # restart | alert | notify | investigate | none
    action_detail:        str
    reversible:           bool
    confidence:           str   # High | Medium | Low
    stop_condition:       str   # Continue | Stop | Escalate
    raw:                  dict  # full parsed JSON for audit log

    @classmethod
    def from_json(cls, data: dict) -> "OrchestratorDecision":
        return cls(
            issues                = data["ANALYSIS"]["issues"],
            metric_interpretation = data["ANALYSIS"]["metric_interpretation"],
            root_cause            = data["DIAGNOSIS"]["root_cause"],
            hypothesis            = data["DIAGNOSIS"]["hypothesis"],
            synthesis             = data["SYNTHESIS"],
            action_type           = data["ACTION"]["type"].lower(),
            action_detail         = data["ACTION"]["detail"],
            reversible            = data["ACTION"].get("reversible", True),
            confidence            = data["CONFIDENCE"],
            stop_condition        = data["STOP_CONDITION"],
            raw                   = data,
        )


def _build_health_message(health: dict, timeline: Optional[list]) -> str:
    """Format health + timeline into a clean message for Claude."""
    hs      = health.get("health_score", {})
    metrics = health.get("metrics", {})
    alerts  = health.get("alerts", [])
    roc     = health.get("rate_of_change", [])

    lines = [
        "## CURRENT SYSTEM HEALTH",
        f"health_score:  {hs.get('score', 'N/A')}",
        f"health_status: {hs.get('status', 'N/A')}",
        f"trend:         {hs.get('trend', 'N/A')}",
        "",
        "## METRICS",
        f"p95_latency_ms: {metrics.get('overall_p95_ms', 0)}",
        f"error_rate:     {metrics.get('error_rate', 0)}",
        f"anomaly_score:  {metrics.get('anomaly_score', 0)}",
        f"sample_size:    {metrics.get('sample_size', 0)}",
        "",
    ]

    if alerts:
        lines.append("## ACTIVE ALERTS")
        for a in alerts:
            lines.append(
                f"- [{a.get('severity','?').upper()}] {a.get('type','?')}: "
                f"{a.get('message','')}"
            )
            if a.get("reason_for_trigger"):
                lines.append(f"  reason: {a['reason_for_trigger']}")
            if a.get("triggered_by"):
                lines.append(f"  triggered_by: {a['triggered_by']}")
        lines.append("")

    if roc:
        lines.append("## RATE-OF-CHANGE EVENTS")
        for r in roc:
            lines.append(
                f"- {r.get('metric')}: {r.get('previous_value')} → "
                f"{r.get('current_value')} (+{r.get('delta_pct', 0):.0f}%)"
            )
        lines.append("")

    if timeline:
        lines.append("## RECENT INCIDENT TIMELINE (last 5 events)")
        for ev in timeline[-5:]:
            lines.append(
                f"- [{ev.get('severity','?')}] {ev.get('event_type','?')}: "
                f"{ev.get('message','')}"
            )
        lines.append("")

    lines.append("Analyze this health snapshot and produce your structured decision.")
    return "\n".join(lines)


async def reason(
    health:   dict,
    timeline: Optional[list] = None,
) -> Optional[OrchestratorDecision]:
    """
    Call Claude with the health snapshot and return a structured decision.
    Retries up to MAX_RETRIES times on JSON parse failure.
    Returns None if all retries fail.
    """
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
        return None

    user_message = _build_health_message(health, timeline)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key":         ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type":      "application/json",
                    },
                    json={
                        "model":      MODEL,
                        "max_tokens": MAX_TOKENS,
                        "system":     SYSTEM_PROMPT,
                        "messages":   [
                            {"role": "user", "content": user_message}
                        ],
                    },
                )
                response.raise_for_status()
                data    = response.json()
                content = data["content"][0]["text"].strip()

                # Strip markdown fences if present
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                    content = content.strip()

                parsed   = json.loads(content)
                decision = OrchestratorDecision.from_json(parsed)
                logger.info(
                    "Claude decision: action=%s confidence=%s stop=%s",
                    decision.action_type,
                    decision.confidence,
                    decision.stop_condition,
                )
                return decision

        except json.JSONDecodeError as exc:
            logger.warning("Attempt %d: Claude returned invalid JSON: %s", attempt, exc)
        except KeyError as exc:
            logger.warning("Attempt %d: Claude JSON missing key: %s", attempt, exc)
        except Exception as exc:
            logger.warning("Attempt %d: Claude API error: %s", attempt, exc)

    logger.error("All %d Claude attempts failed — returning None", MAX_RETRIES)
    return None
