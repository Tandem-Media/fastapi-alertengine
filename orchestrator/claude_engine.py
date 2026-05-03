# orchestrator/claude_engine.py
"""
Claude decision engine.

Returns structured decisions for the pipeline.
Uses a calm, engineer-friendly prompt for WhatsApp message generation.
"""

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("orchestrator.claude")

MODEL      = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 512
RETRIES    = 3
API_URL    = "https://api.anthropic.com/v1/messages"

# ── Decision system prompt ─────────────────────────────────────────────────────

DECISION_PROMPT = """You are an incident response assistant for backend APIs.

Your role is to translate raw system metrics into a structured decision AND a clear, calm WhatsApp alert for an engineer.

Internally reason through the situation:
1. Determine severity: degrading, critical, or stable — use latency, error rate, and trend.
2. Identify most likely cause: high latency = database/I/O/dependency; high errors = failing service/bad deploy; degrading trend = load or resource exhaustion.
3. Select a safe, practical fix: prefer low-risk actions (restart worker, scale service, clear queue). Never suggest destructive or irreversible actions.
4. Assign confidence: High (80-95%) if metrics strongly indicate a known pattern. Medium (60-79%) if somewhat clear. Low (<60%) if uncertain.

Output ONLY valid JSON in this exact format — nothing else:
{
  "action": "escalate | validate | suppress | recover",
  "reason": "one sentence explanation",
  "confidence": 0.0,
  "whatsapp_message": "the full WhatsApp message text"
}

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
Suggested fix:
<one-line action>
Confidence: <High/Medium/Low> (<percentage>%)
👉 Approve fix:
<recovery_url>
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
"""


def _build_prompt(health: dict, incident: Optional[dict], recovery_url: str = "") -> str:
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

    if inc:
        lines += [
            f"Active incident: {inc.get('incident_id', inc.get('id', 'none'))}",
            f"Stage: {inc.get('stage', 'none')}",
        ]

    return "\n".join(lines)


async def get_decision(
    health: dict,
    incident: Optional[dict] = None,
    recovery_url: str = "",
) -> dict:
    """
    Ask Claude to make a decision and generate a WhatsApp message.
    Returns structured decision dict. Never raises.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — returning safe default")
        return _safe_default()

    prompt = _build_prompt(health, incident, recovery_url)

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
                        "model":      MODEL,
                        "max_tokens": MAX_TOKENS,
                        "system":     DECISION_PROMPT,
                        "messages":   [{"role": "user", "content": prompt}],
                    },
                )

            if r.status_code != 200:
                logger.warning("Claude API %d (attempt %d)", r.status_code, attempt)
                continue

            content  = r.json().get("content", [{}])[0].get("text", "")
            # Strip markdown fences if present
            content  = content.strip().strip("```json").strip("```").strip()
            decision = json.loads(content)

            # Validate required fields
            assert "action" in decision
            assert "confidence" in decision
            assert "whatsapp_message" in decision
            assert isinstance(decision["confidence"], (int, float))

            logger.info("Claude: %s (%.0f%%) — %s",
                        decision["action"], decision["confidence"] * 100,
                        decision.get("reason", ""))
            return decision

        except json.JSONDecodeError as e:
            logger.warning("Claude invalid JSON (attempt %d): %s", attempt, e)
        except AssertionError:
            logger.warning("Claude missing fields (attempt %d)", attempt)
        except Exception as e:
            logger.warning("Claude error (attempt %d): %s", attempt, e)

    logger.error("Claude failed after %d attempts — safe default", RETRIES)
    return _safe_default()


def _safe_default() -> dict:
    return {
        "action":            "suppress",
        "reason":            "Claude unavailable — suppressing to avoid false alert",
        "confidence":        0.0,
        "whatsapp_message":  "",
    }
