# anchorflow/actions/replay.py
"""
In-memory replay-attack protection for AnchorFlow action tokens.

Each action token carries a unique ``jti`` (JWT ID).  Once a token is
consumed by the ``/action/restart`` endpoint its JTI is recorded here so
that any subsequent attempt to reuse the same token is rejected with HTTP
403, even while the token's ``exp`` claim is still in the future.

Design
------
Phase 1 — in-memory store (this file):
    Simple dictionary guarded by a ``threading.Lock``.  Expired entries are
    pruned lazily on every ``is_token_used`` call so the dict does not grow
    unbounded.  This is safe for single-process deployments.

Phase 2 (future):
    Replace ``_used_tokens`` with a Redis ``SET`` + ``EXPIRE`` so the store
    is shared across multiple instances.  The public interface
    (``mark_token_used`` / ``is_token_used``) stays identical; only the
    backend changes.

Thread-safety
-------------
All mutations are protected by ``_lock``.  The module is async-compatible
because the lock is held only for fast dict operations (no I/O).
"""

import time
from threading import Lock

# Set to 120 s — intentionally longer than the token TTL (90 s in tokens.py)
# so that JTI records are never evicted before the corresponding JWT could
# theoretically expire, preventing any replay window at the boundary.
TTL: int = 120  # seconds

_used_tokens: dict[str, float] = {}
_lock = Lock()


def mark_token_used(jti: str) -> None:
    """
    Record *jti* as consumed.

    Call this immediately before executing the authorised action so the
    token cannot be replayed even if the action handler raises an exception.

    Parameters
    ----------
    jti:
        The ``jti`` claim extracted from the verified JWT payload.
    """
    with _lock:
        _used_tokens[jti] = time.time()


def is_token_used(jti: str) -> bool:
    """
    Return ``True`` if *jti* has already been consumed.

    Expired entries (older than ``TTL`` seconds) are pruned during this call
    so the store remains bounded over time.

    Parameters
    ----------
    jti:
        The ``jti`` claim extracted from the verified JWT payload.
    """
    now = time.time()
    with _lock:
        # Lazy cleanup — remove entries whose original token has certainly expired.
        expired = [k for k, ts in _used_tokens.items() if now - ts > TTL]
        for k in expired:
            del _used_tokens[k]

        return jti in _used_tokens


def _reset() -> None:
    """
    Clear the store.  Intended for use in tests only.
    """
    with _lock:
        _used_tokens.clear()
