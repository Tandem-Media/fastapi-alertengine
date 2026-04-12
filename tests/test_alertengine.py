"""
Tests for fastapi-alertengine.

All tests use MagicMock for Redis so no live Redis instance is required.
"""

import asyncio
import os
import warnings
from collections import deque
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import fastapi_alertengine.client as client_module
from fastapi_alertengine import (
    AlertConfig,
    AlertEngine,
    RequestMetricsMiddleware,
    aggregate,
    get_alert_engine,
    instrument,
)
from fastapi_alertengine.client import _reset_engine
from fastapi_alertengine.engine import MAX_QUEUE_SIZE
from fastapi_alertengine.storage import write_metric


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_redis(decode_responses: bool = True) -> MagicMock:
    """Return a MagicMock that simulates a redis.Redis instance."""
    rdb = MagicMock()
    pool = MagicMock()
    pool.connection_kwargs = {"decode_responses": decode_responses}
    rdb.connection_pool = pool
    # xrevrange returns an empty list by default (no recorded events)
    rdb.xrevrange.return_value = []
    return rdb


def _make_engine(decode_responses: bool = True) -> AlertEngine:
    config = AlertConfig()
    return AlertEngine(redis=_make_redis(decode_responses), config=config)


@pytest.fixture(autouse=True)
def reset_engine_singleton():
    """Ensure each test starts with a fresh engine singleton."""
    _reset_engine()
    yield
    _reset_engine()


# ── instrument() ──────────────────────────────────────────────────────────────


class TestInstrument:
    def test_returns_alert_engine(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            result = instrument(app)
        assert isinstance(result, AlertEngine)

    def test_health_endpoint_registered(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            resp = client.get("/health/alerts")
        assert resp.status_code == 200

    def test_health_endpoint_returns_valid_status(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            data = client.get("/health/alerts").json()
        assert "status" in data
        assert data["status"] in ("ok", "warning", "critical")

    def test_custom_health_path(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app, health_path="/my/alerts")
        with TestClient(app) as client:
            assert client.get("/my/alerts").status_code == 200
            # Default path should not exist
            assert client.get("/health/alerts").status_code == 404

    def test_middleware_records_metrics(self):
        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"pong": True}

        rdb = _make_redis()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = rdb
            engine = instrument(app)

        with TestClient(app) as client:
            client.get("/ping")

        assert len(engine._queue) >= 1, "middleware should have enqueued a metric"

    def test_metrics_enqueued_contain_expected_keys(self):
        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {}

        rdb = _make_redis()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = rdb
            engine = instrument(app)

        with TestClient(app) as client:
            client.get("/ping")

        metric = engine._queue[-1]
        assert "path" in metric
        assert "method" in metric
        assert "status_code" in metric
        assert "latency_ms" in metric

    def test_health_endpoint_not_in_openapi_schema(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        assert "/health/alerts" not in paths

    def test_instrument_with_redis_url_arg(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app, redis_url="redis://custom:6380/1")
            mock_redis_mod.Redis.from_url.assert_called_once_with(
                "redis://custom:6380/1", decode_responses=True
            )

    def test_instrument_with_config_arg(self):
        app = FastAPI()
        config = AlertConfig(redis_url="redis://cfg:6379/2")
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app, config=config)
            mock_redis_mod.Redis.from_url.assert_called_once_with(
                "redis://cfg:6379/2", decode_responses=True
            )


# ── Redis URL / config resolution ─────────────────────────────────────────────


class TestRedisResolution:
    def test_env_var_used_when_no_explicit_url(self):
        app = FastAPI()
        env = {"ALERTENGINE_REDIS_URL": "redis://env-host:6379/0"}
        with patch.dict(os.environ, env, clear=False):
            with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
                mock_redis_mod.Redis.from_url.return_value = _make_redis()
                instrument(app)
                call_url = mock_redis_mod.Redis.from_url.call_args[0][0]
        assert call_url == "redis://env-host:6379/0"

    def test_default_url_used_when_no_env_and_no_arg(self):
        app = FastAPI()
        env_without_url = {k: v for k, v in os.environ.items() if k != "ALERTENGINE_REDIS_URL"}
        with patch.dict(os.environ, env_without_url, clear=True):
            with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
                mock_redis_mod.Redis.from_url.return_value = _make_redis()
                instrument(app)
                call_url = mock_redis_mod.Redis.from_url.call_args[0][0]
        assert call_url == "redis://localhost:6379/0"

    def test_redis_url_arg_takes_precedence_over_env(self):
        app = FastAPI()
        env = {"ALERTENGINE_REDIS_URL": "redis://env-host:6379/0"}
        with patch.dict(os.environ, env, clear=False):
            with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
                mock_redis_mod.Redis.from_url.return_value = _make_redis()
                instrument(app, redis_url="redis://explicit:9999/3")
                call_url = mock_redis_mod.Redis.from_url.call_args[0][0]
        assert call_url == "redis://explicit:9999/3"


# ── decode_responses enforcement ──────────────────────────────────────────────


class TestDecodeResponsesEnforcement:
    def test_internal_redis_created_with_decode_responses(self):
        app = FastAPI()
        captured = {}

        def fake_from_url(url, **kwargs):
            captured["kwargs"] = kwargs
            return _make_redis()

        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.side_effect = fake_from_url
            instrument(app)

        assert captured["kwargs"].get("decode_responses") is True

    def test_get_alert_engine_builds_redis_with_decode_responses(self):
        captured = {}

        def fake_from_url(url, **kwargs):
            captured["kwargs"] = kwargs
            return _make_redis()

        with patch("fastapi_alertengine.client.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.side_effect = fake_from_url
            get_alert_engine()

        assert captured["kwargs"].get("decode_responses") is True

    def test_user_client_without_decode_responses_warns(self):
        bad_client = _make_redis(decode_responses=False)
        with pytest.warns(UserWarning, match="decode_responses=True"):
            get_alert_engine(redis_client=bad_client)

    def test_user_client_with_decode_responses_no_warning(self):
        good_client = _make_redis(decode_responses=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            get_alert_engine(redis_client=good_client)
        decode_warns = [w for w in caught if "decode_responses" in str(w.message)]
        assert len(decode_warns) == 0


# ── Queue bounding ────────────────────────────────────────────────────────────


class TestQueueBounding:
    def test_queue_stays_at_max_size(self):
        engine = _make_engine()
        for i in range(MAX_QUEUE_SIZE + 500):
            engine.enqueue_metric({"path": "/", "method": "GET", "status_code": 200, "latency_ms": float(i)})
        assert len(engine._queue) == MAX_QUEUE_SIZE

    def test_oldest_metric_dropped_when_full(self):
        engine = _make_engine()
        # Fill queue to capacity with sentinel value in first slot
        engine.enqueue_metric({"path": "/first", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        for _ in range(MAX_QUEUE_SIZE - 1):
            engine.enqueue_metric({"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 0.0})
        # One more pushes the first item out
        engine.enqueue_metric({"path": "/last", "method": "GET", "status_code": 200, "latency_ms": 2.0})

        paths = [m["path"] for m in engine._queue]
        assert "/first" not in paths
        assert "/last" in paths

    def test_queue_below_max_does_not_drop(self):
        engine = _make_engine()
        for i in range(MAX_QUEUE_SIZE - 1):
            engine.enqueue_metric({"path": f"/{i}", "method": "GET", "status_code": 200, "latency_ms": 0.0})
        assert len(engine._queue) == MAX_QUEUE_SIZE - 1

    def test_empty_queue_allows_enqueue(self):
        engine = _make_engine()
        engine.enqueue_metric({"path": "/", "method": "GET", "status_code": 200, "latency_ms": 5.0})
        assert len(engine._queue) == 1


# ── drain() robustness ────────────────────────────────────────────────────────


class TestDrainRobustness:
    def _run_drain_once(self, engine: AlertEngine) -> None:
        """Run drain() until the queue is empty then cancel it."""

        async def _run():
            task = asyncio.create_task(engine.drain())
            # Let the event loop tick a few times so drain processes the queue
            for _ in range(10):
                await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

    def test_drain_flushes_queue_to_redis(self):
        engine = _make_engine()
        engine.enqueue_metric({"path": "/a", "method": "GET", "status_code": 200, "latency_ms": 10.0})
        engine.enqueue_metric({"path": "/b", "method": "POST", "status_code": 201, "latency_ms": 20.0})

        self._run_drain_once(engine)

        assert engine.redis.xadd.call_count == 2

    def test_drain_continues_after_single_write_failure(self):
        engine = _make_engine()

        call_count = {"n": 0}

        def flaky_xadd(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated Redis error")
            # Second call succeeds without calling back into the mock
            return "ok"

        engine.redis.xadd.side_effect = flaky_xadd

        engine.enqueue_metric({"path": "/fail", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        engine.enqueue_metric({"path": "/ok", "method": "GET", "status_code": 200, "latency_ms": 2.0})

        self._run_drain_once(engine)

        # Both metrics were attempted — first failed, second succeeded
        assert call_count["n"] == 2

    def test_drain_queue_empty_after_flush(self):
        engine = _make_engine()
        engine.enqueue_metric({"path": "/c", "method": "GET", "status_code": 200, "latency_ms": 5.0})

        self._run_drain_once(engine)

        assert len(engine._queue) == 0

    def test_drain_stops_cleanly_on_cancel(self):
        engine = _make_engine()

        async def _cancel_immediately():
            task = asyncio.create_task(engine.drain())
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should not raise any exception
        asyncio.run(_cancel_immediately())


# ── middleware ────────────────────────────────────────────────────────────────


class TestMiddleware:
    def _make_app_with_middleware(self, engine: AlertEngine) -> FastAPI:
        app = FastAPI()
        app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

        @app.get("/hello")
        def hello():
            return {"hi": True}

        return app

    def test_middleware_enqueues_on_request(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert len(engine._queue) == 1

    def test_middleware_captures_correct_status_code(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert engine._queue[0]["status_code"] == 200

    def test_middleware_captures_path(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert engine._queue[0]["path"] == "/hello"

    def test_middleware_captures_positive_latency(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert engine._queue[0]["latency_ms"] >= 0.0

    def test_middleware_captures_method(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert engine._queue[0]["method"] == "GET"

    def test_middleware_multiple_requests_enqueue_all(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            for _ in range(5):
                client.get("/hello")
        assert len(engine._queue) == 5


# ── AlertEngine.evaluate() ────────────────────────────────────────────────────


class TestEvaluate:
    def _engine_with_events(self, events: list) -> AlertEngine:
        """Return an engine whose Redis mock returns the given events."""
        rdb = _make_redis()
        rdb.xrevrange.return_value = [
            (f"1-{i}", {
                "latency_ms": str(e["latency_ms"]),
                "status":     str(e["status_code"]),
                "type":       e.get("type", "api"),
            })
            for i, e in enumerate(events)
        ]
        return AlertEngine(redis=rdb, config=AlertConfig())

    def test_no_data_returns_ok(self):
        engine = _make_engine()
        result = engine.evaluate()
        assert result["status"] == "ok"
        assert result.get("reason") == "no_data"

    def test_normal_latency_is_ok(self):
        events = [{"latency_ms": 50.0, "status_code": 200} for _ in range(20)]
        engine = self._engine_with_events(events)
        result = engine.evaluate()
        assert result["status"] == "ok"

    def test_high_latency_triggers_warning(self):
        events = [{"latency_ms": 1500.0, "status_code": 200} for _ in range(20)]
        engine = self._engine_with_events(events)
        result = engine.evaluate()
        assert result["status"] in ("warning", "critical")

    def test_very_high_latency_triggers_critical(self):
        events = [{"latency_ms": 4000.0, "status_code": 200} for _ in range(20)]
        engine = self._engine_with_events(events)
        result = engine.evaluate()
        assert result["status"] == "critical"

    def test_high_error_rate_triggers_critical(self):
        events = [{"latency_ms": 10.0, "status_code": 500} for _ in range(30)]
        engine = self._engine_with_events(events)
        result = engine.evaluate()
        assert result["status"] == "critical"

    def test_moderate_error_rate_triggers_warning(self):
        # 15% error rate → warning
        events = (
            [{"latency_ms": 10.0, "status_code": 500} for _ in range(15)]
            + [{"latency_ms": 10.0, "status_code": 200} for _ in range(85)]
        )
        engine = self._engine_with_events(events)
        result = engine.evaluate()
        assert result["status"] in ("warning", "critical")

    def test_evaluate_returns_all_metric_keys(self):
        events = [{"latency_ms": 50.0, "status_code": 200} for _ in range(10)]
        engine = self._engine_with_events(events)
        result = engine.evaluate()
        metrics = result.get("metrics", {})
        for key in ("overall_p95_ms", "webhook_p95_ms", "api_p95_ms", "error_rate", "anomaly_score", "sample_size"):
            assert key in metrics, f"missing metric key: {key}"

    def test_evaluate_returns_all_threshold_keys(self):
        events = [{"latency_ms": 50.0, "status_code": 200} for _ in range(10)]
        engine = self._engine_with_events(events)
        result = engine.evaluate()
        thresholds = result.get("thresholds", {})
        for key in ("p95_warning_ms", "p95_critical_ms", "anomaly_warning", "anomaly_critical",
                    "error_rate_warning", "error_rate_critical"):
            assert key in thresholds, f"missing threshold key: {key}"

    def test_engine_uses_config_stream_key(self):
        rdb = _make_redis()
        config = AlertConfig(stream_key="custom:metrics")
        engine = AlertEngine(redis=rdb, config=config)
        engine.evaluate()
        rdb.xrevrange.assert_called_with("custom:metrics", count=200)

    def test_status_field_read_correctly(self):
        """stream stores 'status', not 'status_code'; engine must read it right."""
        rdb = _make_redis()
        # All 500s should give critical via error rate
        rdb.xrevrange.return_value = [
            (f"1-{i}", {"latency_ms": "10.0", "status": "500", "type": "api"})
            for i in range(30)
        ]
        engine = AlertEngine(redis=rdb, config=AlertConfig())
        result = engine.evaluate()
        assert result["metrics"]["error_rate"] == 1.0


# ── write_metric / storage ────────────────────────────────────────────────────


class TestStorage:
    def test_write_metric_calls_xadd(self):
        rdb = _make_redis()
        config = AlertConfig()
        metric = {"path": "/api/data", "method": "get", "status_code": 200, "latency_ms": 42.5}
        write_metric(rdb, config, metric)
        rdb.xadd.assert_called_once()

    def test_write_metric_uses_config_stream_key(self):
        rdb = _make_redis()
        config = AlertConfig(stream_key="my:stream")
        write_metric(rdb, config, {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        call_args = rdb.xadd.call_args
        assert call_args[0][0] == "my:stream"

    def test_write_metric_uppercases_method(self):
        rdb = _make_redis()
        write_metric(rdb, AlertConfig(), {"path": "/x", "method": "post", "status_code": 201, "latency_ms": 5.0})
        fields = rdb.xadd.call_args[0][1]
        assert fields["method"] == "POST"

    def test_write_metric_classifies_webhook(self):
        rdb = _make_redis()
        write_metric(rdb, AlertConfig(), {"path": "/webhook/notify", "method": "POST", "status_code": 200, "latency_ms": 5.0})
        fields = rdb.xadd.call_args[0][1]
        assert fields["type"] == "webhook"

    def test_write_metric_classifies_api(self):
        rdb = _make_redis()
        write_metric(rdb, AlertConfig(), {"path": "/api/items", "method": "GET", "status_code": 200, "latency_ms": 5.0})
        fields = rdb.xadd.call_args[0][1]
        assert fields["type"] == "api"

    def test_write_metric_survives_redis_error(self):
        rdb = _make_redis()
        rdb.xadd.side_effect = RuntimeError("connection lost")
        # Must not raise
        write_metric(rdb, AlertConfig(), {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})

    def test_write_metric_uses_config_stream_maxlen(self):
        rdb = _make_redis()
        config = AlertConfig(stream_maxlen=999)
        write_metric(rdb, config, {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        call_kwargs = rdb.xadd.call_args[1]
        assert call_kwargs["maxlen"] == 999


# ── AlertConfig ───────────────────────────────────────────────────────────────


class TestAlertConfig:
    def test_defaults(self):
        config = AlertConfig()
        assert config.redis_url == "redis://localhost:6379/0"
        assert config.stream_key == "anchorflow:request_metrics"
        assert config.stream_maxlen == 5000

    def test_env_prefix(self):
        env = {
            "ALERTENGINE_REDIS_URL": "redis://env:6379/0",
            "ALERTENGINE_STREAM_KEY": "env:stream",
            "ALERTENGINE_STREAM_MAXLEN": "1234",
        }
        with patch.dict(os.environ, env, clear=False):
            config = AlertConfig()
        assert config.redis_url == "redis://env:6379/0"
        assert config.stream_key == "env:stream"
        assert config.stream_maxlen == 1234


# ── aggregate() ───────────────────────────────────────────────────────────────


class TestAggregate:
    def test_aggregate_returns_expected_structure(self):
        rdb = _make_redis()
        result = aggregate(rdb, AlertConfig())
        assert "webhook_latency" in result
        assert "api_latency" in result
        assert "overall_latency" in result
        for key in result:
            assert "p95_ms" in result[key]
            assert "count" in result[key]

    def test_aggregate_empty_returns_none_p95(self):
        rdb = _make_redis()
        result = aggregate(rdb, AlertConfig())
        assert result["overall_latency"]["p95_ms"] is None
        assert result["overall_latency"]["count"] == 0
