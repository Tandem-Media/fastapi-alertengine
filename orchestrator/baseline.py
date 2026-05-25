# orchestrator/baseline.py
"""
Per-tenant rolling baseline using Exponential Moving Average (EMA).

Stores one Redis key per tenant with a 24h TTL.
Low memory footprint — no per-request overhead.

Used by claude_engine.py to provide Claude with deviation context:
"P95 is 43x your normal baseline" is far more useful than "P95 is 5230ms".
"""

import json
import logging
import os
import time
from typing import Optional

import redis

logger = logging.getLogger("orchestrator.baseline")

ALPHA = float(os.getenv("BASELINE_EMA_ALPHA", "0.3"))  # 30% weight to new sample
MIN_SAMPLES = int(os.getenv("BASELINE_MIN_SAMPLES", "10"))  # min samples before reporting
BASELINE_TTL = 86400  # 24 hours


# Module-level client — reused across calls (connection pool, thread-safe)
_redis_client = None

def _redis():
    global _redis_client
    if _redis_client is None:
        url = os.getenv("REDIS_URL", os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
        _redis_client = redis.Redis.from_url(url, decode_responses=True)
    return _redis_client


def get_baseline(tenant_id: str) -> dict:
    """Return current baseline for tenant. Returns empty baseline if not found."""
    try:
        key = f"orchestrator:baseline:{tenant_id}"
        raw = _redis().get(key)
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.debug("get_baseline failed: %s", e)
    return {
        "p95_ms":       0.0,
        "error_rate":   0.0,
        "sample_count": 0,
        "updated_at":   0,
    }


def update_baseline(tenant_id: str, p95: float, err: float) -> dict:
    """
    Update EMA baseline with new sample.
    Called on every healthy poll — O(1) Redis GET + SET.
    """
    try:
        key     = f"orchestrator:baseline:{tenant_id}"
        current = get_baseline(tenant_id)

        if current["sample_count"] == 0:
            new = {
                "p95_ms":       p95,
                "error_rate":   err,
                "rpm":          rpm,
                "sample_count": 1,
                "updated_at":   time.time(),
            }
        else:
            a = ALPHA
            new = {
                "p95_ms":       current["p95_ms"] * (1 - a) + p95 * a,
                "error_rate":   current["error_rate"] * (1 - a) + err * a,
                "rpm":          current["rpm"] * (1 - a) + rpm * a,
                "sample_count": current["sample_count"] + 1,
                "updated_at":   time.time(),
            }

        _redis().setex(key, BASELINE_TTL, json.dumps(new))
        return new

    except Exception as e:
        logger.debug("update_baseline failed: %s", e)
        return {}


def baseline_context(tenant_id: str, current_p95: float, current_err: float) -> str:
    """
    Returns formatted baseline context string for the Claude prompt.
    Returns empty string if not enough samples yet.

    Example output:
        Baseline (last 24 samples):
          Normal P95: 120ms
          Normal error rate: 0.5%
          Normal RPM: 340
        Current deviation: P95 is 43.6x baseline, errors 90.0x baseline
    """
    try:
        b = get_baseline(tenant_id)
        if b["sample_count"] < MIN_SAMPLES:
            return ""

        p95_dev = current_p95 / b["p95_ms"] if b["p95_ms"] > 0 else 0
        err_dev = current_err / b["error_rate"] if b["error_rate"] > 0 else 0

        lines = [
            f"Baseline (last {b['sample_count']} samples):",
            f"  Normal P95: {b['p95_ms']:.0f}ms",
            f"  Normal error rate: {b['error_rate']*100:.1f}%",
            f"  Normal RPM: {b['rpm']:.0f}",
            f"Current deviation: P95 is {p95_dev:.1f}x baseline, errors {err_dev:.1f}x baseline",
        ]
        return "\n".join(lines)

    except Exception as e:
        logger.debug("baseline_context failed: %s", e)
        return ""


def reset_baseline(tenant_id: str) -> None:
    """Clear baseline for a tenant. Called on tenant deactivation."""
    try:
        _redis().delete(f"orchestrator:baseline:{tenant_id}")
    except Exception as e:
        logger.debug("reset_baseline failed: %s", e)
