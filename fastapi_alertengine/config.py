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