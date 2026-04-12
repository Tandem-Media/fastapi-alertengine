# fastapi_alertengine/config.py

from pydantic_settings import BaseSettings


class AlertConfig(BaseSettings):
    """
    Minimal configuration for AlertEngine.

    You can extend this later with more knobs (thresholds, streams, etc.).
    """

    model_config = {"env_prefix": "ALERTENGINE_"}

    redis_url: str = "redis://localhost:6379/0"
    stream_key: str = "anchorflow:request_metrics"
    stream_maxlen: int = 5000