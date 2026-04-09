# fastapi_alertengine/__init__.py

from .engine import AlertEngine           # or from .alert_engine import AlertEngine
from .middleware import RequestMetricsMiddleware
from .client import get_alert_engine

__all__ = ["AlertEngine", "RequestMetricsMiddleware", "get_alert_engine"]

__version__ = "1.1.0"
