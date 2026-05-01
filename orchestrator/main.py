# orchestrator/main.py
"""
Orchestrator entry point.
Validates env, exposes health endpoint, starts the loop.
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

REQUIRED_ENV = [
    "ALERTENGINE_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ALERT_SECRET",
    "REDIS_URL",
]

_START_TIME = time.time()


def _validate_env() -> bool:
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        return False
    return True


def _check_redis() -> tuple[bool, str]:
    try:
        import redis
        r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        r.ping()
        return True, "connected"
    except Exception as e:
        return False, str(e)


# ── Health API ─────────────────────────────────────────────────────────────────

from fastapi import FastAPI
import uvicorn

health_app = FastAPI(title="Orchestrator Health")


@health_app.get("/health")
def health():
    redis_ok, redis_msg = _check_redis()
    return {
        "status":    "ok" if redis_ok else "degraded",
        "uptime_s":  round(time.time() - _START_TIME, 1),
        "redis":     {"connected": redis_ok, "message": redis_msg},
        "loop":      "active",
        "version":   "2.0.0",
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


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    logger.info("⚡ Orchestrator starting")

    if not _validate_env():
        sys.exit(1)

    redis_ok, redis_msg = _check_redis()
    if not redis_ok:
        logger.error("Redis unavailable: %s", redis_msg)
        sys.exit(1)

    logger.info("✅ Redis connected")

    from loop import run_loop

    port = int(os.getenv("PORT", "9000"))

    # Run health server + orchestrator loop concurrently
    config = uvicorn.Config(
        health_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        run_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
