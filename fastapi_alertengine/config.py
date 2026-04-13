# fastapi_alertengine/config.py
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlertConfig(BaseSettings):
    """
    Configuration for AlertEngine.

    All fields can be overridden via environment variables prefixed with
    ALERTENGINE_  e.g.  ALERTENGINE_REDIS_URL=redis://redis:6379/0
    """

    model_config = SettingsConfigDict(env_prefix="ALERTENGINE_")

    # ── Redis ─────────────────────────────────────────────────────────
    redis_url:     str = "redis://localhost:6379/0"
    stream_key:    str = "anchorflow:request_metrics"
    stream_maxlen: int = 10_000

    # ── Service identity ──────────────────────────────────────────────
    service_name: str = "default"
    instance_id:  str = "default"

    # ── Latency thresholds (ms) ────────────────────────────────────────
    p95_warning_ms:  float = 1_000.0
    p95_critical_ms: float = 3_000.0

    # ── Error-rate thresholds (percent 0-100) ─────────────────────────
    error_rate_warning_pct:  float = 2.0
    error_rate_critical_pct: float = 5.0
    error_rate_baseline_pct: float = 0.5

    # ── Slack alert delivery ───────────────────────────────────────
    slack_webhook_url:        Optional[str] = None
    slack_rate_limit_seconds: int           = 10

    # ── Aggregation ───────────────────────────────────────────────
    agg_bucket_seconds:         int = 60
    agg_ttl_seconds:            int = 3_600
    agg_key_prefix:             str = "alertengine:agg"
    agg_flush_interval_seconds: int = 30
