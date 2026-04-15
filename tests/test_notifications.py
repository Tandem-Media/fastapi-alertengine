# tests/test_notifications.py
"""
Tests for fastapi_alertengine.notifications.whatsapp

Covers:
  - Happy path: message is sent and result fields are correct
  - ``whatsapp:`` prefix auto-added to bare E.164 numbers
  - ``whatsapp:`` prefix NOT duplicated when already present
  - Signed URL format and token validity
  - BASE_URL override via kwarg and environment variable
  - Missing environment variables raise RuntimeError
  - Missing twilio package raises ImportError
  - Twilio client errors propagate
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from fastapi_alertengine.actions.tokens import verify_action_token

# ── Fixtures ──────────────────────────────────────────────────────────────────

_SECRET = "test-secret-for-notifications"

_TWILIO_SID = "ACtest000000000000000000000000000000"
_TWILIO_AUTH = "test-auth-token"
_TWILIO_FROM = "+14155238886"


@pytest.fixture(autouse=True)
def set_secret(monkeypatch):
    """Inject ACTION_SECRET_KEY for every test in this module."""
    monkeypatch.setenv("ACTION_SECRET_KEY", _SECRET)


def _make_twilio_message(sid="SM1234567890abcdef1234567890abcdef"):
    """Return a minimal mock that resembles a Twilio Message resource."""
    msg = MagicMock()
    msg.sid = sid
    return msg


@pytest.fixture()
def mock_twilio_client():
    """
    Patch twilio.rest.Client so no real HTTP requests are made.
    Returns (mock_client_class, mock_client_instance).
    """
    mock_instance = MagicMock()
    mock_instance.messages.create.return_value = _make_twilio_message()

    with patch(
        "fastapi_alertengine.notifications.whatsapp.TwilioClient",
        return_value=mock_instance,
    ) as mock_cls:
        # Ensure TwilioClient is importable by injecting a fake twilio module
        # so the lazy import inside send_whatsapp_alert succeeds.
        yield mock_cls, mock_instance


# The module under test is imported after fixtures so monkeypatch can work.
# However we can import it at module level because autouse sets the env var.
from fastapi_alertengine.notifications.whatsapp import (  # noqa: E402
    WhatsAppNotificationResult,
    send_whatsapp_alert,
)


# ── Helper ────────────────────────────────────────────────────────────────────


def _call(
    mock_twilio,
    action="restart",
    service="payments-api",
    user_id="u1",
    to="+447911123456",
    base_url="https://alerts.example.com",
    from_number=_TWILIO_FROM,
    account_sid=_TWILIO_SID,
    auth_token=_TWILIO_AUTH,
):
    """Thin wrapper to reduce repetition in tests."""
    _, mock_instance = mock_twilio
    with patch(
        "fastapi_alertengine.notifications.whatsapp.TwilioClient",
        return_value=mock_instance,
    ):
        return send_whatsapp_alert(
            action,
            service,
            user_id,
            to,
            base_url=base_url,
            from_number=from_number,
            account_sid=account_sid,
            auth_token=auth_token,
        )


# ── Happy path ────────────────────────────────────────────────────────────────


class TestSendWhatsAppAlertHappyPath:
    def test_returns_result_dataclass(self, mock_twilio_client):
        result = _call(mock_twilio_client)
        assert isinstance(result, WhatsAppNotificationResult)

    def test_message_sid_propagated(self, mock_twilio_client):
        result = _call(mock_twilio_client)
        assert result.message_sid == "SM1234567890abcdef1234567890abcdef"

    def test_to_has_whatsapp_prefix(self, mock_twilio_client):
        result = _call(mock_twilio_client, to="+447911123456")
        assert result.to == "whatsapp:+447911123456"

    def test_to_prefix_not_duplicated(self, mock_twilio_client):
        result = _call(mock_twilio_client, to="whatsapp:+447911123456")
        assert result.to == "whatsapp:+447911123456"
        assert result.to.count("whatsapp:") == 1

    def test_signed_url_format(self, mock_twilio_client):
        result = _call(mock_twilio_client, base_url="https://alerts.example.com")
        assert result.signed_url.startswith(
            "https://alerts.example.com/action/confirm?token="
        )

    def test_signed_url_token_is_valid_jwt(self, mock_twilio_client):
        result = _call(mock_twilio_client, action="restart", service="svc", user_id="u42")
        token = result.signed_url.split("token=", 1)[1]
        payload = verify_action_token(token)
        assert payload["action"] == "restart"
        assert payload["service"] == "svc"
        assert payload["user_id"] == "u42"

    def test_body_contains_action_and_service(self, mock_twilio_client):
        result = _call(mock_twilio_client, action="restart", service="payments-api")
        assert "restart" in result.body
        assert "payments-api" in result.body

    def test_body_contains_signed_url(self, mock_twilio_client):
        result = _call(mock_twilio_client)
        assert result.signed_url in result.body

    def test_twilio_create_called_with_correct_args(self, mock_twilio_client):
        _, mock_instance = mock_twilio_client
        with patch(
            "fastapi_alertengine.notifications.whatsapp.TwilioClient",
            return_value=mock_instance,
        ):
            send_whatsapp_alert(
                "restart",
                "payments-api",
                "u1",
                "+447911123456",
                base_url="https://x.io",
                from_number=_TWILIO_FROM,
                account_sid=_TWILIO_SID,
                auth_token=_TWILIO_AUTH,
            )
        call_kwargs = mock_instance.messages.create.call_args.kwargs
        assert call_kwargs["to"] == "whatsapp:+447911123456"
        assert call_kwargs["from_"].startswith("whatsapp:")

    def test_from_number_gets_whatsapp_prefix(self, mock_twilio_client):
        _, mock_instance = mock_twilio_client
        with patch(
            "fastapi_alertengine.notifications.whatsapp.TwilioClient",
            return_value=mock_instance,
        ):
            send_whatsapp_alert(
                "restart",
                "svc",
                "u1",
                "+447911123456",
                base_url="https://x.io",
                from_number="+14155238886",  # no prefix
                account_sid=_TWILIO_SID,
                auth_token=_TWILIO_AUTH,
            )
        call_kwargs = mock_instance.messages.create.call_args.kwargs
        assert call_kwargs["from_"] == "whatsapp:+14155238886"

    def test_from_number_prefix_not_duplicated(self, mock_twilio_client):
        _, mock_instance = mock_twilio_client
        with patch(
            "fastapi_alertengine.notifications.whatsapp.TwilioClient",
            return_value=mock_instance,
        ):
            send_whatsapp_alert(
                "restart",
                "svc",
                "u1",
                "+447911123456",
                base_url="https://x.io",
                from_number="whatsapp:+14155238886",
                account_sid=_TWILIO_SID,
                auth_token=_TWILIO_AUTH,
            )
        call_kwargs = mock_instance.messages.create.call_args.kwargs
        assert call_kwargs["from_"].count("whatsapp:") == 1


# ── Base URL resolution ───────────────────────────────────────────────────────


class TestBaseUrlResolution:
    def test_kwarg_base_url_used(self, mock_twilio_client):
        result = _call(mock_twilio_client, base_url="https://custom.example.com")
        assert result.signed_url.startswith("https://custom.example.com/action/confirm")

    def test_base_url_trailing_slash_stripped(self, mock_twilio_client):
        result = _call(mock_twilio_client, base_url="https://custom.example.com/")
        assert "//action" not in result.signed_url

    def test_env_base_url_used_when_kwarg_absent(self, mock_twilio_client, monkeypatch):
        monkeypatch.setenv("BASE_URL", "https://env-base.example.com")
        _, mock_instance = mock_twilio_client
        with patch(
            "fastapi_alertengine.notifications.whatsapp.TwilioClient",
            return_value=mock_instance,
        ):
            result = send_whatsapp_alert(
                "restart",
                "svc",
                "u1",
                "+447911123456",
                from_number=_TWILIO_FROM,
                account_sid=_TWILIO_SID,
                auth_token=_TWILIO_AUTH,
            )
        assert result.signed_url.startswith("https://env-base.example.com/action/confirm")


# ── Environment variable resolution ──────────────────────────────────────────


class TestEnvVarResolution:
    """Kwargs take precedence; env vars are the fallback."""

    def _call_via_env(self, mock_instance, monkeypatch):
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", _TWILIO_SID)
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", _TWILIO_AUTH)
        monkeypatch.setenv("TWILIO_FROM_NUMBER", _TWILIO_FROM)
        monkeypatch.setenv("BASE_URL", "https://env.example.com")
        with patch(
            "fastapi_alertengine.notifications.whatsapp.TwilioClient",
            return_value=mock_instance,
        ):
            return send_whatsapp_alert("restart", "svc", "u1", "+447911123456")

    def test_env_vars_used_when_no_kwargs(self, mock_twilio_client, monkeypatch):
        _, mock_instance = mock_twilio_client
        result = self._call_via_env(mock_instance, monkeypatch)
        assert result.message_sid  # succeeds when env vars are set

    def test_missing_account_sid_raises(self, monkeypatch):
        monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", _TWILIO_AUTH)
        monkeypatch.setenv("TWILIO_FROM_NUMBER", _TWILIO_FROM)
        with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
            send_whatsapp_alert("restart", "svc", "u1", "+447911123456")

    def test_missing_auth_token_raises(self, monkeypatch):
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", _TWILIO_SID)
        monkeypatch.setenv("TWILIO_FROM_NUMBER", _TWILIO_FROM)
        with pytest.raises(RuntimeError, match="TWILIO_AUTH_TOKEN"):
            send_whatsapp_alert("restart", "svc", "u1", "+447911123456")

    def test_missing_from_number_raises(self, monkeypatch):
        monkeypatch.delenv("TWILIO_FROM_NUMBER", raising=False)
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", _TWILIO_SID)
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", _TWILIO_AUTH)
        with pytest.raises(RuntimeError, match="TWILIO_FROM_NUMBER"):
            send_whatsapp_alert("restart", "svc", "u1", "+447911123456")

    def test_missing_action_secret_key_raises(self, monkeypatch):
        monkeypatch.delenv("ACTION_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ACTION_SECRET_KEY"):
            send_whatsapp_alert(
                "restart",
                "svc",
                "u1",
                "+447911123456",
                from_number=_TWILIO_FROM,
                account_sid=_TWILIO_SID,
                auth_token=_TWILIO_AUTH,
            )


# ── Missing twilio package ────────────────────────────────────────────────────


class TestMissingTwilioPackage:
    def test_import_error_raised_with_helpful_message(self):
        """If twilio is not installed, ImportError with instructions is raised."""
        with patch(
            "fastapi_alertengine.notifications.whatsapp.TwilioClient",
            None,
        ):
            with pytest.raises(ImportError, match=r"fastapi-alertengine\[notifications\]"):
                send_whatsapp_alert(
                    "restart",
                    "svc",
                    "u1",
                    "+447911123456",
                    from_number=_TWILIO_FROM,
                    account_sid=_TWILIO_SID,
                    auth_token=_TWILIO_AUTH,
                )


# ── Twilio API errors propagate ───────────────────────────────────────────────


class TestTwilioApiErrors:
    def test_twilio_exception_propagates(self, mock_twilio_client):
        _, mock_instance = mock_twilio_client
        mock_instance.messages.create.side_effect = RuntimeError("Twilio API error")
        with patch(
            "fastapi_alertengine.notifications.whatsapp.TwilioClient",
            return_value=mock_instance,
        ):
            with pytest.raises(RuntimeError, match="Twilio API error"):
                send_whatsapp_alert(
                    "restart",
                    "svc",
                    "u1",
                    "+447911123456",
                    base_url="https://x.io",
                    from_number=_TWILIO_FROM,
                    account_sid=_TWILIO_SID,
                    auth_token=_TWILIO_AUTH,
                )
