# orchestrator/degraded.py
"""
System degradation mode manager.

Modes:
- NORMAL:     full orchestration
- DEGRADED:   skip escalations, continue detection + validation
- EMERGENCY:  read-only, no state mutations

Transitions are automatic based on observed failure rates.
"""

import logging
import os
import time
from typing import Literal

logger = logging.getLogger("orchestrator.degraded")

Mode = Literal["NORMAL", "DEGRADED", "EMERGENCY"]

_STATE = {
    "mode":            "NORMAL",
    "entered_at":      0.0,
    "redis_failures":  0,
    "notify_failures": 0,
    "last_reset":      time.time(),
}

# Thresholds
REDIS_FAILURE_THRESHOLD  = int(os.getenv("REDIS_FAILURE_THRESHOLD",  "3"))
NOTIFY_FAILURE_THRESHOLD = int(os.getenv("NOTIFY_FAILURE_THRESHOLD", "5"))
DEGRADED_RESET_S         = int(os.getenv("DEGRADED_RESET_S",         "120"))


def current_mode() -> Mode:
    return _STATE["mode"]


def is_normal() -> bool:
    return _STATE["mode"] == "NORMAL"


def is_degraded() -> bool:
    return _STATE["mode"] == "DEGRADED"


def is_emergency() -> bool:
    return _STATE["mode"] == "EMERGENCY"


def record_redis_failure() -> None:
    _STATE["redis_failures"] += 1
    _check_thresholds()


def record_notify_failure() -> None:
    _STATE["notify_failures"] += 1
    _check_thresholds()


def record_success() -> None:
    """Reset failure counters on successful operation."""
    now = time.time()
    if now - _STATE["last_reset"] > DEGRADED_RESET_S:
        if _STATE["mode"] != "NORMAL":
            _enter_mode("NORMAL")
        _STATE["redis_failures"]  = 0
        _STATE["notify_failures"] = 0
        _STATE["last_reset"]      = now


def _check_thresholds() -> None:
    if _STATE["redis_failures"] >= REDIS_FAILURE_THRESHOLD:
        _enter_mode("EMERGENCY")
    elif _STATE["notify_failures"] >= NOTIFY_FAILURE_THRESHOLD:
        _enter_mode("DEGRADED")


def _enter_mode(mode: Mode) -> None:
    if _STATE["mode"] == mode:
        return
    prev = _STATE["mode"]
    _STATE["mode"]       = mode
    _STATE["entered_at"] = time.time()

    if mode == "NORMAL":
        logger.info("🟢 System mode: NORMAL (recovered from %s)", prev)
    elif mode == "DEGRADED":
        logger.warning("🟡 System mode: DEGRADED — escalations suppressed")
    elif mode == "EMERGENCY":
        logger.critical("🔴 System mode: EMERGENCY — read-only, no state mutations")


def can_mutate_state() -> bool:
    """Returns False in EMERGENCY mode — no Redis writes allowed."""
    return _STATE["mode"] != "EMERGENCY"


def can_escalate() -> bool:
    """Returns False in DEGRADED or EMERGENCY mode."""
    return _STATE["mode"] == "NORMAL"


def can_send_notifications() -> bool:
    """Returns False only in EMERGENCY mode."""
    return _STATE["mode"] != "EMERGENCY"


def status() -> dict:
    return {
        "mode":            _STATE["mode"],
        "entered_at":      _STATE["entered_at"],
        "redis_failures":  _STATE["redis_failures"],
        "notify_failures": _STATE["notify_failures"],
    }
