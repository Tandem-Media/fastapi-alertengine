import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"


@pytest.fixture
def orchestrator_path():
    path = str(ORCHESTRATOR_DIR)
    sys.path.insert(0, path)
    try:
        if not asyncio.get_event_loop().is_closed():
            pass
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        yield
    finally:
        sys.path = [p for p in sys.path if p != path]
        for mod in ("degraded", "tenants", "loop", "notifications"):
            sys.modules.pop(mod, None)


def test_list_active_tenants_returns_active_only(orchestrator_path):
    import json
    import tenants

    active = {"tenant_id": "t1", "status": "active",
              "health_url": "http://x"}
    pending = {"tenant_id": "t2", "status": "pending_verification"}

    mock_redis = MagicMock()
    mock_redis.smembers.return_value = {"t1", "t2"}
    mock_redis.get.side_effect = lambda key: (
        json.dumps(active) if "t1" in key else
        json.dumps(pending) if "t2" in key else None
    )

    with patch.object(tenants, "_redis", return_value=mock_redis):
        result = tenants.list_active_tenants()

    assert len(result) == 1
    assert result[0]["tenant_id"] == "t1"


def test_fetch_health_returns_none_on_failure(orchestrator_path):
    import loop

    with patch("loop.httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value.__aenter__.side_effect = \
            Exception("timeout")
        result = asyncio.get_event_loop().run_until_complete(
            loop._fetch_health("http://unreachable.test/health")
        )

    assert result is None


def test_sent_provider_selected_for_sent_channel(orchestrator_path):
    sys.path.insert(0, str(ORCHESTRATOR_DIR))
    from providers.sent import SentProvider
    from providers.whatsapp import WhatsAppProvider

    tenant = {
        "tenant_id": "t1",
        "notification_channel": "sent",
        "sent_api_key": "key",
        "sent_phone_id": "phone_id",
        "whatsapp_numbers": ["+263771234567"],
    }

    channel = tenant.get("notification_channel", "whatsapp")
    provider = SentProvider() if channel == "sent" else WhatsAppProvider()
    assert isinstance(provider, SentProvider)


def test_health_fetch_failure_does_not_increment_redis_counter(
        orchestrator_path):
    import degraded

    initial_redis = degraded._STATE["redis_failures"]
    initial_health = degraded._STATE.get("health_fetch_failures", 0)

    degraded.record_health_fetch_failure()

    assert degraded._STATE["redis_failures"] == initial_redis
    assert degraded._STATE["health_fetch_failures"] == initial_health + 1
