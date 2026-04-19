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
        write_metric(rdb, config, "/api/ping", "GET", 200, 42.5)
        events = read_metrics(rdb, config, last_n=10)
        assert len(events) == 1
        assert events[0].status_code == 200
        assert abs(events[0].latency_ms - 42.5) < 0.01
        assert events[0].type == "api"

    def test_webhook_classification(self, rdb, config):
        write_metric(rdb, config, "/webhook/cb", "POST", 200, 10.0)
        assert read_metrics(rdb, config, last_n=10)[0].type == "webhook"

    def test_empty_stream_returns_empty_list(self, rdb, config):
        assert read_metrics(rdb, config, last_n=100) == []

    def test_write_never_raises_on_dead_redis(self, config):
        bad = fakeredis.FakeRedis(decode_responses=True)
        bad.close()
        write_metric(bad, config, "/api/x", "GET", 200, 10.0)


# ── AlertEngine.evaluate() output schema ──────────────────────────────────

class TestAlertEventSchema:

    def test_no_data_returns_ok_with_empty_alerts(self, engine):
        r = engine.evaluate()
        assert r["status"]         == "ok"
        assert r["reason"]         == "no_data"
        assert r["alerts"]         == []
        assert r["system_health"]  == 100.0
        assert r["engine_version"] == "1.1.4"

    def test_metrics_field_names_match_advertised_json(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        d = engine.evaluate()
        assert "p95_latency_ms"     in d["metrics"]
        assert "p50_latency_ms"     in d["metrics"]
        assert "error_rate_percent" in d["metrics"]
        assert "request_count_1m"   in d["metrics"]

    def test_timestamp_is_iso8601_utc_string(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        r = engine.evaluate()
        assert isinstance(r["timestamp"], str)
        assert "T" in r["timestamp"] and r["timestamp"].endswith("Z")

    def test_engine_version_in_output(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        assert engine.evaluate()["engine_version"] == "1.1.4"

    def test_system_health_is_float_0_to_100(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        r = engine.evaluate()
        assert isinstance(r["system_health"], float)
        assert 0.0 <= r["system_health"] <= 100.0

    def test_as_dict_is_json_serialisable(self, engine, rdb, config):
        write_metric(rdb, config, "/api/x", "GET", 200, 10.0)
        s = json.dumps(engine.evaluate())
        assert '"status"' in s and '"system_health"' in s and '"engine_version"' in s


# ── Alert classification ──────────────────────────────────────────────────

class TestAlertClassification:

    def test_ok_under_all_thresholds(self, engine, rdb, config):
        for _ in range(50):
            write_metric(rdb, config, "/api/fast", "GET", 200, 50.0)
        r = engine.evaluate()
        assert r["status"] == "ok" and r["alerts"] == [] and r["system_health"] == 100.0

    def test_warning_latency_spike(self, engine, rdb, config):
        for _ in range(94): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(6):  write_metric(rdb, config, "/api/x", "GET", 200, 600.0)
        r = engine.evaluate(window_size=100)
        assert r["status"] == "warning"
        assert any(a["type"] == "latency_spike" and a["severity"] == "warning" for a in r["alerts"])

    def test_critical_latency_spike(self, engine, rdb, config):
        for _ in range(94): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(6):  write_metric(rdb, config, "/api/x", "GET", 200, 2000.0)
        r = engine.evaluate(window_size=100)
        assert r["status"] == "critical"
        assert any(a["type"] == "latency_spike" and a["severity"] == "critical" for a in r["alerts"])

    def test_warning_error_anomaly(self, engine, rdb, config):
        for _ in range(97): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(3):  write_metric(rdb, config, "/api/x", "GET", 500, 50.0)
        r = engine.evaluate(window_size=100)
        assert r["status"] in ("warning", "critical")
        assert any(a["type"] == "error_anomaly" for a in r["alerts"])

    def test_critical_error_anomaly(self, engine, rdb, config):
        for _ in range(90): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(10): write_metric(rdb, config, "/api/x", "GET", 500, 50.0)
        r = engine.evaluate(window_size=100)
        assert r["status"] == "critical"
        assert any(a["type"] == "error_anomaly" and a["severity"] == "critical" for a in r["alerts"])

    def test_error_anomaly_message_includes_baseline(self, engine, rdb, config):
        for _ in range(90): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(10): write_metric(rdb, config, "/api/x", "GET", 500, 50.0)
        r = engine.evaluate(window_size=100)
        ea = [a for a in r["alerts"] if a["type"] == "error_anomaly"]
        assert len(ea) >= 1 and "Baseline:" in ea[0]["message"] and "0.5%" in ea[0]["message"]

    def test_latency_alert_message_includes_threshold(self, engine, rdb, config):
        for _ in range(94): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(6):  write_metric(rdb, config, "/api/x", "GET", 200, 2000.0)
        r = engine.evaluate(window_size=100)
        la = [a for a in r["alerts"] if a["type"] == "latency_spike"]
        assert len(la) >= 1 and "exceeds threshold" in la[0]["message"]

    def test_4xx_do_not_count_as_errors(self, engine, rdb, config):
        for _ in range(50): write_metric(rdb, config, "/api/x", "GET", 404, 10.0)
        r = engine.evaluate(window_size=100)
        assert r["metrics"]["error_rate_percent"] == 0.0 and r["status"] == "ok"

    def test_system_health_degrades_under_load(self, engine, rdb, config):
        for _ in range(50): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        healthy = engine.evaluate(window_size=50)["system_health"]
        rdb.delete(config.stream_key)
        for _ in range(90): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(10): write_metric(rdb, config, "/api/x", "GET", 500, 50.0)
        assert healthy > engine.evaluate(window_size=100)["system_health"]

    def test_multiple_alerts_can_fire_simultaneously(self, engine, rdb, config):
        for _ in range(84): write_metric(rdb, config, "/api/x", "GET", 200, 50.0)
        for _ in range(6):  write_metric(rdb, config, "/api/x", "GET", 200, 2000.0)
        for _ in range(10): write_metric(rdb, config, "/api/x", "GET", 500, 50.0)
        r = engine.evaluate(window_size=100)
        types = [a["type"] for a in r["alerts"]]
        assert "latency_spike" in types and "error_anomaly" in types


# ── Middleware integration ────────────────────────────────────────────────

class TestMiddleware:

    @pytest.mark.asyncio
    async def test_records_successful_request(self, app_client, engine, rdb, config):
        async with AsyncClient(transport=ASGITransport(app=app_client), base_url="http://test") as c:
            resp = await c.get("/ping")
        assert resp.status_code == 200
        events = read_metrics(rdb, config, last_n=10)
        assert len(events) == 1 and events[0].status_code == 200 and events[0].latency_ms > 0

    @pytest.mark.asyncio
    async def test_records_500_error(self, app_client, engine, rdb, config):
        async with AsyncClient(transport=ASGITransport(app=app_client), base_url="http://test") as c:
            await c.get("/fail")
        assert any(e.status_code == 500 for e in read_metrics(rdb, config, last_n=10))

    @pytest.mark.asyncio
    async def test_does_not_alter_response(self, app_client):
        async with AsyncClient(transport=ASGITransport(app=app_client), base_url="http://test") as c:
            assert (await c.get("/ping")).json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_full_pipeline(self, app_client, engine, rdb, config):
        async with AsyncClient(transport=ASGITransport(app=app_client), base_url="http://test") as c:
            for _ in range(20): await c.get("/ping")
        r = engine.evaluate(window_size=50)
        assert r["status"] == "ok"
        assert r["metrics"]["request_count_1m"] == 20
        assert r["metrics"]["p95_latency_ms"] > 0
        assert r["metrics"]["error_rate_percent"] == 0.0


# ── get_alert_engine factory ──────────────────────────────────────────────

class TestClientFactory:

    def teardown_method(self):
        from fastapi_alertengine.client import clear_alert_engine
        clear_alert_engine()

    def test_returns_engine_instance(self, rdb, config):
        assert isinstance(get_alert_engine(config=config, redis_client=rdb), AlertEngine)

    def test_singleton(self, rdb, config):
        e1 = get_alert_engine(config=config, redis_client=rdb)
        e2 = get_alert_engine(config=config, redis_client=rdb)
        assert e1 is e2

    def test_auto_builds_redis_no_valueerror(self, config):
        try:
            assert isinstance(get_alert_engine(config=config), AlertEngine)
        except Exception as exc:
            assert "redis_client is required" not in str(exc)
