# fastapi_alertengine/actions/replay_store.py
"""
v1.6 — JTI Replay Protection Store

In v1.3 this was in-memory (lost on restart).
v1.6 persists consumed JTIs to Redis with TTL matching token expiry.
Falls back to in-memory set when Redis is unavailable — never crashes.
"""
import time
from typing import Optional

# In-memory fallback — used when Redis is not available
_used_jtis: set = set()

_REDIS_KEY_PREFIX = "alertengine:jti:"
_DEFAULT_TTL = 180  # 2x token TTL — safe margin


def is_token_used(jti: str, rdb=None) -> bool:
    """Return True if this JTI has already been consumed."""
    if rdb is not None:
        try:
            return bool(rdb.get(f"{_REDIS_KEY_PREFIX}{jti}"))
        except Exception:
            pass
    return jti in _used_jtis


def mark_token_used(jti: str, rdb=None, ttl: int = _DEFAULT_TTL) -> None:
    """Mark a JTI as consumed. Idempotent."""
    if rdb is not None:
        try:
            rdb.set(f"{_REDIS_KEY_PREFIX}{jti}", "1", ex=ttl)
            return
        except Exception:
            pass
    _used_jtis.add(jti)


def _reset_memory_store() -> None:
    """Test helper — resets in-memory fallback."""
    global _used_jtis
    _used_jtis = set()
