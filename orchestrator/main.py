# orchestrator/main.py
"""
Orchestrator entry point.
Validates env, starts the loop.
"""

import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("orchestrator")

REQUIRED_ENV = [
    "ALERTENGINE_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ALERT_SECRET",
]


def _validate_env() -> bool:
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        return False
    return True


async def main():
    logger.info("⚡ Orchestrator starting")

    if not _validate_env():
        sys.exit(1)

    from loop import run_loop
    await run_loop()


if __name__ == "__main__":
    asyncio.run(main())
