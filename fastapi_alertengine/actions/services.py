# fastapi_alertengine/actions/services.py
"""
v1.6 — Infrastructure Action Executors

These functions are called ONLY after a human has authorised an action
via the JWT confirmation flow. They never auto-execute.

restart_container() is the only action wired in v1.6.
Replace the implementation with your actual orchestration layer:
  - Railway: call the Railway GraphQL API
  - Docker: docker restart <container>
  - Kubernetes: kubectl rollout restart deployment/<name>
  - Railway deploy hook: POST to RAILWAY_RESTART_URL env var

The stub below logs the action and returns a detail string.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def restart_container(service: str) -> str:
    """
    Trigger a service restart.

    In production: replace this with your orchestration call.
    The function must be async and return a detail string on success,
    or raise an Exception on failure.

    Current implementation: checks for a RAILWAY_RESTART_URL env var
    and POSTs to it if present, otherwise logs and returns a dry-run message.
    """
    restart_url = os.getenv("RAILWAY_RESTART_URL")

    if restart_url:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    restart_url,
                    headers={"Authorization": f"Bearer {os.getenv('RAILWAY_API_TOKEN', '')}"},
                    json={"service": service},
                )
                resp.raise_for_status()
                logger.info("restart_container: Railway restart triggered for %s", service)
                return f"Railway restart triggered for {service} (HTTP {resp.status_code})"
        except Exception as exc:
            logger.error("restart_container: Railway restart failed for %s: %s", service, exc)
            raise

    # No orchestration configured — dry run
    logger.warning(
        "restart_container: RAILWAY_RESTART_URL not set — dry run for service=%s", service
    )
    return f"Dry-run: restart action recorded for {service}. Set RAILWAY_RESTART_URL to enable."
