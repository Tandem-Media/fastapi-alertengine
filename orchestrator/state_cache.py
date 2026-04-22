# orchestrator/state_cache.py
"""
Last-Known-Good State Cache

When AlertEngine is unreachable, the orchestrator doesn't go blind.
It uses the last valid health snapshot, marked as STALE, so reasoning
can continue with appropriate confidence downgrade.

Staleness rules:
- FRESH:   < 2 × poll interval
- STALE:   >= 2 × poll interval, < 10 minutes
- EXPIRED: >= 10 minutes — orchestrator idles, no action proposed
"""

import logging
import os
import time
from copy import deepcopy
from typing import Optional

logger = logging.getLogger("orchestrator.state_cache")

POLL_INTERVAL_S  = int(os.getenv("ORCHESTRATOR_POLL_S", "30"))
STALE_THRESHOLD  = POLL_INTERVAL_S * 2
EXPIRED_THRESHOLD = 600  # 10 minutes


class StateCache:

    def __init__(self):
        self._last_health:    Optional[dict] = None
        self._last_fetched_at: float         = 0.0
        self._fetch_failures:  int           = 0

    def update(self, health: dict) -> None:
        """Store a fresh health snapshot."""
        self._last_health     = deepcopy(health)
        self._last_fetched_at = time.time()
        self._fetch_failures  = 0

    def record_failure(self) -> None:
        self._fetch_failures += 1
        logger.warning(
            "AlertEngine fetch failure #%d (last good: %.0fs ago)",
            self._fetch_failures,
            time.time() - self._last_fetched_at if self._last_fetched_at else -1,
        )

    def get(self) -> tuple[Optional[dict], str]:
        """
        Return (health_dict, freshness) where freshness is:
            "fresh"   — within normal poll window
            "stale"   — AlertEngine recently unreachable, using cached state
            "expired" — cache too old to trust, orchestrator should idle
            "empty"   — no data ever received
        """
        if self._last_health is None:
            return None, "empty"

        age = time.time() - self._last_fetched_at

        if age < STALE_THRESHOLD:
            return deepcopy(self._last_health), "fresh"
        if age < EXPIRED_THRESHOLD:
            stale = deepcopy(self._last_health)
            # Inject staleness marker so Claude knows
            stale["_cache_staleness"] = "STALE"
            stale["_cache_age_s"]     = round(age, 1)
            return stale, "stale"

        return None, "expired"

    @property
    def consecutive_failures(self) -> int:
        return self._fetch_failures
