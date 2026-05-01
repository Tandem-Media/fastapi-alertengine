"""
contracts.py — SYSTEM ENFORCEMENT LAYER

Non-bypassable guarantees:
- Claude output is validated or rejected
- Pipeline transitions are strictly controlled
- Notifications cannot silently fail
- Redis schema cannot drift

If something violates contracts → FAIL FAST
"""

import json
from typing import Dict, Any

# ─────────────────────────────────────────────────────────────
# 1. CLAUDE DECISION CONTRACT
# ─────────────────────────────────────────────────────────────

REQUIRED_FIELDS = {"decision", "confidence", "reason", "action"}
VALID_DECISIONS = {"propose_fix", "validate_fix", "reject", "noop"}
VALID_ACTIONS = {"restart_service", "scale_up", "ignore"}

CONFIDENCE_THRESHOLD = 0.6


class InvalidClaudeOutput(Exception):
    pass


def validate_claude_output(raw: str) -> Dict[str, Any]:
    """
    Enforces strict JSON schema for Claude responses.
    Rejects anything malformed, partial, or low-confidence.
    """
    try:
        data = json.loads(raw)
    except Exception:
        raise InvalidClaudeOutput("Claude output is not valid JSON")

    if not REQUIRED_FIELDS.issubset(data.keys()):
        raise InvalidClaudeOutput("Missing required fields")

    if data["decision"] not in VALID_DECISIONS:
        raise InvalidClaudeOutput(f"Invalid decision: {data['decision']}")

    if not isinstance(data["confidence"], (int, float)):
        raise InvalidClaudeOutput("Confidence must be numeric")

    if data["confidence"] < CONFIDENCE_THRESHOLD:
        raise InvalidClaudeOutput("Confidence below threshold")

    action = data.get("action", {})
    if action.get("type") not in VALID_ACTIONS:
        raise InvalidClaudeOutput("Invalid action type")

    return data


# ─────────────────────────────────────────────────────────────
# 2. PIPELINE TRANSITION GUARDRAILS
# ─────────────────────────────────────────────────────────────

VALID_TRANSITIONS = {
    None: ["detected"],
    "detected": ["proposed"],
    "proposed": ["validated", "rejected"],
    "validated": ["authorized"],
    "authorized": ["executed"],
    "executed": ["recovered"],
    "recovered": []
}


class InvalidTransition(Exception):
    pass


def enforce_transition(current: str, new: str) -> str:
    """
    Ensures stage transitions are valid and safe.
    """
    allowed = VALID_TRANSITIONS.get(current, [])

    if new not in allowed:
        raise InvalidTransition(f"{current} → {new} not allowed")

    return new


# ─────────────────────────────────────────────────────────────
# 3. NOTIFICATION ENFORCEMENT
# ─────────────────────────────────────────────────────────────

class NotificationFailure(Exception):
    pass


async def enforce_notification(send_primary, send_fallback, payload: dict, dlq_push):
    """
    Guarantees that at least one notification channel succeeds.
    """
    try:
        primary_ok = await send_primary(payload)
        if primary_ok:
            return True
    except Exception:
        primary_ok = False

    try:
        fallback_ok = await send_fallback(payload)
        if fallback_ok:
            return True
    except Exception:
        fallback_ok = False

    # BOTH failed → push to DLQ
    await dlq_push({
        "type": "CRITICAL_NOTIFICATION_FAILURE",
        "payload": payload
    })

    raise NotificationFailure("All notification channels failed")


# ─────────────────────────────────────────────────────────────
# 4. REDIS SCHEMA VERSIONING
# ─────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 1


class SchemaMismatchError(Exception):
    pass


def enforce_schema(data: dict) -> dict:
    """
    Ensures all Redis records conform to expected schema version.
    """
    if "version" not in data:
        raise SchemaMismatchError("Missing schema version")

    if data["version"] != CURRENT_SCHEMA_VERSION:
        raise SchemaMismatchError(
            f"Schema mismatch: {data['version']} != {CURRENT_SCHEMA_VERSION}"
        )

    return data


def with_schema(data: dict) -> dict:
    """
    Attaches schema version to outgoing records.
    """
    data["version"] = CURRENT_SCHEMA_VERSION
    return data