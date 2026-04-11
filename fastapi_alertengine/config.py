# fastapi_alertengine/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlertConfig(BaseSettings):
    """
    Full configuration for AlertEngine and the request-metrics pipeline.

    Every field can be overridden via an environment variable prefixed with
    ALERTENGINE_  e.g.  ALERTENGINE_REDIS_URL=redis://redis:6379/0

    Error-rate thresholds are in PERCENT (0-100) to match the advertised
    JSON output field  error_rate_percent.
    """

    # ── Redis connection ──────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Stream settings ───────────────────────────────────────────────────
    stream_key:    str = "anchorflow:request_metrics"
    stream_maxlen: int = 10_000

    # ── Latency thresholds (milliseconds) ─────────────────────────────────
    p95_warning_ms:  float = 1_000.0
    p95_critical_ms: float = 3_000.0

    # ── Error-rate thresholds (PERCENT) ───────────────────────────────────
    error_rate_warning_pct:  float = 2.0
    error_rate_critical_pct: float = 5.0

    # ── Baseline shown in alert messages ──────────────────────────────────
    error_rate_baseline_pct: float = 0.5

    model_config = SettingsConfigDict(env_prefix="ALERTENGINE_")
