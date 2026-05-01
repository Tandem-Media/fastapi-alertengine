# orchestrator/main.py
"""
Orchestrator entry point.
Exposes health endpoint, starts the loop.
"""

import asyncio
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("orchestrator")

_START_TIME = time.time()


def _check_redis() -> tuple[bool, str]:
    try:
        import redis
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(url, decode_responses=True)
        r.ping()
        return True, "connected"
    except Exception as e:
        return False, str(e)


# ── Health API ─────────────────────────────────────────────────────────────────

from fastapi import FastAPI
import uvicorn

health_app = FastAPI(title="Orchestrator")


@health_app.get("/health")
def health():
    redis_ok, redis_msg = _check_redis()
    missing = [k for k in ["ALERTENGINE_BASE_URL", "ANTHROPIC_API_KEY", "ALERT_SECRET", "REDIS_URL"]
               if not os.getenv(k)]
    return {
        "status":        "ok" if (redis_ok and not missing) else "degraded",
        "uptime_s":      round(time.time() - _START_TIME, 1),
        "redis":         {"connected": redis_ok, "message": redis_msg},
        "missing_vars":  missing,
        "loop":          "active",
        "version":       "2.0.0",
    }


@health_app.get("/status")
def status():
    try:
        from memory import get_active_incident
        from degraded import status as degraded_status
        from dlq import get_count as dlq_count
        incident = get_active_incident()
        return {
            "active_incident": incident.get("incident_id") if incident else None,
            "stage":           incident.get("stage") if incident else None,
            "degraded_mode":   degraded_status(),
            "dlq_count":       dlq_count(),
        }
    except Exception as e:
        return {"error": str(e)}


@health_app.get("/audit/{incident_id}")
def audit_log(incident_id: str):
    try:
        from audit import get_audit_log
        return {"incident_id": incident_id, "log": get_audit_log(incident_id)}
    except Exception as e:
        return {"error": str(e)}


@health_app.get("/dlq")
def dlq_entries():
    try:
        from dlq import get_all
        return {"entries": get_all(limit=20)}
    except Exception as e:
        return {"error": str(e)}


# ── Loop ───────────────────────────────────────────────────────────────────────

async def _run_loop_safe():
    """Start loop only if required vars are present."""
    required = ["ALERTENGINE_BASE_URL", "ANTHROPIC_API_KEY", "ALERT_SECRET", "REDIS_URL"]
    missing  = [k for k in required if not os.getenv(k)]

    if missing:
        logger.warning("Loop disabled — missing vars: %s", missing)
        logger.warning("Add vars in Railway dashboard to enable orchestration")
        # Keep alive without looping
        while True:
            await asyncio.sleep(60)

    redis_ok, redis_msg = _check_redis()
    if not redis_ok:
        logger.error("Redis unavailable: %s — loop disabled", redis_msg)
        while True:
            await asyncio.sleep(60)

    logger.info("✅ All vars present — starting orchestrator loop")
    from loop import run_loop
    await run_loop()


async def main():
    logger.info("⚡ Orchestrator starting")

    port = int(os.getenv("PORT", "9000"))

    config = uvicorn.Config(
        health_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        _run_loop_safe(),
    )


if __name__ == "__main__":
    asyncio.run(main())
