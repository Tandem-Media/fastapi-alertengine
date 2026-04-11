# fastapi_alertengine/__init__.py

from .client     import get_alert_engine
from .config     import AlertConfig
from .engine     import AlertEngine
from .middleware import RequestMetricsMiddleware
from .schemas    import (
    AlertEvent,
    AlertItem,
    AlertMetrics,
    AlertThresholds,
    RequestMetricEvent,
)

__all__ = [
    # Core
    "AlertEngine",
    "RequestMetricsMiddleware",
    "get_alert_engine",
    # Config
    "AlertConfig",
    # Schemas (useful for type hints in host apps)
    "AlertEvent",
    "AlertItem",
    "AlertMetrics",
    "AlertThresholds",
    "RequestMetricEvent",
]

__version__ = "1.1.3"
