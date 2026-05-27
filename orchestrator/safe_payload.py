# orchestrator/safe_payload.py
"""
Safe payload extraction utilities for runtime schema drift handling.

The orchestrator consumes health payloads from multiple sources:
  - FastAPI SDK /health/alerts endpoint
  - Redis state (incidents, tenants, baselines)
  - Claude tool use responses
  - Recovery executor callbacks

These sources WILL drift over time:
  - p95_ms becomes p95
  - error_rate becomes None
  - malformed health payload from degraded SDK
  - partial Redis writes
  - stale orchestrator schema

Rules:
  - Never raise KeyError, TypeError, or ValueError from payload access
  - Always return a safe default on any extraction failure
  - Log schema mismatches at WARNING level for observability
  - Never crash incident evaluation due to a bad payload

Usage:
    from safe_payload import safe_float, safe_int, safe_str, extract_health

    p95   = safe_float(metrics.get("overall_p95_ms"))
    err   = safe_float(metrics.get("error_rate"))
    score = safe_int(health_score.get("score"), default=100)

    # Or use the high-level extractor:
    hs = extract_health(raw_health_payload)
    p95   = hs.p95_ms
    err   = hs.error_rate
    score = hs.score
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("orchestrator.safe_payload")

# ── Internal fields that must never appear in outward-facing surfaces ──────────
# WhatsApp payloads, audit APIs, recovery summaries, public webhooks
# must strip these before sending.

INTERNAL_FIELDS = frozenset({
    "recovery_webhook",
    "internal_trace",
    "redis_key",
    "claude_prompt",
    "approval_token",
    "baseline_key",
    "diagnosis_history_key",
    "tenant_secret",
    "alert_secret",
    "twilio_auth_token",
    "sent_api_key",
})


# ── Primitive extractors ───────────────────────────────────────────────────────

def safe_float(value: Any, default: float = 0.0, field_name: str = "") -> float:
    """
    Extract a float from an untrusted value.
    Returns default on None, non-numeric, or conversion failure.
    Logs a warning if the value is present but not numeric.
    """
    if value is None:
        return default
    try:
        result = float(value)
        if result != result:  # NaN check
            logger.warning("NaN value for field '%s' — using default %.1f", field_name, default)
            return default
        return result
    except (TypeError, ValueError):
        if field_name:
            logger.warning(
                "Schema drift: field '%s' expected float, got %s (%r) — using default %.1f",
                field_name, type(value).__name__, value, default,
            )
        return default


def safe_int(value: Any, default: int = 0, field_name: str = "") -> int:
    """Extract an int from an untrusted value. Returns default on failure."""
    if value is None:
        return default
    try:
        return int(float(value))  # handles "23.0" → 23
    except (TypeError, ValueError):
        if field_name:
            logger.warning(
                "Schema drift: field '%s' expected int, got %s (%r) — using default %d",
                field_name, type(value).__name__, value, default,
            )
        return default


def safe_str(value: Any, default: str = "", field_name: str = "") -> str:
    """Extract a string from an untrusted value. Returns default on None."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return default


def safe_dict(value: Any, default: Optional[dict] = None) -> dict:
    """Extract a dict from an untrusted value. Returns empty dict on failure."""
    if isinstance(value, dict):
        return value
    return default if default is not None else {}


def strip_internal_fields(payload: dict) -> dict:
    """
    Remove internal fields from a dict before sending to external surfaces.
    Safe to call on any dict — never raises.
    """
    return {k: v for k, v in payload.items() if k not in INTERNAL_FIELDS}


# ── High-level health payload extractor ───────────────────────────────────────

@dataclass
class HealthSnapshot:
    """
    Safe, typed snapshot of a /health/alerts payload.
    All fields have safe defaults — never raises on malformed input.
    """
    status:       str   = "healthy"
    score:        int   = 100
    trend:        str   = "stable"
    p95_ms:       float = 0.0
    error_rate:   float = 0.0
    anomaly_score: float = 0.0
    sample_size:  int   = 0
    alerts:       list  = field(default_factory=list)
    is_degraded:  bool  = False

    @property
    def is_critical(self) -> bool:
        return self.status == "critical" or self.score < 40

    @property
    def deviation_summary(self) -> str:
        """One-line summary for logging."""
        return (
            f"status={self.status} score={self.score} "
            f"p95={self.p95_ms:.0f}ms err={self.error_rate*100:.1f}%"
        )


def extract_health(raw: Any) -> HealthSnapshot:
    """
    Safely extract a HealthSnapshot from a raw /health/alerts payload.

    Handles:
    - None payload
    - Missing keys
    - Wrong types (p95_ms as string, error_rate as None)
    - Partial payloads from degraded SDK
    - Unexpected payload shapes

    Never raises. Always returns a usable HealthSnapshot.

    Example:
        raw = await fetch_health(tenant.health_url)
        hs  = extract_health(raw)
        if hs.is_critical:
            ...
    """
    if not isinstance(raw, dict):
        logger.warning(
            "Health payload is not a dict (got %s) — using safe defaults",
            type(raw).__name__,
        )
        return HealthSnapshot()

    # Extract health_score block
    hs_block = safe_dict(raw.get("health_score"))
    status   = safe_str(hs_block.get("status"), default="healthy", field_name="health_score.status")
    score    = safe_int(hs_block.get("score"),  default=100,       field_name="health_score.score")
    trend    = safe_str(hs_block.get("trend"),  default="stable",  field_name="health_score.trend")

    # Clamp score to valid range
    score = max(0, min(100, score))

    # Extract metrics block — handle both naming conventions
    m_block = safe_dict(raw.get("metrics"))
    p95_ms  = safe_float(
        m_block.get("overall_p95_ms") or m_block.get("p95_ms") or m_block.get("p95"),
        default=0.0,
        field_name="metrics.overall_p95_ms",
    )
    error_rate = safe_float(
        m_block.get("error_rate"),
        default=0.0,
        field_name="metrics.error_rate",
    )
    anomaly_score = safe_float(
        m_block.get("anomaly_score"),
        default=0.0,
        field_name="metrics.anomaly_score",
    )
    sample_size = safe_int(
        m_block.get("sample_size"),
        default=0,
        field_name="metrics.sample_size",
    )

    # Extract alerts list
    alerts_raw = raw.get("alerts", [])
    alerts = alerts_raw if isinstance(alerts_raw, list) else []

    return HealthSnapshot(
        status=status,
        score=score,
        trend=trend,
        p95_ms=p95_ms,
        error_rate=error_rate,
        anomaly_score=anomaly_score,
        sample_size=sample_size,
        alerts=alerts,
        is_degraded=(status == "degraded"),
    )
