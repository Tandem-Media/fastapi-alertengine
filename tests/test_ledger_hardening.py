import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"


@pytest.fixture
def orchestrator_path():
    path = str(ORCHESTRATOR_DIR)
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path = [p for p in sys.path if p != path]
        for mod in (
            "main", "audit", "delivery_ledger", "onboard", "onboarding_api",
            "memory", "notifications", "action_generator", "pipeline",
            "plans", "tenants", "loop",
        ):
            sys.modules.pop(mod, None)


def _active_tenant(tenant_id: str) -> dict:
    return {
        "tenant_id":            tenant_id,
        "service_name":         "svc",
        "health_url":           "http://svc/health",
        "status":               "active",
        "plan":                 "growth",
        "notification_channel": "whatsapp",
    }


def _fake_plan() -> MagicMock:
    p = MagicMock()
    p.name = "growth"
    p.has_voice_escalation = False
    p.has_claude_decision  = True
    return p


# ── 1. Audit log non-empty after test incident ────────────────────────────────

def test_audit_log_non_empty_after_test_incident(orchestrator_path, monkeypatch):
    """Triggering /tenant/{id}/test must write at least one entry to the audit log."""
    import audit, memory, tenants, plans, action_generator, notifications, onboard, main

    tenant_id = "t-audit-001"
    tenant    = _active_tenant(tenant_id)

    audit_entries: list[dict] = []

    def fake_append_event(
        incident_id, stage, decision, reason, confidence,
        actor="pipeline", action_id=None, metadata=None, tenant_id=None,
    ):
        audit_entries.append({
            "incident_id": incident_id,
            "stage":       stage,
            "tenant_id":   tenant_id,
        })
        return True

    _get_tenant   = lambda tid: tenant if tid == tenant_id else None
    _get_plan     = lambda t: _fake_plan()
    _get_quota    = lambda t: 10
    _get_verified = lambda tid: []
    _gen_token    = lambda iid, tenant_id=None, **kw: "fake-token"

    monkeypatch.setattr(tenants,          "get_tenant",               _get_tenant)
    monkeypatch.setattr(onboard,          "get_tenant",               _get_tenant)
    monkeypatch.setattr(plans,            "get_tenant_plan",           _get_plan)
    monkeypatch.setattr(onboard,          "get_tenant_plan",           _get_plan)
    monkeypatch.setattr(plans,            "incident_quota_remaining",  _get_quota)
    monkeypatch.setattr(onboard,          "incident_quota_remaining",  _get_quota)
    monkeypatch.setattr(memory,           "get_active_incident",       lambda tenant_id=None: None)
    monkeypatch.setattr(memory,           "save_incident",             lambda rec: True)
    monkeypatch.setattr(audit,            "append_event",              fake_append_event)
    monkeypatch.setattr(tenants,          "get_verified_numbers",      _get_verified)
    monkeypatch.setattr(onboard,          "get_verified_numbers",      _get_verified)
    monkeypatch.setattr(action_generator, "generate_recovery_token",   _gen_token)
    monkeypatch.setattr(notifications,    "dispatch",                  AsyncMock(return_value=True))

    client = TestClient(main.health_app)
    resp   = client.post(f"/tenant/{tenant_id}/test")
    assert resp.status_code == 200, resp.text

    incident_id = resp.json()["incident_id"]
    assert len(audit_entries) > 0, "append_event was not called — audit log would be empty"
    assert any(e["incident_id"] == incident_id for e in audit_entries)
    assert any(e["tenant_id"]   == tenant_id   for e in audit_entries)


# ── 2. Delivery ledger non-empty after test incident ─────────────────────────

def test_delivery_ledger_non_empty_after_test_incident(orchestrator_path, monkeypatch):
    """Triggering /tenant/{id}/test must invoke dispatch → delivery ledger has entries."""
    import audit, memory, tenants, plans, action_generator, notifications, onboard, main

    tenant_id     = "t-delivery-001"
    tenant        = _active_tenant(tenant_id)
    dispatch_mock = AsyncMock(return_value=True)

    _get_tenant   = lambda tid: tenant if tid == tenant_id else None
    _get_plan     = lambda t: _fake_plan()
    _get_quota    = lambda t: 10
    _get_verified = lambda tid: []
    _gen_token    = lambda iid, tenant_id=None, **kw: "fake-token"

    monkeypatch.setattr(tenants,          "get_tenant",               _get_tenant)
    monkeypatch.setattr(onboard,          "get_tenant",               _get_tenant)
    monkeypatch.setattr(plans,            "get_tenant_plan",           _get_plan)
    monkeypatch.setattr(onboard,          "get_tenant_plan",           _get_plan)
    monkeypatch.setattr(plans,            "incident_quota_remaining",  _get_quota)
    monkeypatch.setattr(onboard,          "incident_quota_remaining",  _get_quota)
    monkeypatch.setattr(memory,           "get_active_incident",       lambda tenant_id=None: None)
    monkeypatch.setattr(memory,           "save_incident",             lambda rec: True)
    monkeypatch.setattr(audit,            "append_event",              lambda *a, **kw: True)
    monkeypatch.setattr(tenants,          "get_verified_numbers",      _get_verified)
    monkeypatch.setattr(onboard,          "get_verified_numbers",      _get_verified)
    monkeypatch.setattr(action_generator, "generate_recovery_token",   _gen_token)
    monkeypatch.setattr(notifications,    "dispatch",                  dispatch_mock)

    client = TestClient(main.health_app)
    resp   = client.post(f"/tenant/{tenant_id}/test")
    assert resp.status_code == 200, resp.text

    incident_id = resp.json()["incident_id"]
    assert dispatch_mock.call_count > 0, "dispatch was not called — delivery ledger would be empty"
    assert dispatch_mock.call_args.kwargs["incident_id"] == incident_id


# ── 3. /audit returns 200 for owner tenant ────────────────────────────────────

def test_audit_endpoint_returns_200_for_owner(orchestrator_path, monkeypatch):
    import audit, tenants, main

    tenant_id   = "t-owner-001"
    incident_id = f"test-{tenant_id}-1000"
    fake_log    = [{"incident_id": incident_id, "stage": "DETECTED", "tenant_id": tenant_id}]

    monkeypatch.setattr(tenants, "get_tenant",    lambda tid: _active_tenant(tid))
    monkeypatch.setattr(audit,   "get_audit_log", lambda iid: fake_log)

    client = TestClient(main.health_app)
    resp   = client.get(f"/audit/{incident_id}", params={"tenant_id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["incident_id"] == incident_id
    assert len(resp.json()["log"]) > 0


# ── 4. /audit returns 403 for non-owner tenant ───────────────────────────────

def test_audit_endpoint_returns_403_for_non_owner(orchestrator_path, monkeypatch):
    import audit, tenants, main

    owner_id    = "t-owner-002"
    other_id    = "t-other-002"
    incident_id = f"test-{owner_id}-2000"
    fake_log    = [{"incident_id": incident_id, "stage": "DETECTED", "tenant_id": owner_id}]

    monkeypatch.setattr(tenants, "get_tenant",    lambda tid: _active_tenant(tid))
    monkeypatch.setattr(audit,   "get_audit_log", lambda iid: fake_log)

    client = TestClient(main.health_app)
    resp   = client.get(f"/audit/{incident_id}", params={"tenant_id": other_id})
    assert resp.status_code == 403
    assert "Access denied" in resp.json()["detail"]


# ── 5. /delivery returns 200 for owner tenant ────────────────────────────────

def test_delivery_endpoint_returns_200_for_owner(orchestrator_path, monkeypatch):
    import delivery_ledger, tenants, main

    tenant_id   = "t-owner-003"
    incident_id = f"test-{tenant_id}-3000"
    fake_log    = [{"incident_id": incident_id, "provider": "whatsapp",
                    "tenant_id": tenant_id, "success": True}]

    monkeypatch.setattr(tenants,         "get_tenant",        lambda tid: _active_tenant(tid))
    monkeypatch.setattr(delivery_ledger, "get_delivery_log",  lambda iid: fake_log)
    monkeypatch.setattr(delivery_ledger, "all_failed",        lambda iid: False)

    client = TestClient(main.health_app)
    resp   = client.get(f"/delivery/{incident_id}", params={"tenant_id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["incident_id"] == incident_id
    assert resp.json()["attempts"] == 1


# ── 6. /delivery returns 403 for non-owner tenant ────────────────────────────

def test_delivery_endpoint_returns_403_for_non_owner(orchestrator_path, monkeypatch):
    import delivery_ledger, tenants, main

    owner_id    = "t-owner-004"
    other_id    = "t-other-004"
    incident_id = f"test-{owner_id}-4000"
    fake_log    = [{"incident_id": incident_id, "provider": "whatsapp",
                    "tenant_id": owner_id, "success": True}]

    monkeypatch.setattr(tenants,         "get_tenant",        lambda tid: _active_tenant(tid))
    monkeypatch.setattr(delivery_ledger, "get_delivery_log",  lambda iid: fake_log)
    monkeypatch.setattr(delivery_ledger, "all_failed",        lambda iid: False)

    client = TestClient(main.health_app)
    resp   = client.get(f"/delivery/{incident_id}", params={"tenant_id": other_id})
    assert resp.status_code == 403
    assert "Access denied" in resp.json()["detail"]
