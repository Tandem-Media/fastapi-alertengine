# orchestrator/claude_engine.py
"""
Claude decision engine — hardened version.

Improvements over v1:
1. Native tool use — eliminates JSON parse failures entirely
2. Few-shot examples in system prompt — reduces Haiku format violations
3. Baseline memory integration — improves root cause accuracy
4. Multi-turn diagnosis memory — prevents flip-flopping across polling cycles
"""

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("orchestrator.claude")

# Module-level imports — fail loud at startup rather than silently during incidents
try:
    from baseline import baseline_context as _baseline_context
    _has_baseline = True
except Exception as e:
    logger.warning("Baseline module unavailable: %s", e)
    _has_baseline = False
    _baseline_context = lambda *args, **kwargs: ""

try:
    from diagnosis_memory import build_history_messages as _build_history
    from diagnosis_memory import record_turn as _record_turn
    _has_memory = True
except Exception as e:
    logger.warning("Diagnosis memory module unavailable: %s", e)
    _has_memory = False
    _build_history = lambda *args, **kwargs: []
    _record_turn = lambda *args, **kwargs: None

MODEL      = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 800   # slightly higher to accommodate tool use overhead
RETRIES    = 2     # reduced from 3 — tool use eliminates most parse failures
API_URL    = "https://api.anthropic.com/v1/messages"

# ── Tool schema — guarantees schema-valid output, no JSON parsing needed ───────

TOOLS = [{
    "name": "incident_decision",
    "description": "Make a structured incident response decision and generate a WhatsApp message",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["escalate", "validate", "suppress", "recover"],
                "description": "The recommended action"
            },
            "reason": {
                "type": "string",
                "description": "One sentence explanation of the decision"
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence score between 0.0 and 1.0"
            },
            "whatsapp_message": {
                "type": "string",
                "description": "The full WhatsApp message text following formatting rules. Empty string if action is suppress or recover."
            }
        },
        "required": ["action", "reason", "confidence", "whatsapp_message"]
    }
}]

# ── Decision system prompt with few-shot examples ─────────────────────────────

DECISION_PROMPT = """You are an incident response assistant for backend APIs.

Your role is to translate raw system metrics into a structured decision AND a clear, calm WhatsApp alert for an engineer.

Internally reason through the situation:
1. Determine severity: degrading, critical, or stable — use latency, error rate, trend, and baseline deviation.
2. Identify most likely cause: high latency = database/I/O/dependency; high errors = failing service/bad deploy; degrading trend = load or resource exhaustion.
3. Select a safe, practical fix: prefer low-risk actions (restart worker, scale service, clear queue). Never suggest destructive or irreversible actions.
4. Assign confidence: High (80-95%) if metrics strongly indicate a known pattern. Medium (60-79%) if somewhat clear. Low (<60%) if uncertain.

Use the incident_decision tool to return your response.

Rules for whatsapp_message:
- Under 12 lines total
- Simple language — no jargon like 'orchestrator', 'pipeline', 'agent'
- No panic language (no 'CRITICAL!!!', 'FAILURE!!!')
- Calm, direct, helpful tone
- Format EXACTLY:

⚠️ Action Recommended
Service: <name>
Issue: <short description>
Likely cause: <plain explanation>
Suggested fix: <one-line action>

Confidence: <percentage>%
👉 Approve fix: <recovery_url>

Nothing will run without your approval.

Rules for action field:
- escalate: open or escalate incident
- validate: send recovery link to operator
- suppress: do not alert (signal is noise)
- recover: system has recovered, close incident

Rules:
- confidence < 0.6 → use suppress
- Only recommend recover when score > 70 and error_rate < 0.05
- Only recommend validate when score < 40 and error_rate > 0.2
- Be conservative — false positives in fintech are costly
- whatsapp_message must be empty string "" when action is suppress or recover

Examples:

Input:
Service: Payment API
Health status: critical
Score: 15/100
Trend: degrading
P95 latency: 5230ms
Error rate: 45.0%
Baseline (last 24 samples):
  Normal P95: 120ms
  Normal error rate: 0.5%
  Normal RPM: 340
Current deviation: P95 is 43.6x baseline, errors 90.0x baseline

Expected tool call:
{
  "action": "validate",
  "reason": "Latency 43x baseline strongly suggests database connection pool exhaustion or deadlock",
  "confidence": 0.85,
  "whatsapp_message": "⚠️ Action Recommended\nService: Payment API\nIssue: Extreme latency spike — 5.2s vs normal 0.1s\nLikely cause: Database connection pool exhausted or query deadlock\nSuggested fix: Restart worker pool to clear hung connections\n\nConfidence: 85%\n👉 Approve fix: [URL]\n\nNothing will run without your approval."
}

Input:
Service: Payment API
Health status: healthy
Score: 94/100
Trend: stable
P95 latency: 115ms
Error rate: 0.3%
Baseline (last 24 samples):
  Normal P95: 120ms
  Normal error rate: 0.5%
  Normal RPM: 340
Current deviation: P95 is 1.0x baseline, errors 0.6x baseline

Expected tool call:
{
  "action": "recover",
  "reason": "Metrics returned to normal baseline after incident",
  "confidence": 0.92,
  "whatsapp_message": ""
}
"""


def _build_prompt(
    health: dict,
    incident: Optional[dict],
    tenant_id: str = "",
    recovery_url: str = "",
) -> str:
    hs  = health.get("health_score", {})
    m   = health.get("metrics", {})
    inc = incident or {}

    service_name = inc.get("service_name", os.getenv("SERVICE_NAME", "Payment API"))

    lines = [
        f"Service: {service_name}",
        f"Health status: {hs.get('status', 'unknown')}",
        f"Score: {hs.get('score', 100):.0f}/100",
        f"Trend: {hs.get('trend', 'stable')}",
        f"P95 latency: {m.get('overall_p95_ms', 0):.0f}ms",
        f"Error rate: {m.get('error_rate', 0)*100:.1f}%",
        f"Recovery URL: {recovery_url or 'pending'}",
    ]

    # Inject baseline context if available
    if tenant_id and _has_baseline:
        try:
            ctx = _baseline_context(
                tenant_id,
                m.get("overall_p95_ms", 0),
                m.get("error_rate", 0),
            )
            if ctx:
                lines.append(ctx)
        except Exception as e:
            logger.debug("Baseline context failed: %s", e)

    if inc:
        lines += [
            f"Active incident: {inc.get('incident_id', inc.get('id', 'none'))}",
            f"Stage: {inc.get('stage', 'none')}",
        ]

    return "\n".join(lines)


async def get_decision(
    health: dict,
    incident: Optional[dict] = None,
    tenant_id: str = "",
    recovery_url: str = "",
) -> dict:
    """
    Ask Claude to make a decision and generate a WhatsApp message.
    Uses native tool use for schema-guaranteed output.
    Supports multi-turn diagnosis memory for active incidents.
    Never raises.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — returning safe default")
        return _safe_default()

    prompt = _build_prompt(health, incident, tenant_id, recovery_url)

    # Build message list — prepend history for active incidents
    messages = [{"role": "user", "content": prompt}]
    incident_id = None

    if incident:
        incident_id = incident.get("incident_id") or incident.get("id")
        if incident_id and _has_memory:
            try:
                history = _build_history(incident_id)
                if history:
                    messages = history + messages
            except Exception as e:
                logger.debug("Diagnosis history failed: %s", e)

    for attempt in range(1, RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    API_URL,
                    headers={
                        "x-api-key":         api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type":      "application/json",
                    },
                    json={
                        "model":       MODEL,
                        "max_tokens":  MAX_TOKENS,
                        "system":      DECISION_PROMPT,
                        "messages":    messages,
                        "tools":       TOOLS,
                        "tool_choice": {"type": "tool", "name": "incident_decision"},
                    },
                )

            if r.status_code != 200:
                logger.warning("Claude API %d (attempt %d)", r.status_code, attempt)
                continue

            # Extract tool_use block — schema guaranteed by API
            content_blocks = r.json().get("content", [])
            tool_use = None
            for block in content_blocks:
                if block.get("type") == "tool_use":
                    tool_use = block
                    break

            if not tool_use:
                logger.warning("Claude did not return tool_use (attempt %d)", attempt)
                continue

            decision = tool_use.get("input", {})

            # Defensive validation (schema enforces this, but belt-and-suspenders)
            assert "action" in decision
            assert "confidence" in decision
            assert "whatsapp_message" in decision
            assert isinstance(decision["confidence"], (int, float))

            logger.info(
                "Claude: %s (%.0f%%) — %s",
                decision["action"],
                decision["confidence"] * 100,
                decision.get("reason", ""),
            )

            # Record turn for multi-turn continuity
            if incident_id and _has_memory:
                try:
                    hs = health.get("health_score", {})
                    m  = health.get("metrics", {})
                    summary = (
                        f"score={hs.get('score', 100):.0f} "
                        f"p95={m.get('overall_p95_ms', 0):.0f}ms "
                        f"err={m.get('error_rate', 0)*100:.0f}%"
                    )
                    _record_turn(incident_id, decision, summary)
                except Exception as e:
                    logger.debug("Failed to record diagnosis turn: %s", e)

            return decision

        except AssertionError:
            logger.warning("Claude missing fields (attempt %d)", attempt)
        except Exception as e:
            logger.warning("Claude error (attempt %d): %s", attempt, e)

    logger.error("Claude failed after %d attempts — safe default", RETRIES)
    return _safe_default()


def _safe_default() -> dict:
    return {
        "action":           "suppress",
        "reason":           "Claude unavailable — suppressing to avoid false alert",
        "confidence":       0.0,
        "whatsapp_message": "",
    }
