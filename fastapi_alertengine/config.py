# fastapi_alertengine/config.py
from dataclasses import dataclass, field


@dataclass
class AlertConfig:
    """
    All tunable parameters for the alert engine in one place.

    Pass a custom instance to RequestMetricsMiddleware and AlertEngine
    to override any default without subclassing.
    """

    # ── Redis Stream ──────────────────────────────────────────────────────────
    stream_key: str  = "anchorflow:request_metrics"
    stream_maxlen: int = 10_000          # approximate cap (Redis MAXLEN ~)

    # ── Latency thresholds (ms) ───────────────────────────────────────────────
    p95_warning_ms: float  = 1_000.0
    p95_critical_ms: float = 3_000.0

    # ── Anomaly thresholds (ratio vs rolling mean) ────────────────────────────
    anomaly_warning: float  = 1.0
    anomaly_critical: float = 2.0

    # ── Error-rate thresholds (fraction of 5xx responses) ────────────────────
    error_rate_warning: float  = 0.10
    error_rate_critical: float = 0.20

    # ── Evaluation window ─────────────────────────────────────────────────────
    window_size: int = 200               # events read per evaluate() call

    # ── Deduplication ─────────────────────────────────────────────────────────
    cooldown_seconds: int = 300          # min seconds between identical alerts
