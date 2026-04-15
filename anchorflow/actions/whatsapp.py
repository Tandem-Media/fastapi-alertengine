# anchorflow/actions/whatsapp.py
"""
WhatsApp integration hook for AnchorFlow remote actions.

This module builds signed action URLs that can be embedded in WhatsApp
template messages.  When the recipient taps the link, the JWT in the URL
is verified by the ``/action/restart`` endpoint before any infrastructure
change occurs.

Typical flow
------------
1.  An operator or automated system calls ``build_action_message``.
2.  The function generates a short-lived JWT and constructs a signed URL.
3.  The signed URL is sent to the target user via their preferred WhatsApp
    Business API client (Twilio, 360dialog, Meta Cloud API, etc.).
4.  The user taps the link; the FastAPI endpoint verifies the token and
    executes the action.

Configuration
-------------
``BASE_URL``
    The public base URL of this AnchorFlow instance, e.g.
    ``https://anchorflow.example.com``.  Read from the ``BASE_URL``
    environment variable.

``ACTION_SECRET_KEY``
    JWT signing secret (shared with ``tokens.py``).
"""

import os
from dataclasses import dataclass

from anchorflow.actions.tokens import generate_action_token


@dataclass(frozen=True)
class ActionMessage:
    """Encapsulates everything needed to deliver an action via WhatsApp."""

    token: str
    """The raw JWT action token."""

    signed_url: str
    """
    Fully-qualified URL that the recipient can tap to trigger the action.
    Embed this in a WhatsApp template message body or button URL.
    """

    body: str
    """
    Plain-text message body suitable for a WhatsApp template.
    Customise the template wording to match your WhatsApp Business approval.
    """


def build_action_message(
    action: str,
    service: str,
    user_id: str,
    *,
    base_url: str | None = None,
) -> ActionMessage:
    """
    Generate a signed action URL and compose a WhatsApp message payload.

    Parameters
    ----------
    action:
        The infrastructure action (e.g. ``"restart"``).
    service:
        The target service / container name.
    user_id:
        Identifier of the user who will receive and act on the message.
    base_url:
        Override for the public base URL (falls back to the ``BASE_URL``
        environment variable, then ``http://localhost:8000``).

    Returns
    -------
    ActionMessage
        An immutable dataclass containing the JWT, signed URL, and message
        body ready to pass to your WhatsApp API client.

    Example
    -------
    ::

        msg = build_action_message("restart", "payments-api", "user-42")
        # Pass msg.signed_url to your WhatsApp Business API call:
        whatsapp_client.send_template(
            to="+1234567890",
            template="anchorflow_action",
            params={"url": msg.signed_url, "service": "payments-api"},
        )
    """
    resolved_base = (
        base_url
        or os.getenv("BASE_URL", "http://localhost:8000")
    ).rstrip("/")

    token = generate_action_token(action, service, user_id)
    signed_url = f"{resolved_base}/action/{action}?token={token}"

    body = (
        f"AnchorFlow alert: action *{action}* requested for service *{service}*.\n"
        f"Tap the link below to confirm (link expires in 90 seconds):\n{signed_url}"
    )

    return ActionMessage(token=token, signed_url=signed_url, body=body)
