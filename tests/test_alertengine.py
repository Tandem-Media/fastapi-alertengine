# tests/test_alertengine.py
"""
Automated test suite for fastapi-alertengine.

Run with:  pytest tests/ -v
"""

import json
import pytest
import fakeredis
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from fastapi_alertengine import (
    AlertEngine, AlertConfig, RequestMetricsMiddleware,
    get_alert_engine, AlertEvent,
)
from fastapi_alertengine.schemas import AlertItem
from fastapi_alertengine.storage import write_metric, read_metrics, aggregate


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def rdb():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def config():
    return AlertConfig(
        stream_key              = "test:metrics",
        stream_maxlen           = 1_000,
        p95_warning_ms          = 500.0,
        p95_critical_ms         = 1_000.0,
        error_rate_warning_pct  = 2.0,
        error_rate_critical_pct = 5.0,
        error_rate_baseline_pct = 0.5,
    )


@pytest.fixture()
def engine(config, rdb):
    return AlertEngine(config=config, redis=rdb)


@pytest.fixture()
def app_client(engine):
    app = FastAPI()
    app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.get("/fail")
    def fail():
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="boom")

    return app


# ── storage layer ─────────────────────────────────────────────────────────

class TestStorage:

    def test_write_and_read_roundtrip(self, rdb, config):
        write_metric(rdb, config, "/api/v1/ping", "GET", 200, 42.5)
        events = read_metrics(rdb, config, last_n=10)
        assert len(events) == 1
        e = events[0]
        assert e.path        == "/api/v1/ping"
        assert e.method      == "GET"
        assert e.status_code == 200
        assert abs(e.latency_ms - 42.5) < 0.01
        assert e.type        == "api"

    def test_webhook_classification(self, rdb, config):
        write_metric(rdb, config, "/webhook/callback", "POST", 200, 10.0)
        events = read_metrics(rdb, config, last_n=10)
        assert events[0].type == "webhook"

    def test_empty_stream_returns_empty_list(self, rdb, config):
        assert read_metrics(rdb, config, last_n=100) == []

    def test_write_never_raises_on_dead_redis(self, config):
        bad = fakeredis.FakeRedis(decode_responses=True)
        bad.close()
        write_metric(bad, config, "/api/x", "GET", 200, 10.0)  # must not raise


# ── AlertEngine.evaluate() output schema ──────────────────────────────────

class TestAlertEventSchema:
    """Verify every field in the advertised JSON output is present and correct."""

    def test_no_data_returns_ok_with_empty_alerts(self, engine):
        result = engine.evaluate()
        assert result.status        == "ok"
        assert result.reason        == "no_data"
        assert result.alerts        == []
        assert result.system_health == 100.0
        assert result.engine_version == "1.1.3"

    def test_metrics_field_names_match_advertised_json(self, engine, rdb, config):
        """Field names must be exactly p95_latency_ms, p50_latency_ms,
        error_rate_percent, request_count_1m."""
        write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        d = engine.evaluate().as_dict()

        assert "p95_latency_ms"     in d["metrics"]
        assert "p50_latency_ms"     in d["metrics"]
        assert "error_rate_percent" in d["metrics"]
        assert "request_count_1m"   in d["metrics"]

    def test_timestamp_is_iso8601_utc_string(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        result = engine.evaluate()
        # e.g. "2026-04-10T14:38:21Z"
        assert isinstance(result.timestamp, str)
        assert "T" in result.timestamp
        assert result.timestamp.endswith("Z")

    def test_engine_version_in_output(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        d = engine.evaluate().as_dict()
        assert d["engine_version"] == "1.1.3"

    def test_system_health_is_float_0_to_100(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        result = engine.evaluate()
        assert isinstance(result.system_health, float)
        assert 0.0 <= result.system_health <= 100.0

    def test_as_dict_is_json_serialisable(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 10.0)
        serialised = json.dumps(engine.evaluate().as_dict())
        assert '"status"' in serialised
        assert '"system_health"' in serialised
        assert '"engine_version"' in serialised


# ── Alert classification ──────────────────────────────────────────────────

class TestAlertClassification:

    def test_ok_under_all_thresholds(self, engine, rdb, config):
        for _ in range(50):
            write_metric(rdb, config, "/api/fast", "GET", 200, 50.0)
        result = engine.evaluate()
        assert result.status == "ok"
        assert result.alerts == []
        assert result.system_health == 100.0

    def test_warning_latency_spike(self, engine, rdb, config):
        # p95_warning_ms = 500 in fixture
        for _ in range(94):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(6):
            write_metric(rdb, config, "/api/x", "GET", 200, 600.0)

        result = engine.evaluate(window_size=100)
        assert result.status == "warning"
        assert any(a.type == "latency_spike" and a.severity == "warning"
                   for a in result.alerts)

    def test_critical_latency_spike(self, engine, rdb, config):
        # p95_critical_ms = 1000 in fixture
        for _ in range(94):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(6):
            write_metric(rdb, config, "/api/x", "GET", 200, 2_000.0)

        result = engine.evaluate(window_size=100)
        assert result.status == "critical"
        assert any(a.type == "latency_spike" and a.severity == "critical"
                   for a in result.alerts)

    def test_warning_error_anomaly(self, engine, rdb, config):
        # error_rate_warning_pct = 2.0, so 3% should trigger warning
        for _ in range(97):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(3):
            write_metric(rdb, config, "/api/x", "GET", 500, 50.0)

        result = engine.evaluate(window_size=100)
        assert result.status in ("warning", "critical")
        assert any(a.type == "error_anomaly" for a in result.alerts)

    def test_critical_error_anomaly(self, engine, rdb, config):
        # error_rate_critical_pct = 5.0, so 10% triggers critical
        for _ in range(90):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(10):
            write_metric(rdb, config, "/api/x", "GET", 500, 50.0)

        result = engine.evaluate(window_size=100)
        assert result.status == "critical"
        assert any(a.type == "error_anomaly" and a.severity == "critical"
                   for a in result.alerts)

    def test_error_anomaly_message_includes_baseline(self, engine, rdb, config):
        """Alert message must say 'Baseline: 0.5%' as advertised."""
        for _ in range(90):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(10):
            write_metric(rdb, config, "/api/x", "GET", 500, 50.0)

        result = engine.evaluate(window_size=100)
        error_alerts = [a for a in result.alerts if a.type == "error_anomaly"]
        assert len(error_alerts) >= 1
        assert "Baseline:" in error_alerts[0].message
        assert "0.5%" in error_alerts[0].message

    def test_latency_alert_message_includes_threshold(self, engine, rdb, config):
        """Alert message must say 'exceeds threshold (Xms)' as advertised."""
        for _ in range(94):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(6):
            write_metric(rdb, config, "/api/x", "GET", 200, 2_000.0)

        result = engine.evaluate(window_size=100)
        lat_alerts = [a for a in result.alerts if a.type == "latency_spike"]
        assert len(lat_alerts) >= 1
        assert "exceeds threshold" in lat_alerts[0].message

    def test_4xx_do_not_count_as_errors(self, engine, rdb, config):
        for _ in range(50):
            write_metric(rdb, config, "/api/missing", "GET", 404, 10.0)

        result = engine.evaluate(window_size=100)
        assert result.metrics.error_rate_percent == 0.0
        assert result.status == "ok"

    def test_system_health_degrades_under_load(self, engine, rdb, config):
        # Perfect conditions → 100, spike conditions → lower
        for _ in range(50):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        healthy = engine.evaluate(window_size=50).system_health

        rdb.delete(config.stream_key)
        for _ in range(90):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(10):
            write_metric(rdb, config, "/api/x", "GET", 500, 50.0)
        degraded = engine.evaluate(window_size=100).system_health

        assert healthy > degraded

    def test_multiple_alerts_can_fire_simultaneously(self, engine, rdb, config):
        """Both latency_spike AND error_anomaly can appear in the same result."""
        for _ in range(84):
            write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(6):
            write_metric(rdb, config, "/api/x", "GET", 200, 2_000.0)
        for _ in range(10):
            write_metric(rdb, config, "/api/x", "GET", 500, 50.0)

        result = engine.evaluate(window_size=100)
        types = [a.type for a in result.alerts]
        assert "latency_spike"  in types
        assert "error_anomaly"  in types


# ── Middleware integration ────────────────────────────────────────────────

class TestMiddleware:

    @pytest.mark.asyncio
    async def test_records_successful_request(self, app_client, engine, rdb, config):
        transport = ASGITransport(app=app_client)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/ping")
        assert resp.status_code == 200
        events = read_metrics(rdb, config, last_n=10)
        assert len(events) == 1
        assert events[0].status_code == 200
        assert events[0].latency_ms  > 0

    @pytest.mark.asyncio
    async def test_records_500_error(self, app_client, engine, rdb, config):
        transport = ASGITransport(app=app_client)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/fail")
        events = read_metrics(rdb, config, last_n=10)
        assert any(e.status_code == 500 for e in events)

    @pytest.mark.asyncio
    async def test_does_not_alter_response(self, app_client):
        transport = ASGITransport(app=app_client)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/ping")
        assert resp.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_full_pipeline(self, app_client, engine, rdb, config):
        transport = ASGITransport(app=app_client)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(20):
                await c.get("/ping")

        result = engine.evaluate(window_size=50)
        assert result.status == "ok"
        assert result.metrics.request_count_1m   == 20
        assert result.metrics.p95_latency_ms      > 0
        assert result.metrics.p50_latency_ms      > 0
        assert result.metrics.error_rate_percent  == 0.0


# ── get_alert_engine factory ──────────────────────────────────────────────

class TestClientFactory:

    def teardown_method(self):
        from fastapi_alertengine.client import clear_alert_engine; clear_alert_engine()

    def test_returns_engine_instance(self, rdb, config):
        e = get_alert_engine(config=config, redis_client=rdb)
        assert isinstance(e, AlertEngine)

    def test_singleton(self, rdb, config):
        e1 = get_alert_engine(config=config, redis_client=rdb)
        e2 = get_alert_engine(config=config, redis_client=rdb)
        assert e1 is e2

    def test_auto_builds_redis_no_valueerror(self, config):
        try:
            e = get_alert_engine(config=config)
            assert isinstance(e, AlertEngine)
        except Exception as exc:
            assert "redis_client is required" not in str(exc)
