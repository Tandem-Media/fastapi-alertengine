import sys
from pathlib import Path

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
        for mod in ("main", "action_generator", "onboard", "plans", "tenants"):
            sys.modules.pop(mod, None)


def test_consume_token_fails_closed_on_redis_error(orchestrator_path, monkeypatch):
    import action_generator
    import redis

    monkeypatch.setattr(
        action_generator,
        "verify_recovery_token",
        lambda token: {"tenant_id": "tenant-1", "incident_id": "inc-1", "action": "restart"},
    )
    monkeypatch.setattr(
        redis.Redis,
        "from_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("redis unavailable")),
    )

    valid, reason = action_generator.consume_token("signed-token", expected_tenant_id="tenant-1")
    assert valid is False
    assert reason == "Token validation unavailable. Try again."


def test_hobby_plan_exists_in_plans(orchestrator_path):
    import plans

    assert "hobby" in plans.PLANS
    hobby = plans.PLANS["hobby"]
    assert hobby.name == "hobby"
    assert hobby.max_services == 1
    assert hobby.included_incidents == 5
    assert hobby.default_provider == "telegram"
    assert hobby.has_claude_decision is False


def test_developer_plan_exists_in_plans(orchestrator_path):
    import plans

    assert "developer" in plans.PLANS
    developer = plans.PLANS["developer"]
    assert developer.name == "developer"
    assert developer.max_services == 1
    assert developer.included_incidents == 10
    assert developer.default_provider == "whatsapp"
    assert developer.has_claude_decision is True


def test_hobby_tenant_cannot_trigger_claude_diagnosis(orchestrator_path, monkeypatch):
    import main
    import onboard

    tenant_id = "tenant-hobby-1"
    tenant = {
        "tenant_id": tenant_id,
        "service_name": "svc",
        "health_url": "http://svc/health",
        "status": "active",
        "plan": "hobby",
        "notification_channel": "telegram",
    }

    monkeypatch.setattr(onboard, "get_tenant", lambda tid: tenant if tid == tenant_id else None)
    monkeypatch.setattr(onboard, "incident_quota_remaining", lambda t: 5)
    monkeypatch.setattr(onboard, "get_tenant_plan", lambda t: type("Plan", (), {"name": "hobby", "has_claude_decision": False})())

    client = TestClient(main.health_app)
    resp = client.post(f"/tenant/{tenant_id}/test")

    assert resp.status_code == 403
    assert resp.json()["detail"]["detail"] == (
        "AI diagnosis not available on Hobby plan. Upgrade to Developer or higher."
    )
    assert resp.json()["detail"]["code"] == "PLAN_FEATURE_UNAVAILABLE"
