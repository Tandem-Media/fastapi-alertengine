# orchestrator/action_generator.py
"""
Action Generator

Fetches AlertEngine's signed action suggestions and matches them
to the orchestrator's proposed action. Returns the confirm URL
for human approval.

The orchestrator NEVER generates its own JWT tokens.
Tokens are always sourced from AlertEngine's /actions/suggest endpoint,
which handles signing, IP binding, and JTI assignment correctly.

Flow:
    Claude proposes action_type
    Policy permits it
    Action generator fetches matching suggestion from AlertEngine
    Returns confirm URL → human approves at /action/confirm?token=...
"""

import logging
import os
from typing import Optional

from .alertengine_client import AlertEngineClient

logger  = logging.getLogger("orchestrator.action_generator")
BASE_URL = os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000")


class ActionGenerator:

    def __init__(self, client: AlertEngineClient):
        self.client  = client
        self.base_url = BASE_URL.rstrip("/")

    async def get_confirm_url(
        self,
        action_type: str,
        user_id:     str = "orchestrator",
    ) -> Optional[dict]:
        """
        Fetch suggestions from AlertEngine and return the confirm URL
        for the requested action type.

        Returns:
            {
                "action":      "restart",
                "priority":    "CRITICAL",
                "confirm_url": "https://your-app.railway.app/action/confirm?token=eyJ...",
                "token":       "eyJ...",
                "expires_at":  1712756391,
                "suggestion_id": "uuid"
            }
        or None if no matching suggestion exists.
        """
        suggestions = await self.client.get_suggestions(user_id=user_id)
        if not suggestions:
            logger.warning("No suggestions available from AlertEngine")
            return None

        # Find the matching action
        match = next(
            (s for s in suggestions if s.get("action") == action_type),
            None,
        )

        if not match:
            logger.warning(
                "No suggestion found for action_type=%s (available: %s)",
                action_type,
                [s.get("action") for s in suggestions],
            )
            return None

        token = match.get("token")
        if not token:
            logger.warning("Suggestion has no token — ACTION_SECRET_KEY may not be set")
            return None

        confirm_url = f"{self.base_url}/action/confirm?token={token}"

        logger.info(
            "Action URL generated: action=%s priority=%s",
            match.get("action"),
            match.get("priority"),
        )

        return {
            "action":        match.get("action"),
            "priority":      match.get("priority"),
            "confirm_url":   confirm_url,
            "token":         token,
            "expires_at":    match.get("expires_at"),
            "suggestion_id": match.get("suggestion_id"),
            "reason":        match.get("reason"),
        }
