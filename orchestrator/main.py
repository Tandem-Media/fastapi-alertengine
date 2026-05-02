# orchestrator/main.py
"""
Orchestrator entry point.
Exposes health + onboarding API. Starts multi-tenant loop.
"""

import asyncio
import logging
import os
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
        r = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        r.ping()
        return True, "connected"
    except Exception as e:
        return False, str(e)


from fastapi import FastAPI
import uvicorn

health_app = FastAPI(title="AlertEngine Orchestrator")

# Mount onboarding router
from onboard import router as onboard_router
health_app.include_router(onboard_router)


@health_app.get("/health")
def health():
    redis_ok, redis_msg = _check_redis()
    missing = [k for k in ["ALERTENGINE_BASE_URL", "ANTHROPIC_API_KEY", "ALERT_SECRET", "REDIS_URL"]
               if not os.getenv(k)]
    return {
        "status":       "ok" if (redis_ok and not missing) else "degraded",
        "uptime_s":     round(time.time() - _START_TIME, 1),
        "redis":        {"connected": redis_ok, "message": redis_msg},
        "missing_vars": missing,
        "loop":         "active",
        "version":      "2.1.0",
    }


@health_app.get("/status")
def status():
    try:
        from tenants import list_active_tenants
        from degraded import status as degraded_status
        from dlq import get_count as dlq_count
        return {
            "active_tenants": len(list_active_tenants()),
            "degraded_mode":  degraded_status(),
            "dlq_count":      dlq_count(),
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


async def _run_loop_safe():
    required = ["ALERTENGINE_BASE_URL", "ANTHROPIC_API_KEY", "ALERT_SECRET", "REDIS_URL"]
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        logger.warning("Loop disabled — missing vars: %s", missing)
        while True:
            await asyncio.sleep(60)

    redis_ok, redis_msg = _check_redis()
    if not redis_ok:
        logger.error("Redis unavailable: %s", redis_msg)
        while True:
            await asyncio.sleep(60)

    logger.info("✅ All vars present — starting multi-tenant loop")
    from loop import run_loop
    await run_loop()


async def main():
    logger.info("⚡ Orchestrator v2.1 starting")
    port   = int(os.getenv("PORT", "9000"))
    config = uvicorn.Config(health_app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), _run_loop_safe())


if __name__ == "__main__":
    asyncio.run(main())
