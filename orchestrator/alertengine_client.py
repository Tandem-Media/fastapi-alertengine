# orchestrator/alertengine_client.py
"""
AlertEngine Client

Pulls /health/alerts and /incidents/timeline from the AlertEngine service.
Never raises — returns None on failure so the orchestrator loop can continue.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("orchestrator.alertengine")

BASE_URL     = os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000")
TIMEOUT_S    = float(os.getenv("ALERTENGINE_TIMEOUT_S", "10"))


class AlertEngineClient:

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")

    async def get_health(self) -> Optional[dict]:
        """
        Fetch current system health from /health/alerts.
        Returns the full JSON payload or None on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.get(f"{self.base_url}/health/alerts")
                r.raise_for_status()
                data = r.json()
                logger.debug("Health fetched: score=%s status=%s",
                             data.get("health_score", {}).get("score"),
                             data.get("status"))
                return data
        except Exception as exc:
            logger.warning("Failed to fetch health: %s", exc)
            return None

    async def get_timeline(self, limit: int = 20) -> Optional[list]:
        """
        Fetch recent incident events from /incidents/timeline.
        Returns list of events or None on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.get(
                    f"{self.base_url}/incidents/timeline",
                    params={"limit": limit},
                )
                r.raise_for_status()
                return r.json().get("events", [])
        except Exception as exc:
            logger.warning("Failed to fetch timeline: %s", exc)
            return None

    async def get_suggestions(
        self,
        user_id: str = "orchestrator",
    ) -> Optional[list]:
        """
        Fetch current action suggestions from /actions/suggest.
        Returns list of suggestions or None on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.get(
                    f"{self.base_url}/actions/suggest",
                    params={"user_id": user_id},
                )
                r.raise_for_status()
                return r.json().get("suggestions", [])
        except Exception as exc:
            logger.warning("Failed to fetch suggestions: %s", exc)
            return None

    async def get_pipeline_status(self) -> Optional[dict]:
        """Fetch current pipeline status from /__alertengine/status."""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.get(f"{self.base_url}/__alertengine/status")
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.warning("Failed to fetch pipeline status: %s", exc)
            return None
