# orchestrator/diagnosis_memory.py
"""
Per-incident diagnosis history for multi-turn continuity.

Stores the last N Claude decisions per incident in Redis.
Injected into the Claude message list to prevent diagnosis flip-flopping
across polling cycles.

Without memory: Claude sees each poll independently and may oscillate between
"database issue" and "dependency failure" on successive calls.

With memory: Claude sees its previous reasoning and can refine or confirm
its diagnosis as the incident evolves.

Memory is cleared automatically when an incident resolves.
"""

import json
import logging
import os

import redis

logger = logging.getLogger("orchestrator.diagnosis_memory")

MAX_HISTORY  = 3      # Keep last 3 turns — balances context vs token cost
HISTORY_TTL  = 86400  # 24 hours — incidents shouldn't last longer


def _redis():
    url = os.getenv("REDIS_URL", os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


def record_turn(incident_id: str, decision: dict, health_summary: str) -> None:
    """
    Append a diagnosis turn to the incident's history.
    Caps at MAX_HISTORY entries using lpush + ltrim.

    Args:
        incident_id:    The incident identifier
        decision:       The Claude decision dict (action, reason, confidence)
        health_summary: Compact health string e.g. "score=23 p95=2847ms err=19%"
    """
    try:
        key   = f"orchestrator:diagnosis_history:{incident_id}"
        entry = {
            "decision": {
                "action":     decision.get("action"),
                "reason":     decision.get("reason"),
                "confidence": decision.get("confidence"),
            },
            "health_summary": health_summary,
        }
        r = _redis()
        r.lpush(key, json.dumps(entry))
        r.ltrim(key, 0, MAX_HISTORY - 1)
        r.expire(key, HISTORY_TTL)
    except Exception as e:
        logger.debug("record_turn failed: %s", e)


def build_history_messages(incident_id: str) -> list:
    """
    Build Anthropic-format message list from diagnosis history.

    Returns list of alternating assistant/user message dicts in
    chronological order, ready to prepend to the current message list.

    Example output:
        [
            {"role": "assistant", "content": '{"action":"escalate",...}'},
            {"role": "user",      "content": "Update: score=23 p95=2847ms err=19%"},
            {"role": "assistant", "content": '{"action":"validate",...}'},
            {"role": "user",      "content": "Update: score=28 p95=2100ms err=15%"},
        ]
    """
    try:
        key     = f"orchestrator:diagnosis_history:{incident_id}"
        entries = _redis().lrange(key, 0, -1)
        if not entries:
            return []

        messages = []
        # lpush stores newest first — reverse for chronological order
        for raw in reversed(entries):
            try:
                entry = json.loads(raw)
                messages.append({
                    "role":    "assistant",
                    "content": json.dumps(entry["decision"]),
                })
                messages.append({
                    "role":    "user",
                    "content": f"Update: {entry['health_summary']}",
                })
            except (json.JSONDecodeError, KeyError):
                continue

        return messages

    except Exception as e:
        logger.debug("build_history_messages failed: %s", e)
        return []


def clear_history(incident_id: str) -> None:
    """
    Clear diagnosis history for a resolved incident.
    Called from loop.py when an incident transitions to RECOVERED or CLOSED.
    """
    try:
        _redis().delete(f"orchestrator:diagnosis_history:{incident_id}")
        logger.debug("Cleared diagnosis history for incident %s", incident_id)
    except Exception as e:
        logger.debug("clear_history failed: %s", e)


def get_history_summary(incident_id: str) -> str:
    """
    Returns a human-readable summary of diagnosis history.
    Used for debugging and audit logging.
    """
    try:
        key     = f"orchestrator:diagnosis_history:{incident_id}"
        entries = _redis().lrange(key, 0, -1)
        if not entries:
            return "No diagnosis history"

        lines = [f"Diagnosis history ({len(entries)} turns):"]
        for i, raw in enumerate(reversed(entries), 1):
            try:
                entry    = json.loads(raw)
                decision = entry["decision"]
                lines.append(
                    f"  Turn {i}: {decision.get('action')} "
                    f"({decision.get('confidence', 0)*100:.0f}%) — "
                    f"{decision.get('reason', '')} | {entry['health_summary']}"
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return "\n".join(lines)

    except Exception as e:
        return f"History unavailable: {e}"
