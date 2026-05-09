"""
Tests for duplicate incident guard:
- Double-checked locking pattern in _process_tenant()
- Tenant-scoped creation lock key
- Idempotency prevents double creation
"""
import sys
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"


@pytest.fixture
def orchestrator_path():
    path = str(ORCHESTRATOR_DIR)
    sys.path.insert(0, path)
    yield
    sys.path = [p for p in sys.path if p != path]
    for mod in list(sys.modules.keys()):
        if mod in (
            "loop", "lock", "policy", "idempotency", "degraded",
            "tenants", "notifications", "memory", "pipeline",
            "audit", "dlq", "plans", "claude_engine",
            "action_generator", "providers",
        ):
            sys.modules.pop(mod, None)


def _fake_sentinel_token():
    """Return a truthy sentinel that acts as a lock token."""
    return "fake-lock-token"


def test_no_duplicate_incident_when_lock_held(orchestrator_path):
    """
    When _get_tenant_incident returns None on the first call (outside the lock)
    but returns an existing incident on the second call (inside the lock),
    open_incident() must not be called.
    """
    import loop
    import policy
    import idempotency

    existing_incident = {
        "incident_id": "inc-tenant-abc-9999",
        "tenant_id": "tenant-abc",
        "stage": "DETECTED",
    }

    call_count = [0]

    def fake_get_tenant_incident(tenant_id):
        call_count[0] += 1
        if call_count[0] == 1:
            return None   # first call: outside lock — nothing there
        return existing_incident   # second call: inside lock — another worker created it

    open_incident_mock = MagicMock(return_value={"incident_id": "inc-tenant-abc-0000"})

    @asynccontextmanager
    async def fake_incident_lock(key, ttl=30):
        yield _fake_sentinel_token()

    tenant = {
        "tenant_id": "tenant-abc",
        "health_url": "http://fake/health",
        "plan": "startup",
        "status": "active",
    }

    health = {
        "health_score": {"status": "critical", "score": 30},
        "metrics": {"overall_p95_ms": 1500, "error_rate": 0.4},
    }

    with patch.object(loop, "_fetch_health", new=AsyncMock(return_value=health)), \
         patch.object(loop, "_get_tenant_incident", side_effect=fake_get_tenant_incident), \
         patch.object(loop, "incident_lock", fake_incident_lock), \
         patch.object(loop, "open_incident", open_incident_mock), \
         patch.object(loop, "can_mutate_state", return_value=True), \
         patch.object(loop, "current_mode", return_value="normal"):

        asyncio.run(loop._process_tenant(tenant))

    open_incident_mock.assert_not_called()


def test_creation_lock_uses_tenant_scope(orchestrator_path):
    """
    Verify that when opening a new incident the lock key is
    'creating-{tenant_id}' rather than the incident ID.
    """
    import loop

    acquired_keys = []

    @asynccontextmanager
    async def capturing_incident_lock(key, ttl=30):
        acquired_keys.append(key)
        yield None   # yield None → token falsy → worker returns early

    tenant = {
        "tenant_id": "tenant-xyz",
        "health_url": "http://fake/health",
        "plan": "startup",
        "status": "active",
    }

    health = {
        "health_score": {"status": "critical", "score": 30},
        "metrics": {"overall_p95_ms": 1500, "error_rate": 0.4},
    }

    with patch.object(loop, "_fetch_health", new=AsyncMock(return_value=health)), \
         patch.object(loop, "_get_tenant_incident", return_value=None), \
         patch.object(loop, "incident_lock", capturing_incident_lock), \
         patch.object(loop, "can_mutate_state", return_value=True), \
         patch.object(loop, "current_mode", return_value="normal"):

        asyncio.run(loop._process_tenant(tenant))

    # The first (and only) lock acquired in the new-incident branch must be tenant-scoped
    assert len(acquired_keys) == 1, "incident_lock was not called exactly once"
    assert acquired_keys[0] == "creating-tenant-xyz", (
        f"Expected 'creating-tenant-xyz', got {acquired_keys[0]!r}"
    )


def test_idempotency_prevents_double_creation(orchestrator_path):
    """
    Call the new incident branch twice for the same tenant.
    The second call must be blocked by is_executed() returning True.
    """
    import loop

    executed_keys = set()

    def fake_is_executed(key):
        return key in executed_keys

    def fake_mark_executed(key, meta=None):
        executed_keys.add(key)
        return True

    open_incident_mock = MagicMock(return_value={
        "incident_id": "inc-tenant-idem-1000",
        "tenant_id": "tenant-idem",
        "stage": "DETECTED",
    })
    save_incident_mock = MagicMock(return_value=True)

    @asynccontextmanager
    async def fake_incident_lock(key, ttl=30):
        yield _fake_sentinel_token()

    tenant = {
        "tenant_id": "tenant-idem",
        "health_url": "http://fake/health",
        "plan": "startup",
        "status": "active",
    }

    health = {
        "health_score": {"status": "critical", "score": 30},
        "metrics": {"overall_p95_ms": 1500, "error_rate": 0.4},
    }

    fake_claude = {"action": "escalate", "confidence": 0.9}
    fake_decision = {
        "next_stage": "DETECTED",
        "actions": [],
        "reason": "test",
        "confidence": 0.9,
    }

    patches = [
        patch.object(loop, "_fetch_health", new=AsyncMock(return_value=health)),
        patch.object(loop, "_get_tenant_incident", return_value=None),
        patch.object(loop, "incident_lock", fake_incident_lock),
        patch.object(loop, "can_mutate_state", return_value=True),
        patch.object(loop, "current_mode", return_value="normal"),
        patch.object(loop, "should_alert", return_value=True),
        patch.object(loop, "can_monitor_more_services", return_value=True),
        patch.object(loop, "incident_quota_remaining", return_value=5),
        patch.object(loop, "get_tenant_plan",
                     return_value=MagicMock(has_claude_decision=True)),
        patch.object(loop, "renew_lock", return_value=True),
        patch.object(loop, "claude_decide", new=AsyncMock(return_value=fake_claude)),
        patch.object(loop, "decide_new_incident", return_value=fake_decision),
        patch.object(loop, "validate_decision_schema", return_value=(True, "ok")),
        patch.object(loop, "is_executed", side_effect=fake_is_executed),
        patch.object(loop, "mark_executed", side_effect=fake_mark_executed),
        patch.object(loop, "open_incident", open_incident_mock),
        patch.object(loop, "save_incident", save_incident_mock),
        patch.object(loop, "_save_tenant_active", MagicMock()),
        patch.object(loop, "increment_incident_count", return_value=tenant),
        patch.object(loop, "save_tenant", MagicMock()),
        patch.object(loop, "append_event", MagicMock()),
        patch.object(loop, "_execute_actions", new=AsyncMock(return_value={})),
    ]

    started = [p.start() for p in patches]
    try:
        # First call — should create the incident
        asyncio.run(loop._process_tenant(tenant))
        # Second call — idempotency should block creation
        asyncio.run(loop._process_tenant(tenant))
    finally:
        for p in patches:
            p.stop()

    # open_incident must have been called exactly once despite two process calls
    assert open_incident_mock.call_count == 1, (
        f"Expected open_incident to be called once, got {open_incident_mock.call_count}"
    )
