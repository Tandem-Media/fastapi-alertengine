# orchestrator/main.py
"""
Orchestrator entry point.
Exposes health + onboarding API. Starts multi-tenant loop.
"""

import asyncio
import html
import logging
import os
import time
from urllib.parse import quote

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("orchestrator")

_START_TIME = time.time()

INSECURE_DEFAULTS = {
    "change-this-in-prod",
    "secret",
    "your-secret-key",
    "changeme",
    "alertengine",
    "default",
    "",
}


def _validate_alert_secret() -> None:
    secret = os.getenv("ALERT_SECRET", "")
    env = os.getenv("ENVIRONMENT", "development").lower()
    is_production = env == "production"

    weak = secret in INSECURE_DEFAULTS or len(secret) < 32

    if weak and is_production:
        raise RuntimeError(
            "ALERT_SECRET is insecure. Set a cryptographically "
            "random secret of at least 32 characters before "
            "deploying to production. "
            "Generate one with: python -c \"import secrets; "
            "print(secrets.token_hex(32))\""
        )
    elif weak:
        logger.warning(
            "ALERT_SECRET is weak or using an insecure default. "
            "Set a strong secret before going to production."
        )


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


from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
import uvicorn


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_alert_secret()
    yield


health_app = FastAPI(title="AlertEngine Orchestrator", lifespan=lifespan)

# Mount onboarding router
from onboard import router as onboard_router
from onboarding_api import router as onboarding_router
# Standard onboarding: phone verification flow (production)
# See orchestrator/onboard.py
health_app.include_router(onboard_router)
# Quick-start onboarding: immediate activation (dev/testing)
# See orchestrator/onboarding_api.py
health_app.include_router(onboarding_router)


from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
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
                "slack":    cb_status("slack"),
            },
        }
    except Exception as e:
        return {"error": str(e)}


@health_app.get("/admin/tenants")
def admin_tenants(admin_key: str):
    expected_key = os.getenv("ADMIN_KEY")
    if not expected_key or admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        from tenants import list_active_tenants

        tenants = list_active_tenants()
        sanitized = [
            {
                "tenant_id": t.get("tenant_id"),
                "service_name": t.get("service_name"),
                "health_url": t.get("health_url"),
                "status": t.get("status"),
                "plan": t.get("plan"),
                "notification_channel": t.get("notification_channel"),
                "incident_count": t.get("incident_count"),
                "created_at": t.get("created_at"),
                "last_updated": t.get("last_updated"),
            }
            for t in tenants
        ]
        return {
            "total": len(sanitized),
            "tenants": sanitized,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("admin_tenants error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


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
    Side-effect-free recovery preview endpoint.
    Verifies token signature/expiry and asks for explicit confirmation.
    """
    try:
        from action_generator import verify_recovery_token

        payload = verify_recovery_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        incident_id = html.escape(str(payload.get("incident_id", "unknown")), quote=True)
        action = html.escape(str(payload.get("action", "unknown")), quote=True)
        encoded_token = quote(token, safe="")
        confirm_url = html.escape(f"/action/recover/confirm?token={encoded_token}", quote=True)
        return HTMLResponse(
            content=(
                "<h2>⚡ AlertEngine Recovery Authorization</h2>"
                f"<p>Incident: {incident_id}</p>"
                f"<p>Action: {action}</p>"
                "<p><strong>Are you sure you want to authorize this recovery?</strong></p>"
                "<p>This action cannot be undone. "
                "Token expires 5 minutes after incident detection.</p>"
                f'<form method="post" action="{confirm_url}">'
                '<button style="background:#2563eb;color:#fff;border:none;'
                'padding:10px 16px;border-radius:6px;font-weight:600;cursor:pointer;"'
                ' type="submit">Confirm Recovery</button>'
                "</form>"
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@health_app.post("/action/recover/confirm")
async def recover_action_confirm(token: str):
    """
    Human-authorized recovery endpoint.
    Validates JWT token, enforces replay protection,
    writes audit entry, and returns authorization confirmation.
    Called when engineer taps the recovery link in WhatsApp/Telegram.
    """
    try:
        from action_generator import validate_and_consume
        valid, payload, reason = validate_and_consume(token)
        if not valid:
            raise HTTPException(status_code=401, detail=reason)
        # Write audit entry for the authorization
        try:
            from audit import append_event
            append_event(
                incident_id=payload.get("incident_id", "unknown"),
                stage="AUTHORIZED",
                decision="recover",
                reason="Engineer authorized recovery via secure link",
                confidence=1.0,
                actor="engineer",
                tenant_id=payload.get("tenant_id"),
            )
        except Exception as audit_err:
            logger.warning("Audit write failed on recovery: %s", audit_err)
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



# ── Diff-in-Pocket: Git commit webhook ────────────────────────────────────────

@health_app.post("/commits/webhook", include_in_schema=True, tags=["commits"])
async def github_webhook(request: Request):
    """
    Receive GitHub push webhook events and store commit context.

    Setup in GitHub:
      Repository → Settings → Webhooks → Add webhook
      Payload URL: https://your-orchestrator/commits/webhook
      Content type: application/json
      Events: Just the push event

    The commit SHA and message will be correlated with incidents
    that occur within 10 minutes of the push.
    """
    try:
        from commit_context import store_commit
        import time

        body = await request.json()

        # GitHub push event format
        repo   = body.get("repository", {}).get("full_name", "")
        branch = body.get("ref", "").replace("refs/heads/", "")
        tenant_id = request.headers.get("X-AlertEngine-Tenant-ID", "")

        if not tenant_id:
            # Try to match by repo URL if tenant has it configured
            tenant_id = body.get("repository", {}).get("name", "unknown")

        commits_stored = 0
        for commit in body.get("commits", []):
            sha     = commit.get("id", "")
            message = commit.get("message", "").split("\n")[0]
            author  = commit.get("author", {}).get("name", "unknown")
            ts_str  = commit.get("timestamp", "")

            try:
                import datetime
                dt = datetime.datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00"))
                timestamp = dt.timestamp()
            except Exception:
                timestamp = time.time()

            files_changed = (
                commit.get("added", []) +
                commit.get("modified", []) +
                commit.get("removed", [])
            )

            store_commit(
                tenant_id=tenant_id,
                sha=sha,
                message=message,
                author=author,
                timestamp=timestamp,
                files_changed=files_changed,
                repo=repo,
                branch=branch,
            )
            commits_stored += 1

        return {
            "stored":    commits_stored,
            "tenant_id": tenant_id,
            "repo":      repo,
            "branch":    branch,
        }

    except Exception as e:
        logger.error("Commit webhook error: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@health_app.post("/commits/{tenant_id}", include_in_schema=True, tags=["commits"])
async def store_commit_manual(tenant_id: str, request: Request):
    """
    Manually push a commit to AlertEngine for correlation.
    Use this if you prefer not to set up a GitHub webhook.

    Body:
        {
            "sha": "a1b2c3d",
            "message": "Fix checkout query isolation level",
            "author": "John",
            "timestamp": 1716900000.0,
            "files_changed": ["models/order.py", "queries/checkout.sql"],
            "additions": 12,
            "deletions": 3
        }
    """
    try:
        from commit_context import store_commit
        import time

        body = await request.json()
        store_commit(
            tenant_id=tenant_id,
            sha=body.get("sha", ""),
            message=body.get("message", ""),
            author=body.get("author", "unknown"),
            timestamp=body.get("timestamp", time.time()),
            files_changed=body.get("files_changed", []),
            additions=body.get("additions", 0),
            deletions=body.get("deletions", 0),
            repo=body.get("repo", ""),
            branch=body.get("branch", "main"),
        )
        return {"stored": True, "tenant_id": tenant_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@health_app.get("/commits/{tenant_id}", include_in_schema=True, tags=["commits"])
async def get_commits(tenant_id: str, limit: int = 10):
    """List recent stored commits for a tenant."""
    try:
        from commit_context import get_recent_commits
        import time
        commits = get_recent_commits(tenant_id, time.time(), limit=limit)
        return {"tenant_id": tenant_id, "commits": commits, "count": len(commits)}
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
