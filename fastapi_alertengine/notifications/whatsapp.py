# fastapi_alertengine/notifications/whatsapp.py
"""
Production-ready WhatsApp notification backend for fastapi-alertengine.

Sends alert notifications to operators via WhatsApp using the Twilio Messaging
API, embedding a signed action URL so the recipient can approve infrastructure
actions directly from the message.

Typical flow
------------
1.  An alert fires and your alert handler calls ``send_whatsapp_alert``.
2.  This module generates a short-lived JWT via ``generate_action_token``,
    builds a signed confirmation URL (``{BASE_URL}/action/confirm?token=…``),
    composes a message body, and delivers it through the Twilio WhatsApp
    sandbox or Business API.
3.  The recipient taps the link, reviews the action on the confirmation page,
    and clicks "Confirm" to execute it — protected by JWT expiry and replay
    prevention.

Configuration (environment variables)
--------------------------------------
``TWILIO_ACCOUNT_SID``
    Twilio Account SID (starts with ``AC``).

``TWILIO_AUTH_TOKEN``
    Twilio Auth Token paired with the above SID.

``TWILIO_FROM_NUMBER``
    The sender WhatsApp number in E.164 format prefixed with ``whatsapp:``,
    e.g. ``whatsapp:+14155238886`` (Twilio sandbox) or your approved number.

``BASE_URL``
    The public base URL of this fastapi-alertengine instance,
    e.g. ``https://alertengine.example.com``.

``ACTION_SECRET_KEY``
    JWT signing secret shared with ``fastapi_alertengine.actions.tokens``.

Install the optional dependency
--------------------------------
::

    pip install "fastapi-alertengine[notifications]"

or directly::

    pip install twilio>=9.10.5
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from fastapi_alertengine.actions.tokens import _TOKEN_TTL_SECONDS, generate_action_token

logger = logging.getLogger(__name__)

# Guard the optional twilio import so the module is importable even when the
# package is absent.  ``send_whatsapp_alert`` raises ``ImportError`` at call
# time when twilio is not installed, giving a clear installation instruction.
try:
    from twilio.rest import Client as TwilioClient  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    TwilioClient = None  # type: ignore[assignment,misc]

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "http://localhost:8000"


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WhatsAppNotificationResult:
    """Outcome of a ``send_whatsapp_alert`` call."""

    message_sid: str
    """Twilio message SID returned on success (e.g. ``SM…``)."""

    to: str
    """Recipient number in ``whatsapp:+…`` format."""

    signed_url: str
    """The action confirmation URL embedded in the message."""

    body: str
    """The message body that was delivered."""


# ── Internal helpers ──────────────────────────────────────────────────────────


def _require_env(name: str) -> str:
    """Return the value of *name*, raising ``RuntimeError`` when absent."""
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(
            f"Environment variable {name!r} is required but not set. "
            "Configure it before calling send_whatsapp_alert()."
        )
    return value


def _build_signed_url(action: str, service: str, user_id: str, base_url: str) -> tuple[str, str]:
    """Return ``(token, signed_url)``."""
    token = generate_action_token(action, service, user_id)
    url = f"{base_url.rstrip('/')}/action/confirm?token={token}"
    return token, url


def _compose_body(action: str, service: str, signed_url: str) -> str:
    return (
        f"🚨 Alert: action *{action}* requested for service *{service}*.\n"
        f"Tap the link below to review and confirm (link expires in {_TOKEN_TTL_SECONDS} seconds):\n"
        f"{signed_url}"
    )


# ── Public API ────────────────────────────────────────────────────────────────


def send_whatsapp_alert(
    action: str,
    service: str,
    user_id: str,
    to: str,
    *,
    base_url: str | None = None,
    from_number: str | None = None,
    account_sid: str | None = None,
    auth_token: str | None = None,
) -> WhatsAppNotificationResult:
    """
    Send a WhatsApp alert message via Twilio and return the delivery result.

    Parameters
    ----------
    action:
        The infrastructure action being requested (e.g. ``"restart"``).
    service:
        The target service or container name (e.g. ``"payments-api"``).
    user_id:
        Identifier of the user who will receive and act on the message.
        Used as the ``user_id`` claim in the JWT.
    to:
        Recipient WhatsApp number in E.164 format, with or without the
        ``whatsapp:`` prefix (e.g. ``"+447911123456"`` or
        ``"whatsapp:+447911123456"``).
    base_url:
        Override for the public base URL.  Falls back to the ``BASE_URL``
        environment variable, then ``http://localhost:8000``.
    from_number:
        Override for the sender number.  Falls back to the
        ``TWILIO_FROM_NUMBER`` environment variable.
    account_sid:
        Override for the Twilio Account SID.  Falls back to
        ``TWILIO_ACCOUNT_SID``.
    auth_token:
        Override for the Twilio Auth Token.  Falls back to
        ``TWILIO_AUTH_TOKEN``.

    Returns
    -------
    WhatsAppNotificationResult
        Contains the Twilio ``message_sid``, recipient number, signed URL,
        and message body on success.

    Raises
    ------
    RuntimeError
        When a required environment variable (``TWILIO_ACCOUNT_SID``,
        ``TWILIO_AUTH_TOKEN``, ``TWILIO_FROM_NUMBER``, or
        ``ACTION_SECRET_KEY``) is absent.
    ImportError
        When the ``twilio`` package is not installed.  Install it with
        ``pip install "fastapi-alertengine[notifications]"``.
    twilio.base.exceptions.TwilioRestException
        On API-level errors returned by Twilio (e.g. invalid number,
        insufficient permissions).
    """
    if TwilioClient is None:
        raise ImportError(
            "The 'twilio' package is required for WhatsApp notifications. "
            "Install it with: pip install \"fastapi-alertengine[notifications]\""
        )

    # ── Resolve credentials ───────────────────────────────────────────────────
    resolved_sid = account_sid or _require_env("TWILIO_ACCOUNT_SID")
    resolved_auth = auth_token or _require_env("TWILIO_AUTH_TOKEN")
    resolved_from = from_number or _require_env("TWILIO_FROM_NUMBER")
    resolved_base = base_url or os.getenv("BASE_URL", _DEFAULT_BASE_URL)

    # Normalise recipient number to whatsapp: prefix
    resolved_to = to if to.startswith("whatsapp:") else f"whatsapp:{to}"

    # Ensure sender also has whatsapp: prefix
    if not resolved_from.startswith("whatsapp:"):
        resolved_from = f"whatsapp:{resolved_from}"

    # ── Build signed action URL ───────────────────────────────────────────────
    _token, signed_url = _build_signed_url(action, service, user_id, resolved_base)

    # ── Compose message ───────────────────────────────────────────────────────
    body = _compose_body(action, service, signed_url)

    # ── Send via Twilio ───────────────────────────────────────────────────────
    client = TwilioClient(resolved_sid, resolved_auth)
    message = client.messages.create(
        body=body,
        from_=resolved_from,
        to=resolved_to,
    )

    logger.info(
        "WhatsApp alert sent: sid=%s to=%s action=%s service=%s",
        message.sid,
        resolved_to,
        action,
        service,
    )

    return WhatsAppNotificationResult(
        message_sid=message.sid,
        to=resolved_to,
        signed_url=signed_url,
        body=body,
    )
