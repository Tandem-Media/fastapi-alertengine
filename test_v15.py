# tests/test_v15.py
"""
v1.5 test suite — Adaptive Intelligence Layer

Covers:
1.  BaselineCalibrator — compute_baseline_summary
2.  BaselineCalibrator — calibrate_thresholds (active/inactive, confidence)
3.  HealthScoreEngine  — _score_latency, _score_errors, _score_anomaly
4.  HealthScoreEngine  — compute_health_score (weights, adaptive, trend)
5.  HealthScoreEngine  — _determine_trend (improving / degrading / stable)
6.  RateOfChangeDetector — latency spike detection
7.  RateOfChangeDetector — error rate spike detection
8.  RateOfChangeDetector — suppression rules (min prior values)
9.  RateOfChangeDetector — disabled when threshold = 0
10. Alert enrichment   — enrich_alert fields
11. evaluate()         — health_score key present and structured
12. evaluate()         — adaptive_thresholds key present
13. evaluate()         — rate_of_change key present
14. evaluate()         — RoC alert fires when threshold not crossed
15. evaluate()         — adaptive thresholds override static when active
16. evaluate()         — triggered_by field on alerts
17. evaluate()         — backward compat: type/message/severity still present
18. _maybe_recalibrate — only fires when learning mode enabled
19. _maybe_recalibrate — respects recalibrate_interval
20. get_ingestion_stats — v1.5 keys present
"""

import collections
import math
import time
from typing import List
from unittest.mock import MagicMock, patch
import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_config(**kw):
    from fastapi_alertengine.config import AlertConfig
    defaults = dict(
        service_name                 = "test-svc",
        instance_id                  = "i1",
        p95_warning_ms               = 1_000.0,
        p95_critical_ms              = 3_000.0,
        error_rate_warning_pct       = 2.0,
        error_rate_critical_pct      = 5.0,
        error_rate_baseline_pct      = 0.5,
        circuit_breaker_threshold    = 3,
        circuit_breaker_cooldown_s   = 30.0,
        memory_buffer_maxlen         = 10,
        baseline_preparation_mode    = True,
        baseline_learning_mode       = True,
        baseline_min_snapshots       = 5,
        baseline_snapshot_interval_s = 60,
        baseline_max_snapshots       = 100,
        baseline_warning_multiplier  = 1.5,
        baseline_critical_multiplier = 2.0,
        baseline_recalibrate_interval_s = 300,
        health_weight_latency        = 0.50,
        health_weight_errors         = 0.30,
        health_weight_anomaly        = 0.20,
        health_degraded_threshold    = 70.0,
        health_critical_threshold    = 40.0,
        roc_latency_spike_pct        = 100.0,
        roc_error_rate_spike_pct     = 200.0,
        roc_min_prior_latency_ms     = 100.0,
        roc_min_prior_error_rate     = 0.005,
        evaluation_history_size      = 10,
        capture_route_template       = True,
        capture_trace_id             = True,
    )
    defaults.update(kw)
    return AlertConfig(**defaults)


def make_engine(config=None):
    from fastapi_alertengine.engine import AlertEngine, _NullRedis
    return AlertEngine(redis=_NullRedis(), config=config or make_config())


def make_snapshots(n: int, p95_ms: float = 300.0, err: float = 0.01) -> List[dict]:
    """Build a list of synthetic baseline snapshot dicts."""
    base = time.time() - n * 60
    return [
        {"p95_ms": p95_ms, "p50_ms": p95_ms * 0.5, "mean_ms": p95_ms * 0.6,
         "error_rate": err, "sample_size": 100, "status": "ok",
         "timestamp": base + i * 60}
        for i in range(n)
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 1-2. Baseline Calibrator
# ══════════════════════════════════════════════════════════════════════════════

class TestBaselineCalibrator:

    def test_summary_returns_none_for_empty_snapshots(self):
        from fastapi_alertengine.intelligence import compute_baseline_summary
        assert compute_baseline_summary([], "svc") is None

    def test_summary_returns_none_when_all_p95_zero(self):
        from fastapi_alertengine.intelligence import compute_baseline_summary
        snaps = [{"p95_ms": 0, "error_rate": 0.01} for _ in range(5)]
        assert compute_baseline_summary(snaps, "svc") is None

    def test_summary_median_p95_correct(self):
        from fastapi_alertengine.intelligence import compute_baseline_summary
        snaps = make_snapshots(10, p95_ms=400.0)
        summary = compute_baseline_summary(snaps, "svc")
        assert summary is not None
        assert summary.median_p95_ms == pytest.approx(400.0, abs=1.0)

    def test_summary_confidence_low_under_10(self):
        from fastapi_alertengine.intelligence import compute_baseline_summary
        snaps = make_snapshots(5, p95_ms=300.0)
        summary = compute_baseline_summary(snaps, "svc")
        assert summary.confidence == "low"

    def test_summary_confidence_medium_10_to_59(self):
        from fastapi_alertengine.intelligence import compute_baseline_summary
        snaps = make_snapshots(30, p95_ms=300.0)
        summary = compute_baseline_summary(snaps, "svc")
        assert summary.confidence == "medium"

    def test_summary_confidence_high_60_plus(self):
        from fastapi_alertengine.intelligence import compute_baseline_summary
        snaps = make_snapshots(60, p95_ms=300.0)
        summary = compute_baseline_summary(snaps, "svc")
        assert summary.confidence == "high"

    def test_calibrate_derives_correct_thresholds(self):
        from fastapi_alertengine.intelligence import (
            compute_baseline_summary, calibrate_thresholds
        )
        config  = make_config(baseline_warning_multiplier=1.5,
                              baseline_critical_multiplier=2.0)
        snaps   = make_snapshots(20, p95_ms=400.0)
        summary = compute_baseline_summary(snaps, "svc")
        thresholds = calibrate_thresholds(summary, config)
        assert thresholds.warning_ms  == pytest.approx(600.0, abs=1.0)
        assert thresholds.critical_ms == pytest.approx(800.0, abs=1.0)

    def test_calibrate_inactive_when_too_few_snapshots(self):
        from fastapi_alertengine.intelligence import (
            compute_baseline_summary, calibrate_thresholds
        )
        config  = make_config(baseline_min_snapshots=10)
        snaps   = make_snapshots(5, p95_ms=300.0)   # below min
        summary = compute_baseline_summary(snaps, "svc")
        thresholds = calibrate_thresholds(summary, config)
        assert thresholds.active is False

    def test_calibrate_active_when_enough_snapshots(self):
        from fastapi_alertengine.intelligence import (
            compute_baseline_summary, calibrate_thresholds
        )
        config  = make_config(baseline_min_snapshots=5)
        snaps   = make_snapshots(20, p95_ms=300.0)
        summary = compute_baseline_summary(snaps, "svc")
        thresholds = calibrate_thresholds(summary, config)
        assert thresholds.active is True

    def test_calibrate_as_dict_has_required_keys(self):
        from fastapi_alertengine.intelligence import (
            compute_baseline_summary, calibrate_thresholds
        )
        config  = make_config()
        snaps   = make_snapshots(20)
        summary = compute_baseline_summary(snaps, "svc")
        d       = calibrate_thresholds(summary, config).as_dict()
        for key in ("warning_ms", "critical_ms", "median_p95_ms",
                    "calibrated_from", "confidence", "active", "computed_at"):
            assert key in d, f"Missing key: {key}"


# ══════════════════════════════════════════════════════════════════════════════
# 3-5. Health Score Engine
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthScoreEngine:

    def test_score_latency_100_when_below_warning(self):
        from fastapi_alertengine.intelligence import _score_latency
        assert _score_latency(500.0, 1_000.0, 3_000.0) == 100.0

    def test_score_latency_50_at_critical(self):
        from fastapi_alertengine.intelligence import _score_latency
        score = _score_latency(3_000.0, 1_000.0, 3_000.0)
        assert score == pytest.approx(50.0, abs=1.0)

    def test_score_latency_0_at_3x_critical(self):
        from fastapi_alertengine.intelligence import _score_latency
        score = _score_latency(9_000.0, 1_000.0, 3_000.0)
        assert score == pytest.approx(0.0, abs=1.0)

    def test_score_latency_between_warning_and_critical(self):
        from fastapi_alertengine.intelligence import _score_latency
        # Midpoint between 1000 and 3000 should give ~75
        score = _score_latency(2_000.0, 1_000.0, 3_000.0)
        assert 60.0 < score < 90.0

    def test_score_errors_100_below_warning(self):
        from fastapi_alertengine.intelligence import _score_errors
        assert _score_errors(0.01, 2.0, 5.0) == 100.0   # 1% < 2% warning

    def test_score_errors_50_at_critical(self):
        from fastapi_alertengine.intelligence import _score_errors
        score = _score_errors(0.05, 2.0, 5.0)   # 5% = critical threshold
        assert score == pytest.approx(50.0, abs=1.0)

    def test_score_anomaly_100_when_low(self):
        from fastapi_alertengine.intelligence import _score_anomaly
        assert _score_anomaly(0.2) == 100.0

    def test_score_anomaly_0_when_very_high(self):
        from fastapi_alertengine.intelligence import _score_anomaly
        assert _score_anomaly(5.0) == 0.0

    def test_compute_health_score_returns_health_score(self):
        from fastapi_alertengine.intelligence import compute_health_score
        from fastapi_alertengine.schemas import HealthScore
        config = make_config()
        hs = compute_health_score(
            p95_ms=200.0, error_rate=0.01, anomaly_score=0.1,
            config=config, score_history=[],
        )
        assert isinstance(hs, HealthScore)
        assert 0.0 <= hs.score <= 100.0

    def test_healthy_system_scores_above_degraded_threshold(self):
        from fastapi_alertengine.intelligence import compute_health_score
        config = make_config(health_degraded_threshold=70.0)
        hs = compute_health_score(
            p95_ms=100.0, error_rate=0.001, anomaly_score=0.1,
            config=config, score_history=[],
        )
        assert hs.score >= 70.0
        assert hs.status == "healthy"

    def test_degraded_system_scores_between_thresholds(self):
        from fastapi_alertengine.intelligence import compute_health_score
        config = make_config(health_degraded_threshold=70.0,
                             health_critical_threshold=40.0)
        # Latency at 2.5x warning (2500ms vs 1000 warn / 3000 crit),
        # error rate at 2x critical (10%), anomaly elevated — should push below 70
        hs = compute_health_score(
            p95_ms=2_800.0, error_rate=0.08, anomaly_score=1.8,
            config=config, score_history=[],
        )
        assert hs.status in ("degraded", "critical"), f"Expected degraded/critical, got {hs.status} (score={hs.score:.1f})"

    def test_adaptive_thresholds_used_when_active(self):
        from fastapi_alertengine.intelligence import compute_health_score
        from fastapi_alertengine.schemas import AdaptiveThresholds
        config = make_config(p95_warning_ms=1_000.0, p95_critical_ms=3_000.0)
        # Adaptive thresholds are much lower
        adaptive = AdaptiveThresholds(
            warning_ms=200.0, critical_ms=400.0,
            median_p95_ms=130.0, calibrated_from=20,
            confidence="medium", active=True,
            computed_at=time.time(),
        )
        hs_adaptive = compute_health_score(
            p95_ms=350.0, error_rate=0.01, anomaly_score=0.2,
            config=config, score_history=[], adaptive=adaptive,
        )
        hs_static = compute_health_score(
            p95_ms=350.0, error_rate=0.01, anomaly_score=0.2,
            config=config, score_history=[], adaptive=None,
        )
        # Adaptive should score lower (350ms is near adaptive critical)
        assert hs_adaptive.score < hs_static.score

    def test_inactive_adaptive_thresholds_fall_back_to_static(self):
        from fastapi_alertengine.intelligence import compute_health_score
        from fastapi_alertengine.schemas import AdaptiveThresholds
        config = make_config()
        adaptive = AdaptiveThresholds(
            warning_ms=200.0, critical_ms=400.0,
            median_p95_ms=130.0, calibrated_from=3,
            confidence="low", active=False,          # inactive
            computed_at=time.time(),
        )
        hs_inactive = compute_health_score(
            p95_ms=500.0, error_rate=0.01, anomaly_score=0.1,
            config=config, score_history=[], adaptive=adaptive,
        )
        hs_none = compute_health_score(
            p95_ms=500.0, error_rate=0.01, anomaly_score=0.1,
            config=config, score_history=[], adaptive=None,
        )
        # Should produce the same result when adaptive is inactive
        assert hs_inactive.score == pytest.approx(hs_none.score, abs=0.1)

    def test_trend_improving(self):
        from fastapi_alertengine.intelligence import _determine_trend
        # Scores rising steadily
        history = [50.0, 55.0, 60.0, 65.0, 70.0]
        assert _determine_trend(history) == "improving"

    def test_trend_degrading(self):
        from fastapi_alertengine.intelligence import _determine_trend
        history = [80.0, 74.0, 68.0, 62.0, 56.0]
        assert _determine_trend(history) == "degrading"

    def test_trend_stable(self):
        from fastapi_alertengine.intelligence import _determine_trend
        history = [75.0, 76.0, 74.0, 75.0, 75.0]
        assert _determine_trend(history) == "stable"

    def test_trend_stable_with_fewer_than_3_points(self):
        from fastapi_alertengine.intelligence import _determine_trend
        assert _determine_trend([80.0, 60.0]) == "stable"

    def test_health_score_as_dict_keys(self):
        from fastapi_alertengine.intelligence import compute_health_score
        config = make_config()
        hs = compute_health_score(100.0, 0.01, 0.1, config, [])
        d  = hs.as_dict()
        assert "score" in d
        assert "status" in d
        assert "components" in d
        assert "trend" in d
        for k in ("latency", "errors", "anomalies"):
            assert k in d["components"]


# ══════════════════════════════════════════════════════════════════════════════
# 6-9. Rate-of-Change Detector
# ══════════════════════════════════════════════════════════════════════════════

class TestRateOfChangeDetector:

    def test_latency_spike_detected(self):
        from fastapi_alertengine.intelligence import detect_rate_of_change
        config = make_config(roc_latency_spike_pct=100.0,
                             roc_min_prior_latency_ms=100.0)
        events = detect_rate_of_change(
            current_p95_ms     = 600.0,    # doubled from 300
            previous_p95_ms    = 300.0,
            current_error_rate  = 0.01,
            previous_error_rate = 0.01,
            config             = config,
        )
        assert len(events) == 1
        assert events[0].metric == "p95_latency_ms"
        assert events[0].delta_pct == pytest.approx(100.0, abs=0.1)

    def test_latency_spike_not_detected_below_threshold(self):
        from fastapi_alertengine.intelligence import detect_rate_of_change
        config = make_config(roc_latency_spike_pct=100.0,
                             roc_min_prior_latency_ms=100.0)
        events = detect_rate_of_change(
            current_p95_ms     = 450.0,    # 50% increase — below 100% threshold
            previous_p95_ms    = 300.0,
            current_error_rate  = 0.01,
            previous_error_rate = 0.01,
            config             = config,
        )
        assert len(events) == 0

    def test_error_rate_spike_detected(self):
        from fastapi_alertengine.intelligence import detect_rate_of_change
        # Use 150% threshold and quadruple the rate to ensure clear detection
        config = make_config(roc_error_rate_spike_pct=150.0,
                             roc_min_prior_error_rate=0.005)
        events = detect_rate_of_change(
            current_p95_ms      = 300.0,
            previous_p95_ms     = 300.0,
            current_error_rate  = 0.04,    # 4x from 0.01 = 300% > 150% threshold
            previous_error_rate = 0.01,
            config              = config,
        )
        err_events = [e for e in events if e.metric == "error_rate"]
        assert len(err_events) == 1
        assert err_events[0].delta_pct == pytest.approx(300.0, abs=0.1)

    def test_suppressed_when_prior_latency_too_low(self):
        from fastapi_alertengine.intelligence import detect_rate_of_change
        config = make_config(roc_latency_spike_pct=100.0,
                             roc_min_prior_latency_ms=200.0)
        events = detect_rate_of_change(
            current_p95_ms     = 300.0,    # 200% increase but prior=100ms < min
            previous_p95_ms    = 100.0,    # below roc_min_prior_latency_ms
            current_error_rate  = 0.01,
            previous_error_rate = 0.01,
            config             = config,
        )
        lat_events = [e for e in events if e.metric == "p95_latency_ms"]
        assert len(lat_events) == 0

    def test_suppressed_when_prior_error_rate_too_low(self):
        from fastapi_alertengine.intelligence import detect_rate_of_change
        config = make_config(roc_error_rate_spike_pct=200.0,
                             roc_min_prior_error_rate=0.01)
        events = detect_rate_of_change(
            current_p95_ms      = 300.0,
            previous_p95_ms     = 300.0,
            current_error_rate  = 0.02,
            previous_error_rate = 0.001,   # below roc_min_prior_error_rate
            config              = config,
        )
        err_events = [e for e in events if e.metric == "error_rate"]
        assert len(err_events) == 0

    def test_disabled_when_latency_threshold_zero(self):
        from fastapi_alertengine.intelligence import detect_rate_of_change
        config = make_config(roc_latency_spike_pct=0.0)  # disabled
        events = detect_rate_of_change(
            current_p95_ms     = 1000.0,   # massive spike
            previous_p95_ms    = 200.0,
            current_error_rate  = 0.01,
            previous_error_rate = 0.01,
            config             = config,
        )
        lat_events = [e for e in events if e.metric == "p95_latency_ms"]
        assert len(lat_events) == 0

    def test_roc_event_as_dict_has_required_keys(self):
        from fastapi_alertengine.intelligence import detect_rate_of_change
        config = make_config(roc_latency_spike_pct=50.0,
                             roc_min_prior_latency_ms=100.0)
        events = detect_rate_of_change(600.0, 200.0, 0.01, 0.01, config)
        assert len(events) >= 1
        d = events[0].as_dict()
        for k in ("metric", "previous_value", "current_value",
                  "delta_pct", "window_s", "timestamp"):
            assert k in d


# ══════════════════════════════════════════════════════════════════════════════
# 10. Alert enrichment
# ══════════════════════════════════════════════════════════════════════════════

class TestAlertEnrichment:

    def test_enrich_absolute_threshold(self):
        from fastapi_alertengine.intelligence import enrich_alert
        alert = enrich_alert(
            alert_type     = "latency_spike",
            severity       = "critical",
            message        = "P95 exceeds threshold",
            current_value  = 4_000.0,
            threshold      = 3_000.0,
            baseline_value = 300.0,
            trend          = "degrading",
            triggered_by   = "absolute_threshold",
        )
        assert alert.type                == "latency_spike"
        assert alert.severity            == "critical"
        assert alert.triggered_by        == "absolute_threshold"
        assert alert.trend_direction     == "degrading"
        assert "threshold" in alert.reason_for_trigger.lower()
        assert alert.baseline_comparison is not None
        assert alert.baseline_comparison["baseline_value"] == 300.0

    def test_enrich_rate_of_change(self):
        from fastapi_alertengine.intelligence import enrich_alert
        alert = enrich_alert(
            alert_type     = "latency_spike",
            severity       = "warning",
            message        = "Sudden spike",
            current_value  = 600.0,
            threshold      = 300.0,
            baseline_value = 300.0,
            trend          = "increasing",
            triggered_by   = "rate_of_change",
        )
        assert alert.triggered_by    == "rate_of_change"
        assert "spike" in alert.reason_for_trigger.lower()

    def test_enrich_adaptive_threshold(self):
        from fastapi_alertengine.intelligence import enrich_alert
        alert = enrich_alert(
            alert_type     = "latency_spike",
            severity       = "warning",
            message        = "Adaptive threshold exceeded",
            current_value  = 500.0,
            threshold      = 450.0,
            baseline_value = 300.0,
            trend          = "stable",
            triggered_by   = "adaptive_threshold",
        )
        assert alert.triggered_by == "adaptive_threshold"
        assert "adaptive" in alert.reason_for_trigger.lower()

    def test_enrich_no_baseline_value(self):
        from fastapi_alertengine.intelligence import enrich_alert
        alert = enrich_alert(
            alert_type     = "error_anomaly",
            severity       = "warning",
            message        = "Error rate high",
            current_value  = 8.0,
            threshold      = 5.0,
            baseline_value = None,
            trend          = "stable",
            triggered_by   = "absolute_threshold",
        )
        assert alert.baseline_comparison is None

    def test_as_alert_item_preserves_type_message_severity(self):
        from fastapi_alertengine.intelligence import enrich_alert
        alert = enrich_alert("latency_spike", "critical", "msg",
                             4000.0, 3000.0, 300.0, "stable", "absolute_threshold")
        item = alert.as_alert_item()
        assert item.type     == "latency_spike"
        assert item.message  == "msg"
        assert item.severity == "critical"

    def test_as_dict_contains_v15_keys(self):
        from fastapi_alertengine.intelligence import enrich_alert
        alert = enrich_alert("latency_spike", "critical", "msg",
                             4000.0, 3000.0, 300.0, "degrading", "absolute_threshold")
        d = alert.as_dict()
        for k in ("type", "message", "severity", "reason_for_trigger",
                  "trend_direction", "triggered_by", "baseline_comparison"):
            assert k in d


# ══════════════════════════════════════════════════════════════════════════════
# 11-17. evaluate() integration
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluateV15:

    def _seed(self, engine, n=50, latency=200.0, status=200):
        for _ in range(n):
            engine._recent.append({
                "latency_ms":  latency,
                "type":        "api",
                "status_code": status,
            })

    def test_health_score_key_present(self):
        engine = make_engine()
        self._seed(engine)
        result = engine.evaluate()
        assert "health_score" in result
        assert isinstance(result["health_score"], dict)

    def test_health_score_has_required_keys(self):
        engine = make_engine()
        self._seed(engine)
        hs = engine.evaluate()["health_score"]
        for k in ("score", "status", "components", "trend"):
            assert k in hs

    def test_adaptive_thresholds_key_present(self):
        engine = make_engine()
        self._seed(engine)
        result = engine.evaluate()
        assert "adaptive_thresholds" in result

    def test_adaptive_thresholds_none_without_calibration(self):
        engine = make_engine()
        self._seed(engine)
        result = engine.evaluate()
        # No snapshots yet — should be None
        assert result["adaptive_thresholds"] is None

    def test_rate_of_change_key_present(self):
        engine = make_engine()
        self._seed(engine)
        result = engine.evaluate()
        assert "rate_of_change" in result
        assert isinstance(result["rate_of_change"], list)

    def test_roc_alert_fires_without_threshold_breach(self):
        """RoC detection fires when latency doubles even below warning threshold."""
        config = make_config(
            p95_warning_ms       = 5_000.0,   # very high — won't be crossed
            roc_latency_spike_pct = 100.0,
            roc_min_prior_latency_ms = 50.0,
        )
        engine = make_engine(config=config)
        # Seed first evaluation: low latency
        self._seed(engine, n=50, latency=200.0)
        first = engine.evaluate()
        assert first["status"] == "ok"

        # Second evaluation: latency doubled (still below 5000ms threshold)
        engine._recent.clear()
        self._seed(engine, n=50, latency=450.0)
        second = engine.evaluate()
        # RoC should have fired
        assert len(second["rate_of_change"]) > 0
        # Status promoted to warning
        assert second["status"] in ("warning", "critical")

    def test_roc_not_fired_on_first_evaluation(self):
        """No previous window — RoC cannot fire."""
        engine = make_engine()
        self._seed(engine)
        result = engine.evaluate()
        assert len(result["rate_of_change"]) == 0

    def test_triggered_by_field_on_absolute_alert(self):
        config = make_config(p95_critical_ms=1_000.0)
        engine = make_engine(config=config)
        self._seed(engine, n=50, latency=2_000.0)
        alerts = engine.evaluate()["alerts"]
        assert len(alerts) > 0
        assert alerts[0]["triggered_by"] == "absolute_threshold"

    def test_triggered_by_adaptive_when_active(self):
        from fastapi_alertengine.schemas import AdaptiveThresholds
        config = make_config(
            p95_warning_ms  = 5_000.0,   # static thresholds very high
            p95_critical_ms = 10_000.0,
        )
        engine = make_engine(config=config)
        # Inject active adaptive thresholds — much lower
        engine._adaptive_thresholds = AdaptiveThresholds(
            warning_ms=200.0, critical_ms=400.0,
            median_p95_ms=130.0, calibrated_from=20,
            confidence="medium", active=True, computed_at=time.time(),
        )
        self._seed(engine, n=50, latency=500.0)
        alerts = engine.evaluate()["alerts"]
        latency_alerts = [a for a in alerts if a["type"] == "latency_spike"]
        assert len(latency_alerts) > 0
        assert latency_alerts[0]["triggered_by"] == "adaptive_threshold"

    def test_backward_compat_type_message_severity_present(self):
        config = make_config(p95_critical_ms=500.0)
        engine = make_engine(config=config)
        self._seed(engine, n=50, latency=1_000.0)
        alerts = engine.evaluate()["alerts"]
        assert len(alerts) > 0
        for a in alerts:
            assert "type" in a
            assert "message" in a
            assert "severity" in a

    def test_evaluate_no_data_has_health_score(self):
        """Even with no events, health_score should be present."""
        engine = make_engine()
        result = engine.evaluate()
        assert "health_score" in result
        assert result["health_score"]["score"] == 100.0   # nothing wrong

    def test_score_history_accumulates(self):
        engine = make_engine()
        for _ in range(5):
            self._seed(engine, n=20)
            engine.evaluate()
        assert len(engine._score_history) == 5

    def test_prev_state_updated_after_evaluation(self):
        engine = make_engine()
        self._seed(engine, n=50, latency=300.0)
        engine.evaluate()
        assert engine._prev_p95_ms is not None
        assert engine._prev_error_rate is not None


# ══════════════════════════════════════════════════════════════════════════════
# 18-19. _maybe_recalibrate
# ══════════════════════════════════════════════════════════════════════════════

class TestMaybeRecalibrate:

    def _add_snapshots(self, engine, n=20, p95_ms=300.0):
        from fastapi_alertengine.schemas import BaselineSnapshot
        for i in range(n):
            engine._baseline_snapshots.append(BaselineSnapshot(
                timestamp=time.time() - (n - i) * 60,
                service=engine.config.service_name,
                instance_id=engine.config.instance_id,
                sample_size=100,
                p95_ms=p95_ms, p50_ms=150.0, mean_ms=180.0,
                error_rate=0.01, anomaly_score=0.2, status="ok",
            ))

    def test_calibrates_when_learning_mode_enabled(self):
        config = make_config(
            baseline_learning_mode=True,
            baseline_min_snapshots=5,
            baseline_recalibrate_interval_s=0,  # always recalibrate
        )
        engine = make_engine(config=config)
        self._add_snapshots(engine, n=10)
        engine._maybe_recalibrate()
        assert engine._adaptive_thresholds is not None

    def test_does_not_calibrate_when_learning_mode_disabled(self):
        config = make_config(
            baseline_learning_mode=False,
            baseline_recalibrate_interval_s=0,
        )
        engine = make_engine(config=config)
        self._add_snapshots(engine, n=10)
        engine._maybe_recalibrate()
        assert engine._adaptive_thresholds is None

    def test_does_not_calibrate_before_interval(self):
        config = make_config(
            baseline_learning_mode=True,
            baseline_recalibrate_interval_s=9999,
        )
        engine = make_engine(config=config)
        self._add_snapshots(engine, n=10)
        engine._last_calibration_ts = time.time()  # just calibrated
        engine._maybe_recalibrate()
        assert engine._adaptive_thresholds is None

    def test_does_not_calibrate_without_snapshots(self):
        config = make_config(
            baseline_learning_mode=True,
            baseline_recalibrate_interval_s=0,
        )
        engine = make_engine(config=config)
        # No snapshots
        engine._maybe_recalibrate()
        assert engine._adaptive_thresholds is None

    def test_adaptive_thresholds_computed_correctly(self):
        config = make_config(
            baseline_learning_mode=True,
            baseline_min_snapshots=5,
            baseline_recalibrate_interval_s=0,
            baseline_warning_multiplier=1.5,
            baseline_critical_multiplier=2.0,
        )
        engine = make_engine(config=config)
        self._add_snapshots(engine, n=10, p95_ms=400.0)
        engine._maybe_recalibrate()
        at = engine._adaptive_thresholds
        assert at is not None
        assert at.warning_ms  == pytest.approx(600.0, abs=1.0)
        assert at.critical_ms == pytest.approx(800.0, abs=1.0)


# ══════════════════════════════════════════════════════════════════════════════
# 20. get_ingestion_stats v1.5 keys
# ══════════════════════════════════════════════════════════════════════════════

class TestIngestionStatsV15:

    def test_v15_keys_present(self):
        engine = make_engine()
        stats  = engine.get_ingestion_stats()
        for k in ("adaptive_active", "health_score", "roc_events_recent"):
            assert k in stats, f"Missing v1.5 key: {k}"

    def test_adaptive_active_false_initially(self):
        engine = make_engine()
        assert engine.get_ingestion_stats()["adaptive_active"] is False

    def test_health_score_none_before_first_evaluate(self):
        engine = make_engine()
        assert engine.get_ingestion_stats()["health_score"] is None

    def test_health_score_present_after_evaluate(self):
        engine = make_engine()
        for _ in range(20):
            engine._recent.append({"latency_ms": 100.0, "type": "api",
                                   "status_code": 200})
        engine.evaluate()
        stats = engine.get_ingestion_stats()
        assert stats["health_score"] is not None
        assert isinstance(stats["health_score"], float)

    def test_v14_keys_still_present(self):
        """Backward compat — v1.4 keys must not be removed."""
        engine = make_engine()
        stats  = engine.get_ingestion_stats()
        for k in ("enqueued", "dropped", "last_drain_at",
                  "dropped_agg_keys", "dropped_alerts",
                  "circuit_breaker", "baseline_snapshots"):
            assert k in stats, f"Missing backward-compat key: {k}"
