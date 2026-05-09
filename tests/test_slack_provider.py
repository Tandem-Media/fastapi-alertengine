import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"


@pytest.fixture
def orchestrator_path():
    path = str(ORCHESTRATOR_DIR)
    sys.path.insert(0, path)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        yield
    finally:
        sys.path = [p for p in sys.path if p != path]
        for mod in ("providers.slack", "providers", "plans"):
            sys.modules.pop(mod, None)


def test_slack_provider_sends_to_webhook(orchestrator_path):
    from providers.slack import SlackProvider

    tenant = {"tenant_id": "t1", "slack_webhook_url": "https://hooks.slack.test/123"}

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("providers.slack.is_open", return_value=False), patch(
        "providers.slack.httpx.AsyncClient"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        result = asyncio.get_event_loop().run_until_complete(
            SlackProvider().send(tenant, "inc-1", "hello")
        )

    assert result.success is True


def test_slack_provider_circuit_breaker_suppresses(orchestrator_path):
    from providers.slack import SlackProvider

    tenant = {"tenant_id": "t1", "slack_webhook_url": "https://hooks.slack.test/123"}

    with patch("providers.slack.is_open", return_value=True), patch(
        "providers.slack.httpx.AsyncClient"
    ) as mock_client_cls:
        result = asyncio.get_event_loop().run_until_complete(
            SlackProvider().send(tenant, "inc-1", "hello")
        )

    assert result.success is False
    assert result.error == "circuit_breaker_open"
    mock_client_cls.assert_not_called()


def test_slack_provider_no_webhook_returns_false(orchestrator_path):
    from providers.slack import SlackProvider

    tenant = {"tenant_id": "t1"}

    with patch("providers.slack.is_open", return_value=False):
        result = asyncio.get_event_loop().run_until_complete(
            SlackProvider().send(tenant, "inc-1", "hello")
        )

    assert result.success is False
    assert result.error == "webhook_not_configured"


def test_slack_not_available_on_solo_plan(orchestrator_path):
    from plans import get_tenant_plan

    tenant = {"tenant_id": "t1", "plan": "solo"}
    assert get_tenant_plan(tenant).has_slack is False


def test_slack_available_on_startup_plan(orchestrator_path):
    from plans import get_tenant_plan

    tenant = {"tenant_id": "t1", "plan": "startup"}
    assert get_tenant_plan(tenant).has_slack is True
