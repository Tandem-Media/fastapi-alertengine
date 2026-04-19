# fastapi_alertengine/config.py
"""
v1.5 AlertConfig — Adaptive Intelligence Layer

New fields (all have safe defaults — fully backward compatible):
- baseline_learning_mode, baseline_learning_min_samples, baseline_learning_window
- baseline_warning_multiplier, baseline_critical_multiplier
- health_score_p95_weight, health_score_error_weight, health_score_anomaly_weight
- roc_enabled, roc_window_size, roc_latency_spike_pct, roc_error_spike_pct
"""
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlertConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALERTENGINE_")

    # Redis
    redis_url:     str = "redis://localhost:6379/0"
    stream_key:    str = "anchorflow:request_metrics"
    stream_maxlen: int = 10_000

    # Service identity
    service_name: str = "default"
    instance_id:  str = "default"

    # Static latency thresholds (ms) — used when learning mode is off
    p95_warning_ms:  float = 1_000.0
    p95_critical_ms: float = 3_000.0

    # Error-rate thresholds (percent 0-100)
    error_rate_warning_pct:  float = 2.0
    error_rate_critical_pct: float = 5.0
    error_rate_baseline_pct: float = 0.5

    # Slack alert delivery
    slack_webhook_url:        Optional[str] = None
    slack_rate_limit_seconds: int           = 10

    # Aggregation
    agg_bucket_seconds:         int = 60
    agg_ttl_seconds:            int = 3_600
    agg_key_prefix:             str = "alertengine:agg"
    agg_flush_interval_seconds: int = 30

    # v1.4: Circuit breaker
    circuit_breaker_threshold:  int   = 3
    circuit_breaker_cooldown_s: float = 30.0
    memory_buffer_maxlen:       int   = 500

    # v1.4: Event enrichment
    capture_route_template: bool = True
    capture_trace_id:       bool = True

    # v1.4: Baseline preparation
    baseline_preparation_mode:    bool = False
    baseline_snapshot_interval_s: int  = 60
    baseline_max_snapshots:       int  = 1_440

    # v1.5: Baseline learning mode
    # Derives dynamic thresholds from collected snapshots.
    # Static thresholds act as a floor — learned values never go below them.
    baseline_learning_mode:        bool  = False
    baseline_learning_min_samples: int   = 10
    baseline_learning_window:      int   = 60
    baseline_warning_multiplier:   float = 1.5
    baseline_critical_multiplier:  float = 2.0

    # v1.5: Health Score Engine weights (should sum to 1.0)
    health_score_p95_weight:     float = 0.50
    health_score_error_weight:   float = 0.30
    health_score_anomaly_weight: float = 0.20

    # v1.5: Rate-of-change detection
    roc_enabled:            bool  = True
    roc_window_size:        int   = 5
    roc_latency_spike_pct:  float = 50.0
    roc_error_spike_pct:    float = 100.0

    # ── v1.5: Adaptive Intelligence Layer ─────────────────────────────────────
    # Enable baseline learning — derives dynamic thresholds from snapshots.
    # Requires baseline_preparation_mode=True (v1.4) to have collected data.
    baseline_learning_mode: bool = False

    # Minimum snapshots before adaptive thresholds become active.
    # Below this count, static thresholds are used regardless.
    baseline_min_snapshots: int = 10

    # Multipliers applied to median_p95 to derive adaptive thresholds.
    baseline_warning_multiplier:  float = 1.5
    baseline_critical_multiplier: float = 2.0

    # How often (seconds) to recalibrate adaptive thresholds from snapshots.
    baseline_recalibrate_interval_s: int = 300   # 5 minutes

    # Health score weights — must sum to 1.0 (enforced at runtime).
    # latency contributes most (p95 is the primary signal).
    health_weight_latency:  float = 0.50
    health_weight_errors:   float = 0.30
    health_weight_anomaly:  float = 0.20

    # Thresholds for the composite health score.
    health_degraded_threshold:  float = 70.0   # below this → "degraded"
    health_critical_threshold:  float = 40.0   # below this → "critical"

    # Rate-of-change detection — minimum delta to trigger a spike alert.
    # Set to 0 to disable rate-of-change alerts.
    roc_latency_spike_pct:    float = 100.0   # 100% = doubled vs prior window
    roc_error_rate_spike_pct: float = 200.0   # 200% = tripled vs prior window

    # Minimum prior value below which rate-of-change alerts are suppressed.
    # Prevents false positives from tiny absolute values (e.g. 1ms → 3ms).
    roc_min_prior_latency_ms: float = 100.0
    roc_min_prior_error_rate: float = 0.005   # 0.5%

    # How many prior evaluations to retain for trend analysis.
    evaluation_history_size: int = 10
