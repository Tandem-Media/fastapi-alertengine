import sys
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"


@pytest.fixture
def orchestrator_path():
    path = str(ORCHESTRATOR_DIR)
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path = [p for p in sys.path if p != path]
        for mod in ("loop", "policy", "lock", "idempotency"):
            sys.modules.pop(mod, None)


def _critical_health():
    return {
        "health_score": {"status": "critical", "score": 20},
        "metrics": {"overall_p95_ms": 450, "error_rate": 0.25},
    }


@pytest.mark.asyncio
async def test_no_duplicate_incident_when_lock_held(orchestrator_path):
    import loop

    tenant = {"tenant_id": "5f858940", "health_url": "http://health", "plan": "solo"}

    @asynccontextmanager
    async def _lock(*_args, **_kwargs):
        yield SimpleNamespace(valid=True)

    with ExitStack() as stack:
        stack.enter_context(patch("loop._fetch_health",
            new=AsyncMock(return_value=_critical_health())))
        stack.enter_context(patch("loop.can_mutate_state", return_value=True))
        stack.enter_context(patch("loop._get_tenant_incident",
            side_effect=[None, {"incident_id": "inc-existing"}]))
        stack.enter_context(patch("loop.incident_lock", side_effect=_lock))
        stack.enter_context(patch("loop.should_alert", return_value=True))
        stack.enter_context(patch("loop.can_monitor_more_services", return_value=True))
        stack.enter_context(patch("loop.incident_quota_remaining", return_value=1))
        stack.enter_context(patch("loop.get_tenant_plan",
            return_value=SimpleNamespace(has_claude_decision=True)))
        stack.enter_context(patch("loop.should_open_new_incident",
            return_value=False))
        stack.enter_context(patch("loop.claude_decide",
            new=AsyncMock(return_value={"action": "escalate", "confidence": 0.9})))

        await loop._process_tenant(tenant)


@pytest.mark.asyncio
async def test_creation_lock_uses_tenant_scope(orchestrator_path):
    import loop

    tenant = {"tenant_id": "5f858940", "health_url": "http://health", "plan": "solo"}
    lock_calls = []

    @asynccontextmanager
    async def _lock(key, ttl=0):
        lock_calls.append((key, ttl))
        yield SimpleNamespace(valid=True)

    with ExitStack() as stack:
        stack.enter_context(patch("loop.time.time", return_value=1778294865.25))
        stack.enter_context(patch("loop._fetch_health",
            new=AsyncMock(return_value=_critical_health())))
        stack.enter_context(patch("loop.can_mutate_state", return_value=True))
        stack.enter_context(patch("loop._get_tenant_incident",
            side_effect=[None, None]))
        stack.enter_context(patch("loop.incident_lock", side_effect=_lock))
        stack.enter_context(patch("loop.should_alert", return_value=True))
        stack.enter_context(patch("loop.can_monitor_more_services", return_value=True))
        stack.enter_context(patch("loop.incident_quota_remaining", return_value=1))
        stack.enter_context(patch("loop.get_tenant_plan",
            return_value=SimpleNamespace(has_claude_decision=True)))
        stack.enter_context(patch("loop.should_open_new_incident",
            return_value=False))
        stack.enter_context(patch("loop.claude_decide",
            new=AsyncMock(return_value={"action": "escalate", "confidence": 0.9})))

        await loop._process_tenant(tenant)

    assert lock_calls[0] == ("creating-5f858940", 10)


@pytest.mark.asyncio
async def test_idempotency_prevents_double_creation(orchestrator_path):
    import loop

    tenant = {"tenant_id": "5f858940", "health_url": "http://health", "plan": "solo"}
    claim_calls = []

    @asynccontextmanager
    async def _lock(*_args, **_kwargs):
        yield SimpleNamespace(valid=True)

    def _claim(action_id, metadata=None):
        claim_calls.append(action_id)
        return len(claim_calls) == 1

    with ExitStack() as stack:
        stack.enter_context(patch("loop.time.time", return_value=1778294865.99))
        stack.enter_context(patch("loop._fetch_health",
            new=AsyncMock(return_value=_critical_health())))
        stack.enter_context(patch("loop.can_mutate_state", return_value=True))
        stack.enter_context(patch("loop._get_tenant_incident",
            side_effect=[None, None, None, None]))
        stack.enter_context(patch("loop.incident_lock", side_effect=_lock))
        stack.enter_context(patch("loop.should_alert", return_value=True))
        stack.enter_context(patch("loop.can_monitor_more_services", return_value=True))
        stack.enter_context(patch("loop.incident_quota_remaining", return_value=1))
        stack.enter_context(patch("loop.get_tenant_plan",
            return_value=SimpleNamespace(has_claude_decision=True)))
        stack.enter_context(patch("loop.should_open_new_incident",
            return_value=True))
        stack.enter_context(patch("loop.claim_action", side_effect=_claim))
        stack.enter_context(patch("loop.claude_decide",
            new=AsyncMock(return_value={"action": "escalate", "confidence": 0.9})))

        await loop._process_tenant(tenant)
        await loop._process_tenant(tenant)

    assert len(claim_calls) == 1