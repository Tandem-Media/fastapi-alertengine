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


from fastapi import FastAPI, HTTPException
import uvicorn

health_app = FastAPI(title="AlertEngine Orchestrator")

# Mount onboarding router
from onboard import router as onboard_router
from onboarding_api import router as onboarding_router
health_app.include_router(onboard_router)
health_app.include_router(onboarding_router)


from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os as _os

_STATIC_DIR = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.exists(_STATIC_DIR):
    health_app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

@health_app.get("/onboarding", include_in_schema=False)
def onboarding_page():
    path = _os.path.join(_os.path.dirname(__file__), "static", "onboarding.html")
    if _os.path.exists(path):
        return FileResponse(path)
    return {"error": "onboarding.html not found in static/"}

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
        from pipeline import STAGE_GATES
        from circuit_breaker import is_open

        def cb_status(provider: str) -> dict:
            return {"open": is_open(provider)}

        return {
            "active_tenants": len(list_active_tenants()),
            "degraded_mode":  degraded_status(),
            "dlq_count":      dlq_count(),
            "stage_gates":    STAGE_GATES,
            "circuit_breakers": {
                "whatsapp": cb_status("whatsapp"),
                "telegram": cb_status("telegram"),
                "webhook":  cb_status("webhook"),
                "sent":     cb_status("sent"),
            },
        }
    except Exception as e:
        return {"error": str(e)}


@health_app.get("/audit/{incident_id}")
def audit_log(incident_id: str, tenant_id: str):
    try:
        from tenants import get_tenant
        from audit import get_audit_log
        tenant = get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404,
                                detail="Tenant not found")
        log = get_audit_log(incident_id)
        owned = (
            any(e.get("tenant_id") == tenant_id for e in log)
            or incident_id.startswith(f"inc-{tenant_id}")
            or incident_id.startswith(f"test-{tenant_id}")
        )
        if log and not owned:
            raise HTTPException(status_code=403,
                                detail="Access denied to this incident")
        return {"incident_id": incident_id, "log": log}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("audit_log error: %s", e)
        raise HTTPException(status_code=500,
                            detail="Internal server error")


@health_app.get("/delivery/{incident_id}")
def delivery_log(incident_id: str, tenant_id: str):
    try:
        from tenants import get_tenant
        from delivery_ledger import get_delivery_log, all_failed
        tenant = get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404,
                                detail="Tenant not found")
        log = get_delivery_log(incident_id)
        owned = (
            any(e.get("tenant_id") == tenant_id for e in log)
            or incident_id.startswith(f"inc-{tenant_id}")
            or incident_id.startswith(f"test-{tenant_id}")
        )
        if log and not owned:
            raise HTTPException(status_code=403,
                                detail="Access denied to this incident")
        return {
            "incident_id": incident_id,
            "attempts":    len(log),
            "all_failed":  all_failed(incident_id),
            "log":         log,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delivery_log error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@health_app.get("/dlq")
def dlq_entries(tenant_id: str):
    try:
        from tenants import get_tenant
        from plans import get_tenant_plan
        from dlq import get_all
        tenant = get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        plan = get_tenant_plan(tenant)
        if not plan.has_dlq_access:
            raise HTTPException(
                status_code=403,
                detail=f"DLQ access not available on {tenant.get('plan', 'solo')} plan. Upgrade to startup or higher.",
            )
        return {"entries": get_all(limit=20)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("dlq_entries error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@health_app.get("/action/recover")
async def recover_action(token: str):
    """
    Human-authorized recovery endpoint.
    Validates JWT token, enforces replay protection,
    and returns authorization confirmation.
    Called when engineer taps the recovery link in WhatsApp/Telegram.
    """
    try:
        from action_generator import validate_and_consume
        valid, payload, reason = validate_and_consume(token)
        if not valid:
            raise HTTPException(status_code=401, detail=reason)
        return {
            "authorized":    True,
            "incident_id":   payload.get("incident_id"),
            "tenant_id":     payload.get("tenant_id"),
            "action":        payload.get("action"),
            "authorized_at": time.time(),
            "message":       "Recovery action authorized. System will execute fix.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
