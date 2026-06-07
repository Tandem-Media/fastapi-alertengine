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
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("orchestrator")

_START_TIME = time.time()

# Rate limiter — per IP, in-memory
# Protects public endpoints from flooding and abuse
limiter = Limiter(key_func=get_remote_address)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_alert_secret()
    yield


health_app = FastAPI(title="AlertEngine Orchestrator", lifespan=lifespan)

# Wire rate limiter into app
health_app.state.limiter = limiter
health_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Mount onboarding router
from onboard import router as onboard_router
from onboarding_api import router as onboarding_router
health_app.include_router(onboard_router)
health_app.include_router(onboarding_router)

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
        logger.error("Internal error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@health_app.post("/action/recover/confirm")
@limiter.limit("30/hour")
async def recover_action_confirm(token: str):
    """
    Human-authorized recovery endpoint.

    Flow:
    1. Validate JWT token (signature, expiry, single-use via Redis SET NX)
    2. Write AUTHORIZED audit entry
    3. POST to tenant's recovery_webhook_url with retry logic (3 attempts, exponential backoff)
    4. Write EXECUTED or WEBHOOK_FAILED audit entry
    5. Push to DLQ on webhook failure
    6. Return confirmation with webhook status
    """
    try:
        from action_generator import validate_and_consume
        valid, payload, reason = validate_and_consume(token)
        if not valid:
            raise HTTPException(status_code=401, detail=reason)

        incident_id   = payload.get("incident_id", "unknown")
        tenant_id     = payload.get("tenant_id")
        action        = payload.get("action", "recover")
        authorized_at = time.time()

        # Write AUTHORIZED audit entry
        try:
            from audit import append_event
            append_event(
                incident_id=incident_id,
                stage="AUTHORIZED",
                decision=action,
                reason="Engineer authorized recovery via secure link",
                confidence=1.0,
                actor="engineer",
                tenant_id=tenant_id,
            )
        except Exception as audit_err:
            logger.warning("Audit write failed on recovery: %s", audit_err)

        # Fetch tenant to get recovery_webhook_url
        webhook_url = None
        try:
            from tenants import get_tenant
            tenant = get_tenant(tenant_id) if tenant_id else None
            if tenant:
                webhook_url = tenant.get("recovery_webhook_url")
        except Exception as e:
            logger.warning("Could not fetch tenant for webhook: %s", e)

        # POST to recovery webhook with retry
        webhook_success = False
        webhook_error   = None

        if webhook_url:
            import httpx as _httpx
            webhook_payload = {
                "incident_id":   incident_id,
                "tenant_id":     tenant_id,
                "action":        action,
                "authorized_at": authorized_at,
                "authorized_by": "engineer",
            }

            # 3 attempts with exponential backoff (2s, 4s)
            for attempt in range(1, 4):
                try:
                    async with _httpx.AsyncClient(timeout=10) as client:
                        resp = await client.post(
                            webhook_url,
                            json=webhook_payload,
                            headers={"X-AlertEngine-Incident": incident_id},
                        )
                        if resp.status_code < 400:
                            webhook_success = True
                            logger.info(
                                "Recovery webhook succeeded (attempt %d): status=%d",
                                attempt, resp.status_code,
                            )
                            break
                        else:
                            webhook_error = f"HTTP {resp.status_code}"
                            logger.warning(
                                "Recovery webhook attempt %d failed: status=%d",
                                attempt, resp.status_code,
                            )
                except Exception as e:
                    webhook_error = str(e)
                    logger.warning("Recovery webhook attempt %d error: %s", attempt, e)

                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)  # 2s, 4s backoff

            # Write EXECUTED or WEBHOOK_FAILED audit entry
            try:
                from audit import append_event
                append_event(
                    incident_id=incident_id,
                    stage="EXECUTED" if webhook_success else "WEBHOOK_FAILED",
                    decision=action,
                    reason=(
                        f"Webhook succeeded: {webhook_url}"
                        if webhook_success
                        else f"Webhook failed after 3 attempts: {webhook_error}"
                    ),
                    confidence=1.0,
                    actor="orchestrator",
                    tenant_id=tenant_id,
                )
            except Exception as audit_err:
                logger.warning("Audit write failed on execution: %s", audit_err)

            # Push to DLQ if webhook failed
            if not webhook_success:
                try:
                    from dlq import push as dlq_push
                    dlq_push(
                        incident_id=incident_id,
                        action_type="RECOVERY_WEBHOOK",
                        error=webhook_error or "Unknown error",
                        stage="WEBHOOK_FAILED",
                        action_id=f"recovery-{incident_id}",
                    )
                    logger.error(
                        "Recovery webhook failed after 3 attempts → DLQ: %s", webhook_url)
                except Exception as dlq_err:
                    logger.error("DLQ push failed: %s", dlq_err)
        else:
            logger.warning(
                "No recovery_webhook_url for tenant %s — authorized but no action executed",
                tenant_id,
            )

        return {
            "authorized":      True,
            "incident_id":     incident_id,
            "tenant_id":       tenant_id,
            "action":          action,
            "authorized_at":   authorized_at,
            "webhook_called":  webhook_url is not None,
            "webhook_success": webhook_success,
            "message": (
                "Recovery action authorized and executed."
                if webhook_success
                else "Recovery authorized. " + (
                    "Webhook failed — check DLQ."
                    if webhook_url else
                    "No recovery webhook configured."
                )
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("recover_action_confirm error: %s", e)
        logger.error("Internal error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")



# ── Auditor's One-Pager: PDF audit report ─────────────────────────────────────

@health_app.get("/audit/{incident_id}/report", tags=["audit"])
@limiter.limit("60/hour")
async def incident_audit_report(incident_id: str, tenant_id: Optional[str] = None):
    """
    Generate a PDF audit report for an incident.

    Returns a professional PDF containing:
    - Incident summary (ID, tenant, plan, policy version)
    - Complete audit trail with actor attribution
    - AI diagnosis and reasoning detail
    - Active policy thresholds at time of incident
    - Attestation statement for auditors

    Usage:
        curl -o report.pdf "https://your-orchestrator/audit/{incident_id}/report?tenant_id={tenant_id}"

    The PDF is designed to be handed directly to SOC 2, PCI DSS,
    HIPAA, or internal compliance auditors.
    """
    try:
        from audit_report import generate_incident_report
        from fastapi.responses import Response

        pdf_bytes = generate_incident_report(incident_id, tenant_id)

        filename = f"alertengine-audit-{incident_id}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(pdf_bytes)),
            }
        )
    except Exception as e:
        logger.error("Audit report generation failed for %s: %s", incident_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ── Self-serve signup endpoint ────────────────────────────────────────────────

@health_app.post("/signup", tags=["onboarding"])
@limiter.limit("10/hour")
async def signup(request: Request):
    """
    Self-serve signup endpoint. Called from the landing page form.

    Stores lead in Redis, sends auto-response to customer,
    and notifies Lenard for manual tenant creation.

    Body:
        {
            "name": "John Smith",
            "email": "john@example.com",
            "phone": "+263712345678",
            "health_url": "https://myapp.railway.app/health/alerts",
            "plan": "growth",
            "channel": "whatsapp",
            "recovery_webhook_url": "https://myapp.railway.app/recovery",
            "github_repo": "owner/repo"
        }
    """
    try:
        from signup import store_lead, send_auto_response, notify_lenard

        body = await request.json()

        # Validate required fields
        required = ["name", "email", "phone", "health_url", "plan"]
        missing  = [f for f in required if not body.get(f)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required fields: {', '.join(missing)}"
            )

        # Sanitize plan name
        plan = body.get("plan", "growth").lower().strip()
        valid_plans = {"starter", "growth", "team", "compliance", "platform", "enterprise"}
        if plan not in valid_plans:
            plan = "growth"

        lead = {
            "name":                 body.get("name", "").strip(),
            "email":                body.get("email", "").strip().lower(),
            "phone":                body.get("phone", "").strip(),
            "health_url":           body.get("health_url", "").strip(),
            "plan":                 plan,
            "channel":              body.get("channel", "whatsapp").strip(),
            "recovery_webhook_url": body.get("recovery_webhook_url", "").strip() or None,
            "github_repo":          body.get("github_repo", "").strip() or None,
            "message":              body.get("message", "").strip() or None,
        }

        lead_id = store_lead(lead)

        # Fire and forget — don't block response on email
        import asyncio
        asyncio.create_task(
            asyncio.to_thread(send_auto_response, lead)
        )
        asyncio.create_task(
            asyncio.to_thread(notify_lenard, lead)
        )

        logger.info("New signup: %s %s (%s)", lead_id, lead["name"], lead["plan"])

        return {
            "success":  True,
            "lead_id":  lead_id,
            "message":  "Thanks for signing up! You'll be live within 2 hours.",
            "plan":     plan,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Signup error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@health_app.get("/signup/leads", tags=["onboarding"])
async def list_signup_leads(admin_key: str = "", limit: int = 20):
    """List recent signup leads. Requires admin key."""
    expected = os.getenv("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        from signup import list_leads
        leads = list_leads(limit)
        return {"leads": leads, "count": len(leads)}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@health_app.post("/signup/leads/{lead_id}/onboarded", tags=["onboarding"])
async def mark_onboarded(lead_id: str, admin_key: str = ""):
    """Mark a lead as onboarded after tenant creation."""
    expected = os.getenv("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        from signup import mark_lead_onboarded
        ok = mark_lead_onboarded(lead_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Lead not found")
        return {"success": True, "lead_id": lead_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# ── Diff-in-Pocket: Git commit webhook ────────────────────────────────────────

@health_app.post("/commits/webhook", include_in_schema=True, tags=["commits"])
@limiter.limit("100/hour")
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
        logger.warning("Bad request: %s", e)
        raise HTTPException(status_code=400, detail="Invalid request")


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
        logger.warning("Bad request: %s", e)
        raise HTTPException(status_code=400, detail="Invalid request")


@health_app.get("/commits/{tenant_id}", include_in_schema=True, tags=["commits"])
async def get_commits(tenant_id: str, limit: int = 10):
    """List recent stored commits for a tenant."""
    try:
        from commit_context import get_recent_commits
        import time
        commits = get_recent_commits(tenant_id, time.time(), limit=limit)
        return {"tenant_id": tenant_id, "commits": commits, "count": len(commits)}
    except Exception as e:
        logger.error("Internal error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


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
