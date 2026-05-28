# orchestrator/tenants.py
"""
Tenant registry — Redis-backed, no in-memory state.

Schema:
    tenant:{tenant_id}           → tenant record
    tenant:{tenant_id}:contacts  → list of contact records
    verify:{phone}               → verification code (TTL 5min)
"""

import json
import logging
import os
import secrets
import time
import uuid
from typing import Optional

logger = logging.getLogger("orchestrator.tenants")

TENANT_TTL        = 0        # permanent
VERIFY_TTL        = 300      # 5 minutes
TENANT_PREFIX     = "tenant:"
VERIFY_PREFIX     = "verify:"
ACTIVE_SET_KEY    = "orchestrator:active_tenants"
PHONE_INDEX_PREFIX = "orchestrator:phone:"


def _redis():
    import redis
    url = os.getenv("REDIS_URL",
          os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


# ── Tenant CRUD ────────────────────────────────────────────────────────────────

def create_tenant(
    service_name: str,
    health_url: str,
    whatsapp_numbers: list,
    plan: str = "solo",
    notification_channel: str = "whatsapp",
    telegram_bot_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    twilio_account_sid: Optional[str] = None,
    twilio_auth_token: Optional[str] = None,
    twilio_whatsapp_from: Optional[str] = None,
    sent_api_key: Optional[str] = None,
    sent_phone_id: Optional[str] = None,
    slack_webhook_url: Optional[str] = None,
    slack_channel: Optional[str] = None,
    recovery_webhook_url: Optional[str] = None,
) -> dict:
    """Register a new tenant. Contacts start as unverified."""
    tenant_id = str(uuid.uuid4())[:8]
    now       = time.time()

    initial_status = "active" if notification_channel == "telegram" else "pending_verification"

    tenant = {
        "schema_version":        "1.0.0",
        "tenant_id":             tenant_id,
        "service_name":          service_name,
        "health_url":            health_url,
        "status":                initial_status,
        "notification_channel":  notification_channel,
        "telegram_bot_token":    telegram_bot_token,
        "telegram_chat_id":      telegram_chat_id,
        "created_at":            now,
        "last_updated":          now,
        "plan":                  plan,
        "twilio_account_sid":    twilio_account_sid,
        "twilio_auth_token":     twilio_auth_token,
        "twilio_whatsapp_from":  twilio_whatsapp_from,
        "sent_api_key":          sent_api_key,
        "sent_phone_id":         sent_phone_id,
        "slack_webhook_url":     slack_webhook_url,
        "slack_channel":         slack_channel,
        "recovery_webhook_url":  recovery_webhook_url,
        "incident_count":        0,
        "incidents_this_month":  0,
        "incidents_reset_at":    now,
        "billing_cycle_start":   now,
        "services_monitored":    [],
    }

    contacts = [
        {
            "phone":     number,
            "verified":  False,
            "added_at":  now,
        }
        for number in whatsapp_numbers
    ]

    r = _redis()
    r.set(f"{TENANT_PREFIX}{tenant_id}", json.dumps(tenant))
    r.set(f"{TENANT_PREFIX}{tenant_id}:contacts", json.dumps(contacts))
    r.sadd(ACTIVE_SET_KEY, tenant_id)
    for number in whatsapp_numbers:
        r.set(f"{PHONE_INDEX_PREFIX}{number}", tenant_id)

    if notification_channel == "telegram":
        try:
            r.sadd(ACTIVE_SET_KEY, tenant_id)
        except Exception as e:
            logger.error("create_tenant: failed to SADD to active set: %s", e)

    logger.info("Tenant created: %s (%s) — %d contacts pending verification",
                tenant_id, service_name, len(contacts))
    return tenant


def get_tenant(tenant_id: str) -> Optional[dict]:
    try:
        data = _redis().get(f"{TENANT_PREFIX}{tenant_id}")
        return json.loads(data) if data else None
    except Exception as e:
        logger.error("get_tenant failed: %s", e)
        return None


def get_contacts(tenant_id: str) -> list:
    try:
        data = _redis().get(f"{TENANT_PREFIX}{tenant_id}:contacts")
        return json.loads(data) if data else []
    except Exception as e:
        logger.error("get_contacts failed: %s", e)
        return []


def get_verified_numbers(tenant_id: str) -> list:
    return [c["phone"] for c in get_contacts(tenant_id) if c.get("verified")]


def save_tenant(tenant: dict) -> bool:
    try:
        _redis().set(f"{TENANT_PREFIX}{tenant['tenant_id']}", json.dumps(tenant))
        return True
    except Exception as e:
        logger.error("save_tenant failed: %s", e)
        return False


def save_contacts(tenant_id: str, contacts: list) -> bool:
    try:
        _redis().set(f"{TENANT_PREFIX}{tenant_id}:contacts", json.dumps(contacts))
        return True
    except Exception as e:
        logger.error("save_contacts failed: %s", e)
        return False


def migrate_tenant(data: dict) -> dict:
    """Migrate tenant record to current schema version."""
    if "incidents_reset_at" not in data:
        data["incidents_reset_at"] = data.get(
            "billing_cycle_start", time.time()
        )
    return data


def increment_incident_count(tenant: dict) -> dict:
    """Increment incident count with monthly reset."""
    last_reset = float(tenant.get("incidents_reset_at", 0))
    days_since_reset = (time.time() - last_reset) / 86400
    if days_since_reset >= 30:
        tenant["incident_count"] = 0
        tenant["incidents_this_month"] = 0
        tenant["incidents_reset_at"] = time.time()
    current = int(tenant.get("incident_count", 0))
    tenant["incident_count"] = current + 1
    tenant["incidents_this_month"] = int(
        tenant.get("incidents_this_month", 0)
    ) + 1
    return tenant


def list_active_tenants() -> list:
    """Return all tenants with status=active."""
    try:
        r = _redis()
        tenant_ids = r.smembers(ACTIVE_SET_KEY)
        if not tenant_ids:
            keys = r.keys(f"{TENANT_PREFIX}*")
            tenant_ids = [
                k.replace(TENANT_PREFIX, "")
                for k in keys
                if ":" not in k.replace(TENANT_PREFIX, "", 1)
            ]
        tenants = []
        for tenant_id in tenant_ids:
            data = r.get(f"{TENANT_PREFIX}{tenant_id}")
            if data:
                try:
                    t = json.loads(data)
                    if t.get("status") == "active":
                        tenants.append(t)
                except Exception:
                    continue
        return tenants
    except Exception as e:
        logger.error("list_active_tenants failed: %s", e)
        return []


def activate_tenant(tenant_id: str) -> bool:
    tenant = get_tenant(tenant_id)
    if not tenant:
        return False
    tenant["status"]       = "active"
    tenant["last_updated"] = time.time()
    ok = save_tenant(tenant)
    if ok:
        try:
            _redis().sadd(ACTIVE_SET_KEY, tenant_id)
        except Exception as e:
            logger.error("activate_tenant: failed to SADD to active set: %s", e)
    return ok


def deactivate_tenant(tenant_id: str) -> bool:
    """Set tenant status to inactive and remove from the active set."""
    try:
        r      = _redis()
        tenant = get_tenant(tenant_id)
        if tenant:
            tenant["status"]       = "inactive"
            tenant["last_updated"] = time.time()
            r.set(f"{TENANT_PREFIX}{tenant_id}", json.dumps(tenant))
        r.srem(ACTIVE_SET_KEY, tenant_id)
        return True
    except Exception as e:
        logger.error("deactivate_tenant failed: %s", e)
        return False


# ── Verification ───────────────────────────────────────────────────────────────

def generate_verification_code(phone: str) -> str:
    """Generate and store a 6-digit verification code. TTL 5 minutes."""
    code = str(secrets.randbelow(900000) + 100000)
    key  = f"{VERIFY_PREFIX}{phone}"
    try:
        _redis().setex(key, VERIFY_TTL, code)
        logger.info("Verification code generated for %s", phone)
    except Exception as e:
        logger.error("Failed to store verification code: %s", e)
    return code


def verify_phone(phone: str, code: str) -> bool:
    """Check code. Returns True if valid. Deletes code on success (one-time use)."""
    key = f"{VERIFY_PREFIX}{phone}"
    try:
        r       = _redis()
        stored  = r.get(key)
        if stored and stored == code:
            r.delete(key)
            return True
        return False
    except Exception as e:
        logger.error("verify_phone failed: %s", e)
        return False


def mark_phone_verified(tenant_id: str, phone: str) -> bool:
    """Mark a contact as verified. Activate tenant if all verified."""
    contacts = get_contacts(tenant_id)
    updated  = False

    for contact in contacts:
        if contact["phone"] == phone:
            contact["verified"]     = True
            contact["verified_at"]  = time.time()
            updated = True

    if not updated:
        logger.warning("Phone %s not found in tenant %s contacts", phone, tenant_id)
        return False

    save_contacts(tenant_id, contacts)

    all_verified = all(c.get("verified") for c in contacts)
    if all_verified:
        activate_tenant(tenant_id)
        logger.info("All contacts verified — tenant %s is now ACTIVE", tenant_id)

    return True


def find_tenant_by_phone(phone: str) -> Optional[str]:
    """Find which tenant owns a phone number."""
    try:
        return _redis().get(f"{PHONE_INDEX_PREFIX}{phone}")
    except Exception as e:
        logger.error("find_tenant_by_phone failed: %s", e)
    return None
