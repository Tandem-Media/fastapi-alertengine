# fastapi_alertengine/actions/audit.py
"""
Structured audit logging for fastapi-alertengine remote actions.

Every infrastructure action — whether it succeeds or fails — must call
``log_action``.  The output is a single JSON line emitted through the
standard Python ``logging`` module so it integrates with any log aggregation
pipeline (Datadog, CloudWatch, ELK, etc.) without additional configuration.

Example log line::

    {"user_id": "user-42", "action": "restart", "service": "payments-api",
     "timestamp": "2026-04-15T16:00:00.000000Z", "result": "success",
     "detail": "Restarted payments-api (container id: abc123)"}
"""

import json
import logging
from datetime import datetime, timezone
from typing import Literal

logger = logging.getLogger("fastapi_alertengine.audit")


def log_action(
    *,
    user_id: str,
    action: str,
    service: str,
    result: Literal["success", "failure"],
    detail: str = "",
) -> None:
    """
    Emit a structured JSON audit record for an infrastructure action.

    Parameters
    ----------
    user_id:
        Identifier of the user who triggered the action.
    action:
        The action that was performed (e.g. ``"restart"``).
    service:
        Target service / container name.
    result:
        ``"success"`` or ``"failure"``.
    detail:
        Optional human-readable description of the outcome or error.
    """
    record = {
        "user_id": user_id,
        "action": action,
        "service": service,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "result": result,
        "detail": detail,
    }
    # Emit at WARNING level for failures so they stand out in log dashboards.
    level = logging.WARNING if result == "failure" else logging.INFO
    logger.log(level, json.dumps(record))
