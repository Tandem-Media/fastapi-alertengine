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

TENANT_TTL       = 0        # permanent
VERIFY_TTL       = 300      # 5 minutes
TENANT_PREFIX    = "tenant:"
VERIFY_PREFIX    = "verify:"
ACTIVE_SET_KEY   = "orchestrator:active_tenants"


def _redis():
    import redis
    url = os.getenv("REDIS_URL",
          os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
    return redis.Redis.from_url(url, decode_responses=True)


# ── Tenant CRUD ────────────────────────────────────────────────────────────────

def create_tenant(service_name: str, health_url: str, whatsapp_numbers: list) -> dict:
    """Register a new tenant. Contacts start as unverified."""
    tenant_id = str(uuid.uuid4())[:8]
    now       = time.time()

    tenant = {
        "schema_version": "1.0.0",
        "tenant_id":      tenant_id,
        "service_name":   service_name,
        "health_url":     health_url,
        "status":         "pending_verification",
        "created_at":     now,
        "last_updated":   now,
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


def list_active_tenants() -> list:
    """Return all tenants with status=active."""
    try:
        r    = _redis()
        keys = r.keys(f"{TENANT_PREFIX}*")
        # Filter out :contacts and :incident keys
        tenant_keys = [k for k in keys
                       if ":" not in k.replace(TENANT_PREFIX, "", 1)]
        tenants = []
        for key in tenant_keys:
            data = r.get(key)
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
    return save_tenant(tenant)


# ── Verification ───────────────────────────────────────────────────────────────

def generate_verification_code(phone: str) -> str:
    """Generate and store a 6-digit verification code. TTL 5 minutes."""
    code = str(secrets.randbelow(900000) + 100000)   # 100000-999999
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

    # Check if all contacts are now verified
    all_verified = all(c.get("verified") for c in contacts)
    if all_verified:
        activate_tenant(tenant_id)
        logger.info("All contacts verified — tenant %s is now ACTIVE", tenant_id)

    return True


def find_tenant_by_phone(phone: str) -> Optional[str]:
    """Find which tenant owns a phone number."""
    try:
        r    = _redis()
        keys = r.keys(f"{TENANT_PREFIX}*:contacts")
        for key in keys:
            data = r.get(key)
            if data:
                contacts = json.loads(data)
                for c in contacts:
                    if c["phone"] == phone:
                        tenant_id = key.replace(TENANT_PREFIX, "").replace(":contacts", "")
                        return tenant_id
    except Exception as e:
        logger.error("find_tenant_by_phone failed: %s", e)
    return None
