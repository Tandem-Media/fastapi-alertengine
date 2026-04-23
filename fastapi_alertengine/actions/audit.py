# fastapi_alertengine/actions/audit.py
"""
v1.6 — Action Audit Log

Full structured audit trail for every action attempt (success or failure).
Written to Redis ZSET when available, printed to stderr as fallback.

Schema per entry:
{
    "ts":          unix timestamp (float),
    "user_id":     str,
    "action":      str,
    "service":     str,
    "result":      "success" | "failure" | "denied" | "ip_mismatch",
    "detail":      str,
    "jti":         str | null,
    "incident_id": str | null,
    "client_ip":   str | null,
}
"""
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_AUDIT_KEY_PREFIX = "alertengine:audit:"
_AUDIT_TTL        = 86_400 * 30   # 30 days
_AUDIT_MAX_ENTRIES = 10_000


def log_action(
    user_id:     str,
    action:      str,
    service:     str,
    result:      str,   # "success" | "failure" | "denied" | "ip_mismatch"
    detail:      str    = "",
    jti:         Optional[str] = None,
    incident_id: Optional[str] = None,
    client_ip:   Optional[str] = None,
    rdb=None,
) -> None:
    """
    Append one audit entry. Never raises.

    Written to Redis ZSET alertengine:audit:{service} with unix timestamp
    as score. Falls back to structured log line when Redis unavailable.
    """
    entry = {
        "ts":          time.time(),
        "user_id":     user_id,
        "action":      action,
        "service":     service,
        "result":      result,
        "detail":      detail,
        "jti":         jti,
        "incident_id": incident_id,
        "client_ip":   client_ip,
    }

    if rdb is not None:
        try:
            key   = f"{_AUDIT_KEY_PREFIX}{service}"
            value = json.dumps(entry)
            pipe  = rdb.pipeline(transaction=False)
            pipe.zadd(key, {value: entry["ts"]})
            pipe.zremrangebyrank(key, 0, -(_AUDIT_MAX_ENTRIES + 1))
            pipe.expire(key, _AUDIT_TTL)
            pipe.execute()
            return
        except Exception as exc:
            logger.warning("audit log Redis write failed: %s", exc)

    # Fallback: structured log line
    logger.info(
        "ACTION_AUDIT service=%s action=%s user=%s result=%s jti=%s detail=%s",
        service, action, user_id, result, jti, detail,
    )


def read_audit_log(
    rdb,
    service:  str,
    last_n:   int   = 100,
    since_ts: float = 0.0,
) -> list:
    """Read audit entries for service. Returns [] on error."""
    try:
        key = f"{_AUDIT_KEY_PREFIX}{service}"
        raw = rdb.zrangebyscore(key, since_ts, "+inf",
                                start=0, num=last_n, withscores=True)
        results = []
        for member, score in raw:
            try:
                entry = json.loads(member)
                entry.setdefault("ts", score)
                results.append(entry)
            except Exception:
                continue
        return results
    except Exception as exc:
        logger.warning("read_audit_log failed: %s", exc)
        return []
