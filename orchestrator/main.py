# orchestrator/main.py
"""
AnchorFlow AI Orchestrator — Production Entry Point

Standalone Railway service. Polls AlertEngine, reasons with Claude,
enforces policy, generates action tokens, and logs everything.

Environment variables required:
    ALERTENGINE_BASE_URL     Base URL of the AlertEngine service
    ANTHROPIC_API_KEY        Claude API key
    ACTION_SECRET_KEY        Shared secret for JWT action tokens
    ORCHESTRATOR_POLL_S      Poll interval in seconds (default: 30)
    ORCHESTRATOR_MIN_SCORE   Health score below which orchestrator activates (default: 75)

Optional:
    ALERTENGINE_SERVICE_NAME Service name to pass to /actions/suggest
    LOG_LEVEL                Logging level (default: INFO)
"""

import asyncio
import logging
import os
import sys

from .loop import OrchestratorLoop

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger("orchestrator")


async def main():
    logger.info("⚡ AnchorFlow AI Orchestrator starting")

    required = ["ALERTENGINE_BASE_URL", "ANTHROPIC_API_KEY", "ACTION_SECRET_KEY"]
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error("Missing required environment variables: %s", missing)
        sys.exit(1)

    loop = OrchestratorLoop()
    await loop.run()


if __name__ == "__main__":
    asyncio.run(main())
