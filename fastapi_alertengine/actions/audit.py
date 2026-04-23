# fastapi_alertengine/actions/audit.py
"""
Audit logging for remote actions.
Logs structured JSON to the fastapi_alertengine.audit logger.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("fastapi_alertengine.audit")


def log_action(
    user_id:    str,
    action:     str,
    service:    str,
    result:     str,
    detail:     str = "",
    jti:        Optional[str] = None,
    incident_id: Optional[str] = None,
    client_ip:  Optional[str] = None,
    rdb=None,
) -> None:
    """
    Log a structured audit record for a remote action.

    result: "success" | "failure" | "denied" | "ip_mismatch"
    """
    record = {
        "user_id":    user_id,
        "action":     action,
        "service":    service,
        "result":     result,
        "detail":     detail,
        "timestamp":  datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if jti:
        record["jti"] = jti
    if incident_id:
        record["incident_id"] = incident_id
    if client_ip:
        record["client_ip"] = client_ip

    msg = json.dumps(record)

    if result in ("success", "denied"):
        logger.info(msg)
    else:
        logger.warning(msg)
