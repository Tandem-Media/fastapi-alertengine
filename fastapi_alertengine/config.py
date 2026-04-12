# fastapi_alertengine/config.py

from typing import Optional

from pydantic_settings import BaseSettings


class AlertConfig(BaseSettings):
    """
    Configuration for AlertEngine.

    All fields can be overridden via environment variables prefixed with
    ``ALERTENGINE_`` (e.g. ``ALERTENGINE_REDIS_URL``).
    """

    model_config = {"env_prefix": "ALERTENGINE_"}

    redis_url: str = "redis://localhost:6379/0"
    stream_key: str = "anchorflow:request_metrics"
    stream_maxlen: int = 5000

    # Service identity — attached to every metric for multi-service deployments.
    service_name: str = "default"
    instance_id: str = "default"

    # Optional Slack webhook for alert delivery.
    # When set, POST /alerts/evaluate will post a message on non-ok status.
    slack_webhook_url: Optional[str] = None

    # Rate-limit Slack notifications: minimum seconds between messages.
    slack_rate_limit_seconds: int = 10

    # Aggregation settings.
    # Metrics are bucketed by this interval (seconds) in memory and then
    # flushed to Redis hashes once the bucket is complete.
    agg_bucket_seconds: int = 60
    # TTL applied to every aggregation hash key in Redis (default: 1 hour).
    agg_ttl_seconds: int = 3600
    # Key prefix used for all aggregation hashes.
    agg_key_prefix: str = "alertengine:agg"
    # How often drain() attempts to flush completed buckets to Redis (seconds).
    agg_flush_interval_seconds: int = 30