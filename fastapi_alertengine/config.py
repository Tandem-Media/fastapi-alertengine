# fastapi_alertengine/config.py

from pydantic_settings import BaseSettings


class AlertConfig(BaseSettings):
    """
    Minimal configuration for AlertEngine.

    You can extend this later with more knobs (thresholds, streams, etc.).
    """

    redis_url: str = "redis://localhost:6379/0"
    stream_key: str = "anchorflow:request_metrics"

    class Config:
        env_prefix = "ALERTENGINE_"