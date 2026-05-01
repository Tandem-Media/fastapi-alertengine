# orchestrator/claude_engine.py
"""
Claude decision engine.

Rules:
- Returns ONLY structured decisions
- No side effects
- No state storage
- Retries up to 3 times on failure
- Returns safe default on total failure
"""

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("orchestrator.claude")

MODEL       = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS  = 256
RETRIES     = 3
API_URL     = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """You are an incident response decision engine for a fintech payment API.

You receive system health data and active incident state.
You return a single structured JSON decision.

Allowed actions:
- escalate: open or escalate incident
- validate: send recovery link to operator
- suppress: do not alert (signal is noise)
- recover: system has recovered, close incident

Output ONLY valid JSON in this exact format:
{
  "action": "escalate | validate | suppress | recover",
  "reason": "one sentence explanation",
  "confidence": 0.0-1.0
}

Rules:
- confidence < 0.6 means you are uncertain — use suppress
- Only recommend recover when score > 70 and error_rate < 0.05
- Only recommend validate when score < 40 and error_rate > 0.2
- Be conservative — false positives in fintech are costly
"""


def _build_prompt(health: dict, incident: Optional[dict]) -> str:
    hs  = health.get("health_score", {})
    m   = health.get("metrics", {})
    inc = incident or {}

    lines = [
        f"Health status: {hs.get('status', 'unknown')}",
        f"Score: {hs.get('score', 100):.0f}/100",
        f"Trend: {hs.get('trend', 'stable')}",
        f"P95 latency: {m.get('overall_p95_ms', 0):.0f}ms",
        f"Error rate: {m.get('error_rate', 0)*100:.1f}%",
        f"Sample size: {m.get('sample_size', 0)}",
    ]

    if inc:
        lines += [
            f"",
            f"Active incident: {inc.get('id')}",
            f"Stage: {inc.get('stage')}",
            f"Duration: {inc.get('started_at', 0):.0f}s ago",
        ]

    return "\n".join(lines)


async def get_decision(health: dict, incident: Optional[dict] = None) -> dict:
    """
    Ask Claude to make a decision about the current system state.
    Returns structured decision dict. Never raises.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — returning default decision")
        return {"action": "suppress", "reason": "No API key", "confidence": 0.0}

    prompt = _build_prompt(health, incident)

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
                        "system":     SYSTEM_PROMPT,
                        "messages":   [{"role": "user", "content": prompt}],
                    },
                )

            if r.status_code != 200:
                logger.warning("Claude API %d (attempt %d)", r.status_code, attempt)
                continue

            content = r.json().get("content", [{}])[0].get("text", "")
            decision = json.loads(content)

            # Validate structure
            assert "action" in decision
            assert "confidence" in decision
            assert isinstance(decision["confidence"], (int, float))

            logger.info("Claude decision: %s (%.0f%%)",
                        decision["action"], decision["confidence"] * 100)
            return decision

        except json.JSONDecodeError as e:
            logger.warning("Claude returned invalid JSON (attempt %d): %s", attempt, e)
        except Exception as e:
            logger.warning("Claude error (attempt %d): %s", attempt, e)

    # Safe fallback — suppress on total failure
    logger.error("Claude failed after %d attempts — suppressing", RETRIES)
    return {
        "action":     "suppress",
        "reason":     "Claude unavailable — fail safe",
        "confidence": 0.0,
    }
