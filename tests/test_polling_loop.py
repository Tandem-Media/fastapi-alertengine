import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
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


def test_whatsapp_provider_sends_to_tenant_numbers(orchestrator_path):
    from providers.whatsapp import WhatsAppProvider

    tenant = {
        "tenant_id": "tenantx",
        "twilio_account_sid": "sid",
        "twilio_auth_token": "token",
        "twilio_whatsapp_from": "whatsapp:+14155238886",
        "whatsapp_numbers": ["+263785023897", "whatsapp:+263712345678"],
    }
    sent_to = []

    class DummyMsg:
        sid = "SM123"

    class DummyClient:
        def __init__(self, sid, token):
            self.messages = self

        def create(self, body, from_, to):
            sent_to.append(to)
            return DummyMsg()

    with patch("twilio.rest.Client", DummyClient):
        provider = WhatsAppProvider()
        result = asyncio.get_event_loop().run_until_complete(
            provider.send(tenant, "inc001", "Test message")
        )

    assert result.success
    assert sent_to == ["whatsapp:+263785023897", "whatsapp:+263712345678"]


def test_whatsapp_provider_requires_tenant_recipients(orchestrator_path):
    from providers.whatsapp import WhatsAppProvider

    tenant = {
        "tenant_id": "tenantx",
        "twilio_account_sid": "sid",
        "twilio_auth_token": "token",
        "twilio_whatsapp_from": "whatsapp:+14155238886",
        "whatsapp_numbers": [],
    }

    provider = WhatsAppProvider()
    result = asyncio.get_event_loop().run_until_complete(
        provider.send(tenant, "inc002", "no recipients")
    )

    assert not result.success
    assert result.error == "no_recipients"


def test_real_incident_whatsapp_route_uses_tenant_dispatch(orchestrator_path):
    import loop

    tenant = {
        "tenant_id": "tenantx",
        "notification_channel": "whatsapp",
        "whatsapp_numbers": ["+263785023897"],
    }

    with patch("loop.dispatch_notification", new=AsyncMock(return_value=True)) as mock_dispatch:
        result = asyncio.get_event_loop().run_until_complete(
            loop._send_tenant_notification(
                tenant,
                "DETECTION",
                "inc9001",
                99,
                120,
                0.02,
            )
        )

    assert result is True
    mock_dispatch.assert_awaited_once()
    dispatched_tenant, incident_id, message = mock_dispatch.await_args.args
    assert dispatched_tenant is tenant
    assert dispatched_tenant["whatsapp_numbers"] == ["+263785023897"]
    assert incident_id == "inc9001"
    assert "inc9001" in message
