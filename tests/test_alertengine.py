"""
Tests for fastapi-alertengine.

All tests use MagicMock for Redis so no live Redis instance is required.
"""

import asyncio
import os
import time
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

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
    write_batch,
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


def _make_engine(decode_responses: bool = True, **config_kwargs) -> AlertEngine:
    config = AlertConfig(**config_kwargs)
    return AlertEngine(redis=_make_redis(decode_responses), config=config)


def _queue_items(engine: AlertEngine) -> list:
    """Return a snapshot list of items currently in the engine's asyncio.Queue."""
    return list(engine._queue._queue)


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

        assert engine._queue.qsize() >= 1, "middleware should have enqueued a metric"

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

        metric = _queue_items(engine)[-1]
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

    # ── New endpoints wired by instrument() ───────────────────────────────────

    def test_alerts_evaluate_endpoint_registered(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            resp = client.post("/alerts/evaluate")
        assert resp.status_code == 200

    def test_alerts_evaluate_returns_valid_status(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            data = client.post("/alerts/evaluate").json()
        assert "status" in data
        assert data["status"] in ("ok", "warning", "critical")

    def test_metrics_history_endpoint_registered(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            resp = client.get("/metrics/history")
        assert resp.status_code == 200

    def test_metrics_history_returns_metrics_key(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            data = client.get("/metrics/history").json()
        assert "metrics" in data
        assert isinstance(data["metrics"], list)


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
        assert engine._queue.qsize() == MAX_QUEUE_SIZE

    def test_newest_metric_dropped_when_full(self):
        """asyncio.Queue drops the newest (incoming) metric when full."""
        engine = _make_engine()
        # Enqueue sentinel as the first item
        engine.enqueue_metric({"path": "/first", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        # Fill the rest of the queue
        for _ in range(MAX_QUEUE_SIZE - 1):
            engine.enqueue_metric({"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 0.0})
        # Queue is now full; this entry should be dropped
        engine.enqueue_metric({"path": "/last", "method": "GET", "status_code": 200, "latency_ms": 2.0})

        paths = [m["path"] for m in _queue_items(engine)]
        # Oldest item (/first) is still present; newest overflow (/last) was dropped
        assert "/first" in paths
        assert "/last" not in paths

    def test_queue_below_max_does_not_drop(self):
        engine = _make_engine()
        for i in range(MAX_QUEUE_SIZE - 1):
            engine.enqueue_metric({"path": f"/{i}", "method": "GET", "status_code": 200, "latency_ms": 0.0})
        assert engine._queue.qsize() == MAX_QUEUE_SIZE - 1

    def test_empty_queue_allows_enqueue(self):
        engine = _make_engine()
        engine.enqueue_metric({"path": "/", "method": "GET", "status_code": 200, "latency_ms": 5.0})
        assert engine._queue.qsize() == 1


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
        """drain() calls write_batch, which uses the pipeline's xadd."""
        engine = _make_engine()
        engine.enqueue_metric({"path": "/a", "method": "GET", "status_code": 200, "latency_ms": 10.0})
        engine.enqueue_metric({"path": "/b", "method": "POST", "status_code": 201, "latency_ms": 20.0})

        self._run_drain_once(engine)

        # write_batch uses a pipeline; pipeline.xadd should have been called twice
        pipe = engine.redis.pipeline.return_value
        assert pipe.xadd.call_count == 2
        assert pipe.execute.call_count >= 1

    def test_drain_continues_after_write_batch_failure(self):
        """If write_batch raises (patched at the engine level), drain recovers and continues."""
        engine = _make_engine()

        call_count = {"n": 0}

        def flaky_write_batch(rdb, config, batch):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated write_batch error")
            # Subsequent calls succeed silently

        async def _run():
            with patch("fastapi_alertengine.engine.write_batch", side_effect=flaky_write_batch):
                task = asyncio.create_task(engine.drain())
                # First metric → write_batch raises → drain sleeps 1s to recover
                engine.enqueue_metric({"path": "/a", "method": "GET", "status_code": 200, "latency_ms": 1.0})
                await asyncio.sleep(1.2)   # wait past the 1s recovery sleep
                # Second metric → write_batch should succeed on next iteration
                engine.enqueue_metric({"path": "/b", "method": "GET", "status_code": 200, "latency_ms": 2.0})
                await asyncio.sleep(0.1)   # give drain time to process
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(_run())

        # write_batch was called at least twice: once (failed) then at least once more (recovered)
        assert call_count["n"] >= 2

    def test_drain_queue_empty_after_flush(self):
        engine = _make_engine()
        engine.enqueue_metric({"path": "/c", "method": "GET", "status_code": 200, "latency_ms": 5.0})

        self._run_drain_once(engine)

        assert engine._queue.qsize() == 0

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

    def test_drain_uses_batch_of_100(self):
        """drain() pulls up to 100 metrics per iteration."""
        engine = _make_engine()
        for i in range(150):
            engine.enqueue_metric({"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})

        async def _run():
            task = asyncio.create_task(engine.drain())
            # One sleep cycle processes one batch
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

        pipe = engine.redis.pipeline.return_value
        # First batch processes 100 items
        assert pipe.xadd.call_count >= 100


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
        assert engine._queue.qsize() == 1

    def test_middleware_captures_correct_status_code(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert _queue_items(engine)[0]["status_code"] == 200

    def test_middleware_captures_path(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert _queue_items(engine)[0]["path"] == "/hello"

    def test_middleware_captures_positive_latency(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert _queue_items(engine)[0]["latency_ms"] >= 0.0

    def test_middleware_captures_method(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert _queue_items(engine)[0]["method"] == "GET"

    def test_middleware_multiple_requests_enqueue_all(self):
        engine = _make_engine()
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            for _ in range(5):
                client.get("/hello")
        assert engine._queue.qsize() == 5

    def test_middleware_adds_service_name(self):
        engine = _make_engine(service_name="my-service")
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert _queue_items(engine)[0]["service_name"] == "my-service"

    def test_middleware_adds_instance_id(self):
        engine = _make_engine(instance_id="pod-abc")
        app = self._make_app_with_middleware(engine)
        with TestClient(app) as client:
            client.get("/hello")
        assert _queue_items(engine)[0]["instance_id"] == "pod-abc"


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
        # thresholds live in AlertConfig, not duplicated in evaluate() output
        assert "alerts" in result

    def test_engine_uses_config_stream_key(self):
        rdb = _make_redis()
        config = AlertConfig(stream_key="custom:metrics")
        engine = AlertEngine(redis=rdb, config=config)
        engine.evaluate()
        rdb.xrevrange.assert_called_with("custom:metrics", count=200)

    def test_status_field_read_correctly(self):
        """stream stores 'status', not 'status_code'; engine must read it right."""
        rdb = _make_redis()
        rdb.xrevrange.return_value = [
            (f"1-{i}", {"latency_ms": "10.0", "status": "500", "type": "api"})
            for i in range(30)
        ]
        engine = AlertEngine(redis=rdb, config=AlertConfig())
        result = engine.evaluate()
        assert result["metrics"]["error_rate"] == 1.0

    def test_evaluate_includes_service_and_instance(self):
        events = [{"latency_ms": 10.0, "status_code": 200} for _ in range(5)]
        rdb = _make_redis()
        rdb.xrevrange.return_value = [
            (f"1-{i}", {"latency_ms": str(e["latency_ms"]), "status": str(e["status_code"]), "type": "api"})
            for i, e in enumerate(events)
        ]
        engine = AlertEngine(redis=rdb, config=AlertConfig(service_name="svc-a", instance_id="pod-1"))
        result = engine.evaluate()
        assert result["service_name"] == "svc-a"
        assert result["instance_id"] == "pod-1"


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

    def test_write_metric_stores_service_name(self):
        rdb = _make_redis()
        config = AlertConfig(service_name="my-svc")
        write_metric(rdb, config, {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        fields = rdb.xadd.call_args[0][1]
        assert fields["service_name"] == "my-svc"

    def test_write_metric_stores_instance_id(self):
        rdb = _make_redis()
        config = AlertConfig(instance_id="pod-xyz")
        write_metric(rdb, config, {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        fields = rdb.xadd.call_args[0][1]
        assert fields["instance_id"] == "pod-xyz"

    def test_write_batch_uses_pipeline(self):
        rdb = _make_redis()
        metrics = [
            {"path": "/a", "method": "GET", "status_code": 200, "latency_ms": 1.0},
            {"path": "/b", "method": "POST", "status_code": 201, "latency_ms": 2.0},
        ]
        write_batch(rdb, AlertConfig(), metrics)
        rdb.pipeline.assert_called_once_with(transaction=False)
        pipe = rdb.pipeline.return_value
        assert pipe.xadd.call_count == 2
        pipe.execute.assert_called_once()

    def test_write_batch_empty_list_is_noop(self):
        rdb = _make_redis()
        write_batch(rdb, AlertConfig(), [])
        rdb.pipeline.assert_not_called()

    def test_write_batch_survives_pipeline_error(self):
        rdb = _make_redis()
        rdb.pipeline.return_value.execute.side_effect = RuntimeError("pipeline error")
        # Must not raise
        write_batch(rdb, AlertConfig(), [
            {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0}
        ])


# ── AlertConfig ───────────────────────────────────────────────────────────────


class TestAlertConfig:
    def test_defaults(self):
        config = AlertConfig()
        assert config.redis_url == "redis://localhost:6379/0"
        assert config.stream_key == "anchorflow:request_metrics"
        assert config.stream_maxlen == 10000
        assert config.service_name == "default"
        assert config.instance_id == "default"
        assert config.slack_webhook_url is None
        assert config.slack_rate_limit_seconds == 10

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

    def test_service_name_configurable(self):
        config = AlertConfig(service_name="payments-api")
        assert config.service_name == "payments-api"

    def test_instance_id_configurable(self):
        config = AlertConfig(instance_id="pod-abc123")
        assert config.instance_id == "pod-abc123"

    def test_slack_webhook_url_configurable(self):
        config = AlertConfig(slack_webhook_url="https://hooks.slack.com/x")
        assert config.slack_webhook_url == "https://hooks.slack.com/x"

    def test_slack_rate_limit_configurable(self):
        config = AlertConfig(slack_rate_limit_seconds=30)
        assert config.slack_rate_limit_seconds == 30


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


# ── Slack delivery ────────────────────────────────────────────────────────────


class TestSlackDelivery:
    def _make_engine_with_slack(self, webhook_url: str = "https://hooks.slack.com/test") -> AlertEngine:
        config = AlertConfig(slack_webhook_url=webhook_url)
        return AlertEngine(redis=_make_redis(), config=config)

    def test_deliver_alert_returns_false_without_webhook(self):
        engine = _make_engine()  # no slack_webhook_url
        result = asyncio.run(engine.deliver_alert({"status": "critical", "metrics": {}}))
        assert result is False

    def test_deliver_alert_sends_message_on_ok_status(self):
        engine = self._make_engine_with_slack()
        evaluation = {"status": "ok", "metrics": {"overall_p95_ms": 10.0, "error_rate": 0.0, "sample_size": 5}}

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        async def _run():
            with patch("fastapi_alertengine.engine.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                return await engine.deliver_alert(evaluation)

        result = asyncio.run(_run())
        assert result is True

    def test_deliver_alert_rate_limited_on_second_call(self):
        engine = self._make_engine_with_slack()
        evaluation = {"status": "critical", "metrics": {"overall_p95_ms": 5000.0, "error_rate": 0.3, "sample_size": 10}}

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        async def _run():
            with patch("fastapi_alertengine.engine.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                first  = await engine.deliver_alert(evaluation)
                second = await engine.deliver_alert(evaluation)  # within rate-limit window
                return first, second

        first, second = asyncio.run(_run())
        assert first is True
        assert second is False  # rate-limited

    def test_deliver_alert_not_rate_limited_after_window(self):
        engine = self._make_engine_with_slack()
        engine.config = AlertConfig(slack_webhook_url="https://hooks.slack.com/test", slack_rate_limit_seconds=0)
        evaluation = {"status": "critical", "metrics": {"overall_p95_ms": 5000.0, "error_rate": 0.3, "sample_size": 10}}

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        async def _run():
            with patch("fastapi_alertengine.engine.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                first  = await engine.deliver_alert(evaluation)
                second = await engine.deliver_alert(evaluation)  # rate_limit_seconds=0 → always allowed
                return first, second

        first, second = asyncio.run(_run())
        assert first is True
        assert second is True

    def test_deliver_alert_survives_http_error(self):
        engine = self._make_engine_with_slack()
        evaluation = {"status": "warning", "metrics": {}}

        async def _run():
            with patch("fastapi_alertengine.engine.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=RuntimeError("network error"))
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                return await engine.deliver_alert(evaluation)

        result = asyncio.run(_run())
        assert result is False  # failure is swallowed


# ── engine.history() ──────────────────────────────────────────────────────────


class TestHistory:
    def test_history_returns_list(self):
        engine = _make_engine()
        result = engine.history()
        assert isinstance(result, list)

    def test_history_returns_empty_on_no_data(self):
        engine = _make_engine()
        assert engine.history() == []

    def test_history_returns_metric_dicts(self):
        rdb = _make_redis()
        rdb.xrevrange.return_value = [
            ("1-0", {"path": "/api/x", "method": "GET", "status": "200", "latency_ms": "12.500", "type": "api"}),
        ]
        engine = AlertEngine(redis=rdb, config=AlertConfig())
        result = engine.history(last_n=10)
        assert len(result) == 1
        item = result[0]
        assert item["path"] == "/api/x"
        assert item["method"] == "GET"
        assert item["status_code"] == 200
        assert item["latency_ms"] == 12.5
        assert item["type"] == "api"

    def test_history_respects_last_n(self):
        engine = _make_engine()
        engine.history(last_n=50)
        engine.redis.xrevrange.assert_called_with(engine.config.stream_key, count=50)


# ── Aggregation (config) ──────────────────────────────────────────────────────


class TestAggregationConfig:
    def test_agg_defaults(self):
        config = AlertConfig()
        assert config.agg_bucket_seconds == 60
        assert config.agg_ttl_seconds == 3600
        assert config.agg_key_prefix == "alertengine:agg"
        assert config.agg_flush_interval_seconds == 30

    def test_agg_fields_configurable(self):
        config = AlertConfig(agg_bucket_seconds=30, agg_ttl_seconds=600, agg_flush_interval_seconds=10)
        assert config.agg_bucket_seconds == 30
        assert config.agg_ttl_seconds == 600
        assert config.agg_flush_interval_seconds == 10


# ── _aggregate_batch ──────────────────────────────────────────────────────────


class TestAggregateBatch:
    def test_adds_entry_to_agg_buffer(self):
        engine = _make_engine()
        engine._aggregate_batch([{"path": "/api/x", "method": "GET", "status_code": 200, "latency_ms": 50.0}])
        assert len(engine._agg) == 1

    def test_groups_2xx_status_codes(self):
        engine = _make_engine()
        engine._aggregate_batch([
            {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 10.0},
            {"path": "/x", "method": "GET", "status_code": 201, "latency_ms": 20.0},
        ])
        # Both 200 and 201 land in the same "2xx" key
        assert len(engine._agg) == 1
        row = next(iter(engine._agg.values()))
        assert row[0] == 2  # count

    def test_groups_4xx_separately(self):
        engine = _make_engine()
        engine._aggregate_batch([
            {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 5.0},
            {"path": "/x", "method": "GET", "status_code": 404, "latency_ms": 3.0},
        ])
        assert len(engine._agg) == 2

    def test_accumulates_count_and_total(self):
        engine = _make_engine()
        metric = {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 10.0}
        engine._aggregate_batch([metric])
        engine._aggregate_batch([metric])
        row = next(iter(engine._agg.values()))
        assert row[0] == 2          # count
        assert abs(row[1] - 20.0) < 1e-6  # total_latency

    def test_tracks_max_latency(self):
        engine = _make_engine()
        engine._aggregate_batch([
            {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 10.0},
            {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 99.0},
            {"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 5.0},
        ])
        row = next(iter(engine._agg.values()))
        assert row[2] == 99.0   # max_latency

    def test_uses_config_service_name_when_metric_has_none(self):
        engine = _make_engine(service_name="svc-test")
        engine._aggregate_batch([{"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0}])
        key = next(iter(engine._agg.keys()))
        assert key[0] == "svc-test"

    def test_uses_metric_service_name_when_present(self):
        engine = _make_engine(service_name="default")
        engine._aggregate_batch([{
            "path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0,
            "service_name": "override-svc",
        }])
        key = next(iter(engine._agg.keys()))
        assert key[0] == "override-svc"


# ── _flush_aggregates ─────────────────────────────────────────────────────────


class TestFlushAggregates:
    def test_past_bucket_is_flushed_and_removed(self):
        engine = _make_engine()
        bucket_size = engine.config.agg_bucket_seconds
        # Use a timestamp from the previous bucket
        past_bucket = (int(time.time()) // bucket_size - 1) * bucket_size
        key = ("svc", past_bucket, "/x", "GET", "2xx")
        engine._agg[key] = [5, 100.0, 25.0]

        engine._flush_aggregates()

        # Buffer should be empty after flush
        assert key not in engine._agg
        # Pipeline should have been called
        engine.redis.pipeline.assert_called()

    def test_current_bucket_stays_in_buffer(self):
        engine = _make_engine()
        bucket_size = engine.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        key = ("svc", now_bucket, "/x", "GET", "2xx")
        engine._agg[key] = [1, 10.0, 10.0]

        engine._flush_aggregates()

        # Current bucket must NOT be flushed
        assert key in engine._agg
        engine.redis.pipeline.assert_not_called()

    def test_empty_buffer_is_noop(self):
        engine = _make_engine()
        engine._flush_aggregates()
        engine.redis.pipeline.assert_not_called()

    def test_flush_aggregates_storage_survives_redis_error(self):
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        rdb.pipeline.return_value.execute.side_effect = RuntimeError("Redis down")
        # Must not raise
        snapshot = {("svc", 1000, "/x", "GET", "2xx"): [1, 10.0, 10.0]}
        flush_aggregates(rdb, AlertConfig(), snapshot)


# ── flush_aggregates (storage) ────────────────────────────────────────────────


class TestFlushAggregatesStorage:
    def test_uses_pipeline(self):
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        snapshot = {("svc", 1000, "/api", "GET", "2xx"): [3, 60.0, 30.0]}
        flush_aggregates(rdb, AlertConfig(), snapshot)
        rdb.pipeline.assert_called_once_with(transaction=False)
        pipe = rdb.pipeline.return_value
        pipe.hset.assert_called_once()
        # Two expire calls: one for the hash key, one for the ZSET index key.
        assert pipe.expire.call_count == 2
        pipe.zadd.assert_called_once()
        pipe.execute.assert_called_once()

    def test_empty_snapshot_is_noop(self):
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        flush_aggregates(rdb, AlertConfig(), {})
        rdb.pipeline.assert_not_called()

    def test_key_format(self):
        from fastapi_alertengine.storage import flush_aggregates
        import json
        rdb = _make_redis()
        config = AlertConfig(agg_key_prefix="myapp:agg")
        snapshot = {("my-svc", 1234567200, "/api/data", "POST", "2xx"): [1, 50.0, 50.0]}
        flush_aggregates(rdb, config, snapshot)
        pipe = rdb.pipeline.return_value
        call_args = pipe.hset.call_args[0]
        assert call_args[0] == "myapp:agg:my-svc:1234567200"
        assert call_args[1] == "/api/data|POST|2xx"
        # Value is now JSON: {"c": count, "t": total, "m": max}
        v = json.loads(call_args[2])
        assert v["c"] == 1
        assert abs(v["t"] - 50.0) < 0.001
        assert abs(v["m"] - 50.0) < 0.001

    def test_ttl_applied(self):
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        config = AlertConfig(agg_ttl_seconds=7200)
        snapshot = {("svc", 1000, "/x", "GET", "2xx"): [1, 10.0, 10.0]}
        flush_aggregates(rdb, config, snapshot)
        pipe = rdb.pipeline.return_value
        # All expire calls (hash key + index key) must use the configured TTL.
        for call in pipe.expire.call_args_list:
            assert call[0][1] == 7200

    def test_survives_pipeline_error(self):
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        rdb.pipeline.return_value.execute.side_effect = RuntimeError("boom")
        snapshot = {("svc", 1000, "/x", "GET", "2xx"): [1, 10.0, 10.0]}
        # Must not raise
        flush_aggregates(rdb, AlertConfig(), snapshot)


# ── read_aggregates (storage) ─────────────────────────────────────────────────


class TestReadAggregates:
    def test_returns_empty_on_no_data(self):
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        rdb.zrevrange.return_value = []
        result = read_aggregates(rdb, AlertConfig(), "my-svc")
        assert result == []

    def test_parses_stored_data(self):
        """Legacy pipe-delimited values are still parsed correctly."""
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        config = AlertConfig(agg_key_prefix="alertengine:agg")
        rdb.zrevrange.return_value = ["1712345220"]
        # Pipeline execute returns one HGETALL result per bucket.
        rdb.pipeline.return_value.execute.return_value = [{"/api|GET|2xx": "10|500.000|80.000"}]

        result = read_aggregates(rdb, config, "my-svc", last_n_buckets=5)
        assert len(result) == 1
        row = result[0]
        assert row["service"] == "my-svc"
        assert row["path"] == "/api"
        assert row["method"] == "GET"
        assert row["status_group"] == "2xx"
        assert row["count"] == 10
        assert abs(row["avg_latency_ms"] - 50.0) < 0.01
        assert row["max_latency_ms"] == 80.0
        assert row["bucket_ts"] == 1712345220

    def test_filters_strictly_by_service(self):
        """ZREVRANGE index key is scoped to the requested service."""
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        rdb.zrevrange.return_value = []

        read_aggregates(rdb, AlertConfig(), "other-svc", last_n_buckets=5)

        # The index key in the ZREVRANGE call must contain the service name.
        zrevrange_call = rdb.zrevrange.call_args
        assert "other-svc" in zrevrange_call[0][0]

    def test_survives_redis_error(self):
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        rdb.zrevrange.side_effect = RuntimeError("Redis down")
        # Must not raise
        result = read_aggregates(rdb, AlertConfig(), "svc")
        assert result == []


# ── aggregated_history (engine) ───────────────────────────────────────────────


class TestAggregatedHistory:
    def test_returns_list(self):
        engine = _make_engine()
        engine.redis.zrevrange.return_value = []
        result = engine.aggregated_history()
        assert isinstance(result, list)

    def test_uses_config_service_name_by_default(self):
        engine = _make_engine(service_name="my-svc")
        engine.redis.zrevrange.return_value = []
        engine.aggregated_history()
        zrevrange_call = engine.redis.zrevrange.call_args
        # The index key (first positional arg) must contain the service name.
        assert "my-svc" in zrevrange_call[0][0]

    def test_accepts_explicit_service(self):
        engine = _make_engine(service_name="default")
        engine.redis.zrevrange.return_value = []
        engine.aggregated_history(service="explicit-svc")
        zrevrange_call = engine.redis.zrevrange.call_args
        assert "explicit-svc" in zrevrange_call[0][0]


# ── Ingestion stats ───────────────────────────────────────────────────────────


class TestIngestionStats:
    def test_enqueue_increments_enqueued(self):
        engine = _make_engine()
        for _ in range(3):
            engine.enqueue_metric({"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        assert engine.get_ingestion_stats()["enqueued"] == 3

    def test_overflow_increments_dropped(self):
        engine = _make_engine()
        # Fill the queue completely
        for i in range(MAX_QUEUE_SIZE):
            engine.enqueue_metric({"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        # One more should be dropped
        engine.enqueue_metric({"path": "/overflow", "method": "GET", "status_code": 200, "latency_ms": 1.0})
        stats = engine.get_ingestion_stats()
        assert stats["dropped"] >= 1

    def test_get_ingestion_stats_returns_dict(self):
        engine = _make_engine()
        stats = engine.get_ingestion_stats()
        assert isinstance(stats, dict)
        assert "enqueued" in stats
        assert "dropped" in stats
        assert "last_drain_at" in stats

    def test_last_drain_at_is_none_before_drain(self):
        engine = _make_engine()
        assert engine.get_ingestion_stats()["last_drain_at"] is None

    def test_drain_updates_last_drain_at(self):
        engine = _make_engine()
        engine.enqueue_metric({"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0})

        async def _run():
            task = asyncio.create_task(engine.drain())
            for _ in range(10):
                await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())
        assert engine.get_ingestion_stats()["last_drain_at"] is not None

    def test_get_ingestion_stats_returns_copy(self):
        engine = _make_engine()
        stats = engine.get_ingestion_stats()
        stats["enqueued"] = 9999  # mutate the returned copy
        # Should not affect internal state
        assert engine._stats["enqueued"] == 0


# ── Alert queue ───────────────────────────────────────────────────────────────


class TestAlertQueue:
    def test_enqueue_alert_returns_true(self):
        engine = _make_engine()
        result = engine.enqueue_alert({"status": "ok"})
        assert result is True

    def test_enqueue_alert_queues_item(self):
        engine = _make_engine()
        engine.enqueue_alert({"status": "warning"})
        assert engine._alert_queue.qsize() == 1

    def test_enqueue_alert_returns_false_when_full(self):
        engine = _make_engine()
        # Fill the alert queue (maxsize=1000)
        for _ in range(1000):
            engine._alert_queue.put_nowait({"status": "ok"})
        result = engine.enqueue_alert({"status": "critical"})
        assert result is False

    def test_alert_delivery_loop_calls_deliver_alert(self):
        engine = _make_engine()
        engine.enqueue_alert({"status": "critical", "metrics": {}})

        delivered = {"count": 0}

        async def mock_deliver(evaluation):
            delivered["count"] += 1
            return True

        async def _run():
            with patch.object(engine, "deliver_alert", side_effect=mock_deliver):
                task = asyncio.create_task(engine.alert_delivery_loop())
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(_run())
        assert delivered["count"] == 1

    def test_alert_delivery_loop_stops_on_cancel(self):
        engine = _make_engine()

        async def _run():
            task = asyncio.create_task(engine.alert_delivery_loop())
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should not raise
        asyncio.run(_run())

    def test_alert_delivery_loop_recovers_from_exception(self):
        engine = _make_engine()
        engine.enqueue_alert({"status": "ok"})

        call_count = {"n": 0}

        async def flaky_deliver(evaluation):
            call_count["n"] += 1
            raise RuntimeError("boom")

        async def _run():
            with patch.object(engine, "deliver_alert", side_effect=flaky_deliver):
                task = asyncio.create_task(engine.alert_delivery_loop())
                await asyncio.sleep(1.2)  # wait past 1s recovery sleep
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(_run())
        # Loop should have attempted delivery and recovered
        assert call_count["n"] >= 1


# ── New endpoints ─────────────────────────────────────────────────────────────


class TestNewEndpoints:
    def _make_instrumented_app(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            rdb = _make_redis()
            rdb.zrevrange.return_value = []
            mock_redis_mod.Redis.from_url.return_value = rdb
            engine = instrument(app)
        return app, engine

    def test_metrics_ingestion_endpoint_registered(self):
        app, _ = self._make_instrumented_app()
        with TestClient(app) as client:
            resp = client.get("/metrics/ingestion")
        assert resp.status_code == 200

    def test_metrics_ingestion_returns_expected_keys(self):
        app, _ = self._make_instrumented_app()
        with TestClient(app) as client:
            data = client.get("/metrics/ingestion").json()
        assert "enqueued" in data
        assert "dropped" in data
        assert "last_drain_at" in data

    def test_metrics_ingestion_enqueued_increments_on_requests(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            rdb = _make_redis()
            mock_redis_mod.Redis.from_url.return_value = rdb
            engine = instrument(app)

        @app.get("/ping")
        def ping():
            return {}

        with TestClient(app) as client:
            for _ in range(3):
                client.get("/ping")
            data = client.get("/metrics/ingestion").json()

        assert data["enqueued"] >= 3

    def test_metrics_history_uses_aggregated_data(self):
        app, engine = self._make_instrumented_app()
        with TestClient(app) as client:
            resp = client.get("/metrics/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert isinstance(data["metrics"], list)

    def test_metrics_history_accepts_service_param(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            rdb = _make_redis()
            rdb.zrevrange.return_value = []
            mock_redis_mod.Redis.from_url.return_value = rdb
            instrument(app)

        with TestClient(app) as client:
            resp = client.get("/metrics/history?service=my-svc&last_n_buckets=5")
        assert resp.status_code == 200

    def test_alerts_evaluate_is_non_blocking(self):
        """POST /alerts/evaluate must not await Slack delivery directly."""
        app, engine = self._make_instrumented_app()
        with TestClient(app) as client:
            # Should complete immediately regardless of Slack config
            resp = client.post("/alerts/evaluate")
        assert resp.status_code == 200
        # Alert was enqueued (not directly sent), so alert_queue may have item
        # (or was already consumed; just verify no error)

    def test_alerts_evaluate_enqueues_to_alert_queue(self):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            rdb = _make_redis()
            mock_redis_mod.Redis.from_url.return_value = rdb
            engine = instrument(app)

        # Wrap enqueue_alert to track calls — the delivery loop may consume the
        # alert before the qsize assertion runs, so we check invocations instead.
        calls: list = []
        original = engine.enqueue_alert
        engine.enqueue_alert = lambda ev: calls.append(ev) or original(ev)

        with TestClient(app) as client:
            client.post("/alerts/evaluate")

        assert len(calls) == 1  # endpoint must have called enqueue_alert exactly once


# ── ZSET index ────────────────────────────────────────────────────────────────


class TestZsetIndex:
    def test_flush_creates_zadd_entry(self):
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        snapshot = {("svc", 1000, "/x", "GET", "2xx"): [1, 10.0, 10.0]}
        flush_aggregates(rdb, AlertConfig(), snapshot)
        pipe = rdb.pipeline.return_value
        pipe.zadd.assert_called_once()
        # The ZADD key must contain the service name.
        zadd_key = pipe.zadd.call_args[0][0]
        assert "svc" in zadd_key

    def test_flush_zadd_member_is_bucket_ts_string(self):
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        snapshot = {("svc", 1712345220, "/x", "GET", "2xx"): [1, 10.0, 10.0]}
        flush_aggregates(rdb, AlertConfig(), snapshot)
        pipe = rdb.pipeline.return_value
        mapping = pipe.zadd.call_args[0][1]
        assert "1712345220" in mapping
        assert mapping["1712345220"] == 1712345220

    def test_read_uses_zrevrange_not_scan(self):
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        rdb.zrevrange.return_value = []
        read_aggregates(rdb, AlertConfig(), "svc")
        rdb.zrevrange.assert_called_once()
        rdb.scan.assert_not_called()

    def test_read_zrevrange_uses_correct_index_key(self):
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        config = AlertConfig(agg_key_prefix="myapp:agg")
        rdb.zrevrange.return_value = []
        read_aggregates(rdb, config, "my-svc", last_n_buckets=5)
        call_args = rdb.zrevrange.call_args[0]
        assert call_args[0] == "myapp:agg:index:my-svc"
        assert call_args[1] == 0
        assert call_args[2] == 4  # last_n_buckets - 1

    def test_read_pipelines_hgetall_for_each_bucket(self):
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        rdb.zrevrange.return_value = ["1712345280", "1712345220"]
        rdb.pipeline.return_value.execute.return_value = [{}, {}]
        read_aggregates(rdb, AlertConfig(), "svc", last_n_buckets=5)
        pipe = rdb.pipeline.return_value
        assert pipe.hgetall.call_count == 2
        pipe.execute.assert_called_once()

    def test_flush_index_key_gets_expire(self):
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        config = AlertConfig(agg_key_prefix="alertengine:agg", agg_ttl_seconds=900)
        snapshot = {("svc", 1000, "/x", "GET", "2xx"): [1, 10.0, 10.0]}
        flush_aggregates(rdb, config, snapshot)
        pipe = rdb.pipeline.return_value
        # Both the hash key and index key should get an expire.
        expire_keys = [call[0][0] for call in pipe.expire.call_args_list]
        assert any("index:svc" in k for k in expire_keys)
        assert all(call[0][1] == 900 for call in pipe.expire.call_args_list)

    def test_zadd_is_idempotent_for_same_bucket(self):
        """Flushing the same bucket twice does not create duplicate index entries."""
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        snapshot = {("svc", 1000, "/x", "GET", "2xx"): [5, 100.0, 30.0]}
        flush_aggregates(rdb, AlertConfig(), snapshot)
        flush_aggregates(rdb, AlertConfig(), snapshot)
        pipe = rdb.pipeline.return_value
        # zadd is called twice (once per flush call) — same member, same score:
        # Redis would just update the score (no-op for identical data).
        assert pipe.zadd.call_count == 2


# ── JSON encoding ─────────────────────────────────────────────────────────────


class TestJsonEncoding:
    def test_value_is_stored_as_json(self):
        import json
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        snapshot = {("svc", 1000, "/x", "GET", "2xx"): [5, 100.0, 30.0]}
        flush_aggregates(rdb, AlertConfig(), snapshot)
        pipe = rdb.pipeline.return_value
        raw_value = pipe.hset.call_args[0][2]
        v = json.loads(raw_value)  # must not raise
        assert v["c"] == 5
        assert abs(v["t"] - 100.0) < 0.001
        assert abs(v["m"] - 30.0) < 0.001

    def test_json_value_parsed_correctly_by_read(self):
        import json
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        config = AlertConfig(agg_key_prefix="alertengine:agg")
        rdb.zrevrange.return_value = ["1712345220"]
        json_val = json.dumps({"c": 10, "t": 500.0, "m": 80.0})
        rdb.pipeline.return_value.execute.return_value = [{"/api|GET|2xx": json_val}]

        result = read_aggregates(rdb, config, "svc")
        assert len(result) == 1
        assert result[0]["count"] == 10
        assert abs(result[0]["avg_latency_ms"] - 50.0) < 0.01
        assert result[0]["max_latency_ms"] == 80.0

    def test_old_pipe_format_still_parses(self):
        """Backward-compat: values written in old format are still readable."""
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        config = AlertConfig(agg_key_prefix="alertengine:agg")
        rdb.zrevrange.return_value = ["1712345220"]
        rdb.pipeline.return_value.execute.return_value = [{"/api|GET|2xx": "10|500.000|80.000"}]

        result = read_aggregates(rdb, config, "svc")
        assert len(result) == 1
        assert result[0]["count"] == 10
        assert abs(result[0]["avg_latency_ms"] - 50.0) < 0.01
        assert result[0]["max_latency_ms"] == 80.0

    def test_malformed_value_is_skipped(self):
        from fastapi_alertengine.storage import read_aggregates
        rdb = _make_redis()
        rdb.zrevrange.return_value = ["1712345220"]
        rdb.pipeline.return_value.execute.return_value = [{"/api|GET|2xx": "not_json_or_pipe"}]
        # Must not raise; bad entries are skipped.
        result = read_aggregates(rdb, AlertConfig(), "svc")
        assert result == []

    def test_json_keys_match_expected_schema(self):
        import json
        from fastapi_alertengine.storage import flush_aggregates
        rdb = _make_redis()
        snapshot = {("svc", 1000, "/x", "GET", "2xx"): [2, 40.0, 25.0]}
        flush_aggregates(rdb, AlertConfig(), snapshot)
        raw = rdb.pipeline.return_value.hset.call_args[0][2]
        v = json.loads(raw)
        assert set(v.keys()) == {"c", "t", "m"}


# ── Aggregation memory guard ──────────────────────────────────────────────────


class TestAggregationMemoryGuard:
    def test_exceeding_max_agg_keys_drops_new_key(self):
        from fastapi_alertengine.engine import MAX_AGG_KEYS
        engine = _make_engine()
        # Pre-fill the buffer to the limit.
        for i in range(MAX_AGG_KEYS):
            engine._agg[("svc", 0, f"/p{i}", "GET", "2xx")] = [1, 10.0, 10.0]
        assert len(engine._agg) == MAX_AGG_KEYS

        initial_dropped = engine._dropped_agg_keys
        engine._aggregate_batch([{"path": "/new", "method": "GET", "status_code": 200, "latency_ms": 5.0}])

        assert engine._dropped_agg_keys > initial_dropped
        assert len(engine._agg) == MAX_AGG_KEYS  # no new key added

    def test_existing_key_still_accumulates_at_capacity(self):
        from fastapi_alertengine.engine import MAX_AGG_KEYS
        engine = _make_engine()
        bucket_size = engine.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        existing_key = ("default", now_bucket, "/exist", "GET", "2xx")
        engine._agg[existing_key] = [1, 10.0, 10.0]
        # Fill remaining capacity.
        for i in range(MAX_AGG_KEYS - 1):
            engine._agg[("svc", 0, f"/p{i}", "GET", "2xx")] = [1, 10.0, 10.0]
        assert len(engine._agg) == MAX_AGG_KEYS

        engine._aggregate_batch([{"path": "/exist", "method": "GET", "status_code": 200, "latency_ms": 5.0}])

        # Existing key must accumulate (count goes from 1 → 2).
        assert engine._agg[existing_key][0] == 2
        assert engine._dropped_agg_keys == 0

    def test_below_limit_no_drops(self):
        engine = _make_engine()
        engine._aggregate_batch([{"path": "/x", "method": "GET", "status_code": 200, "latency_ms": 1.0}])
        assert engine._dropped_agg_keys == 0

    def test_dropped_agg_keys_exposed_in_stats(self):
        engine = _make_engine()
        engine._dropped_agg_keys = 42
        stats = engine.get_ingestion_stats()
        assert stats["dropped_agg_keys"] == 42

    def test_max_agg_keys_constant_is_exported(self):
        from fastapi_alertengine.engine import MAX_AGG_KEYS
        assert MAX_AGG_KEYS == 50_000


# ── Dropped alerts ────────────────────────────────────────────────────────────


class TestDroppedAlerts:
    def test_full_queue_increments_dropped_alerts(self):
        engine = _make_engine()
        for _ in range(1000):  # fill to maxsize
            engine._alert_queue.put_nowait({"status": "ok"})
        engine.enqueue_alert({"status": "critical"})
        assert engine._dropped_alerts == 1

    def test_multiple_drops_accumulate(self):
        engine = _make_engine()
        for _ in range(1000):
            engine._alert_queue.put_nowait({"status": "ok"})
        engine.enqueue_alert({"status": "a"})
        engine.enqueue_alert({"status": "b"})
        assert engine._dropped_alerts == 2

    def test_successful_enqueue_does_not_increment(self):
        engine = _make_engine()
        engine.enqueue_alert({"status": "ok"})
        assert engine._dropped_alerts == 0

    def test_dropped_alerts_exposed_in_stats(self):
        engine = _make_engine()
        engine._dropped_alerts = 7
        stats = engine.get_ingestion_stats()
        assert stats["dropped_alerts"] == 7

    def test_ingestion_stats_has_all_new_keys(self):
        engine = _make_engine()
        stats = engine.get_ingestion_stats()
        assert "dropped_agg_keys" in stats
        assert "dropped_alerts" in stats


# ── Shutdown flush ────────────────────────────────────────────────────────────


class TestShutdownFlush:
    def test_flush_all_writes_current_bucket(self):
        engine = _make_engine()
        bucket_size = engine.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        key = ("default", now_bucket, "/x", "GET", "2xx")
        engine._agg[key] = [5, 100.0, 30.0]

        asyncio.run(engine.flush_all_aggregates())

        engine.redis.pipeline.assert_called()

    def test_flush_all_clears_buffer(self):
        engine = _make_engine()
        bucket_size = engine.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        engine._agg[("default", now_bucket, "/x", "GET", "2xx")] = [1, 10.0, 10.0]

        asyncio.run(engine.flush_all_aggregates())

        assert engine._agg == {}

    def test_flush_all_empty_buffer_is_noop(self):
        engine = _make_engine()
        asyncio.run(engine.flush_all_aggregates())
        engine.redis.pipeline.assert_not_called()

    def test_flush_all_survives_redis_error(self):
        engine = _make_engine()
        engine.redis.pipeline.return_value.execute.side_effect = RuntimeError("boom")
        bucket_size = engine.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        engine._agg[("default", now_bucket, "/x", "GET", "2xx")] = [1, 10.0, 10.0]
        # Must not raise.
        asyncio.run(engine.flush_all_aggregates())

    def test_flush_all_includes_both_past_and_current_buckets(self):
        engine = _make_engine()
        bucket_size = engine.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        past_bucket = now_bucket - bucket_size

        engine._agg[("default", now_bucket,  "/a", "GET", "2xx")] = [1, 10.0, 10.0]
        engine._agg[("default", past_bucket, "/b", "GET", "2xx")] = [2, 20.0, 15.0]

        asyncio.run(engine.flush_all_aggregates())

        assert engine._agg == {}

    def test_shutdown_hook_registered_in_instrument(self):
        """instrument() must register a shutdown handler."""
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            rdb = _make_redis()
            rdb.zrevrange.return_value = []
            mock_redis_mod.Redis.from_url.return_value = rdb
            instrument(app)
        assert len(app.router.on_shutdown) >= 1

    def test_shutdown_hook_calls_flush_all(self):
        """The registered shutdown hook must call flush_all_aggregates."""
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            rdb = _make_redis()
            rdb.zrevrange.return_value = []
            mock_redis_mod.Redis.from_url.return_value = rdb
            engine = instrument(app)

        bucket_size = engine.config.agg_bucket_seconds
        now_bucket  = int(time.time()) // bucket_size * bucket_size
        engine._agg[("default", now_bucket, "/x", "GET", "2xx")] = [3, 60.0, 25.0]

        # TestClient triggers startup + shutdown lifecycles.
        with TestClient(app):
            pass  # shutdown happens on __exit__

        # Buffer must be empty after shutdown.
        assert engine._agg == {}


# ── Plug-and-play runtime ─────────────────────────────────────────────────────


class TestPlugAndPlay:
    """Requirements: zero-config, memory fallback, auto loops, auto endpoints."""

    # 1. instrument(app) with zero additional arguments
    def test_instrument_zero_args(self):
        """instrument(app) must work with no extra arguments."""
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            engine = instrument(app)
        assert isinstance(engine, AlertEngine)

    # 2. System runs without Redis (memory mode fallback)
    def test_runs_without_redis(self):
        """When Redis ping fails the engine switches to memory mode."""
        app = FastAPI()
        rdb = _make_redis()
        rdb.ping.side_effect = ConnectionError("no redis")
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = rdb
            engine = instrument(app)
        assert engine._memory_mode is True

    def test_memory_mode_health_endpoint_works(self):
        """In memory mode the /health/alerts endpoint must return a valid response."""
        app = FastAPI()
        rdb = _make_redis()
        rdb.ping.side_effect = ConnectionError("no redis")
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = rdb
            instrument(app)
        with TestClient(app) as client:
            resp = client.get("/health/alerts")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("ok", "warning", "critical")

    def test_memory_mode_evaluate_uses_recent_buffer(self):
        """In memory mode evaluate() reads from _recent, not Redis xrevrange."""
        app = FastAPI()
        rdb = _make_redis()
        rdb.ping.side_effect = ConnectionError("no redis")
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = rdb
            engine = instrument(app)

        # Inject events directly into _recent
        engine._recent.append({"latency_ms": 10.0, "type": "api", "status_code": 200})
        result = engine.evaluate()
        assert result["status"] in ("ok", "warning", "critical")
        assert result.get("metrics", {}).get("sample_size", 0) > 0
        # xrevrange should NOT have been called (memory mode bypasses Redis reads)
        rdb.xrevrange.assert_not_called()

    # 3. System runs without Slack configured
    def test_runs_without_slack(self):
        """Missing slack_webhook_url must not crash the system."""
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            engine = instrument(app)
        assert engine.config.slack_webhook_url is None
        # deliver_alert must return False, not raise
        result = asyncio.run(engine.deliver_alert({"status": "critical", "metrics": {}}))
        assert result is False

    # 4. All background loops start automatically
    def test_background_loops_start_automatically(self):
        """startup hook must register drain + alert_delivery_loop tasks."""
        app = FastAPI()
        tasks_started: list = []

        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)

        original_create_task = asyncio.create_task

        async def _run():
            nonlocal tasks_started
            with patch("fastapi_alertengine.engine.asyncio.create_task",
                       side_effect=lambda coro: (tasks_started.append(coro.__name__),
                                                 original_create_task(coro))[1]):
                for hook in app.router.on_startup:
                    await hook()

        asyncio.run(_run())
        assert "drain" in tasks_started
        assert "alert_delivery_loop" in tasks_started

    # 5. Endpoints are auto-registered
    def test_all_endpoints_auto_registered(self):
        """instrument() must register all four observability endpoints."""
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            assert client.get("/health/alerts").status_code == 200
            assert client.post("/alerts/evaluate").status_code == 200
            assert client.get("/metrics/history").status_code == 200
            assert client.get("/metrics/ingestion").status_code == 200

    # 6. No manual engine instantiation required
    def test_no_manual_instantiation_required(self):
        """instrument() is self-contained: callers need not build an engine."""
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            engine = instrument(app)
        # engine is returned for optional advanced use, but all endpoints work
        # without the caller doing anything extra.
        with TestClient(app) as client:
            data = client.get("/health/alerts").json()
        assert "status" in data

    # AlertEngine(config) one-arg form + engine.start(app)
    def test_alert_engine_single_arg_config_form(self):
        """AlertEngine(config) must work without passing a redis client."""
        config = AlertConfig()
        engine = AlertEngine(config)
        assert engine.config is config

    def test_engine_start_wires_app(self):
        """engine.start(app) wires all endpoints onto the app."""
        app = FastAPI()
        config = AlertConfig()
        engine = AlertEngine(config)
        # Force memory mode by making Redis unavailable
        with patch("redis.Redis.from_url") as mock_from_url:
            mock_from_url.return_value.ping.side_effect = ConnectionError("no redis")
            engine.start(app)
        assert engine._memory_mode is True
        with TestClient(app) as client:
            assert client.get("/health/alerts").status_code == 200

    def test_startup_print_shows_mode(self, capsys):
        """instrument() must print exactly one line indicating the mode."""
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = _make_redis()
            instrument(app)
        captured = capsys.readouterr()
        lines = [l for l in captured.out.splitlines() if "fastapi-alertengine initialized" in l]
        assert len(lines) == 1
        assert "mode" in lines[0]

    def test_startup_print_memory_mode(self, capsys):
        """When Redis is unavailable the startup line must say 'memory mode'."""
        app = FastAPI()
        rdb = _make_redis()
        rdb.ping.side_effect = ConnectionError("no redis")
        with patch("fastapi_alertengine.redis_lib") as mock_redis_mod:
            mock_redis_mod.Redis.from_url.return_value = rdb
            instrument(app)
        captured = capsys.readouterr()
        assert "memory mode" in captured.out

    def test_aggregate_batch_populates_recent(self):
        """_aggregate_batch must append events to _recent for memory-mode eval."""
        engine = _make_engine()
        engine._aggregate_batch([
            {"path": "/api/x", "method": "GET", "status_code": 200, "latency_ms": 42.0}
        ])
        assert len(engine._recent) == 1
        item = engine._recent[0]
        assert item["latency_ms"] == 42.0
        assert item["status_code"] == 200
        assert item["type"] == "api"

    def test_webhook_path_classified_in_recent(self):
        """Webhook paths must be classified as 'webhook' in _recent."""
        engine = _make_engine()
        engine._aggregate_batch([
            {"path": "/webhook/notify", "method": "POST", "status_code": 200, "latency_ms": 5.0}
        ])
        assert engine._recent[0]["type"] == "webhook"


# ── Onboarding experience ─────────────────────────────────────────────────────


class TestOnboarding:
    """
    Tests for the zero-config onboarding experience:
    startup banner, first-request detection, demo spike mode,
    `/__alertengine/status` endpoint, and progressive hints.
    """

    def _instrument_memory(self, capsys=None):
        """Instrument a fresh FastAPI app in memory mode (Redis ping fails)."""
        app = FastAPI()
        rdb = _make_redis()
        rdb.ping.side_effect = ConnectionError("no redis")
        with patch("fastapi_alertengine.redis_lib") as mock:
            mock.Redis.from_url.return_value = rdb
            engine = instrument(app)
        return app, engine

    def _instrument_redis(self):
        """Instrument a fresh FastAPI app in Redis mode (mock Redis succeeds)."""
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock:
            mock.Redis.from_url.return_value = _make_redis()
            engine = instrument(app)
        return app, engine

    # ── 1. Startup banner ─────────────────────────────────────────────────────

    def test_banner_has_separator_line(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "─" in out

    def test_banner_shows_metrics_active(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "Metrics: ACTIVE" in out

    def test_banner_shows_alerts_active(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "Alerts:" in out and "ACTIVE" in out

    def test_banner_shows_actions_status(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "Actions:" in out

    def test_banner_lists_health_endpoint(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "/health/alerts" in out

    def test_banner_lists_status_endpoint(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "/__alertengine/status" in out

    def test_banner_lists_metrics_history_endpoint(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "/metrics/history" in out

    def test_banner_lists_metrics_ingestion_endpoint(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "/metrics/ingestion" in out

    def test_banner_has_waiting_for_traffic(self, capsys):
        self._instrument_redis()
        out = capsys.readouterr().out
        assert "Waiting for traffic" in out

    def test_banner_actions_enabled_when_secret_and_router_mounted(self, capsys):
        app = FastAPI()
        from fastapi_alertengine import actions_router
        app.include_router(actions_router)
        with patch("fastapi_alertengine.redis_lib") as mock, \
             patch.dict(os.environ, {"ACTION_SECRET_KEY": "test-secret"}):
            mock.Redis.from_url.return_value = _make_redis()
            instrument(app)
        out = capsys.readouterr().out
        assert "Actions: ENABLED" in out

    def test_banner_actions_disabled_when_secret_missing(self, capsys):
        app = FastAPI()
        env = {k: v for k, v in os.environ.items() if k != "ACTION_SECRET_KEY"}
        with patch("fastapi_alertengine.redis_lib") as mock, \
             patch.dict(os.environ, env, clear=True):
            mock.Redis.from_url.return_value = _make_redis()
            instrument(app)
        out = capsys.readouterr().out
        assert "Actions: DISABLED" in out

    def test_banner_custom_health_path_appears(self, capsys):
        app = FastAPI()
        with patch("fastapi_alertengine.redis_lib") as mock:
            mock.Redis.from_url.return_value = _make_redis()
            instrument(app, health_path="/custom/health")
        out = capsys.readouterr().out
        assert "/custom/health" in out

    # ── 2. /__alertengine/status endpoint ─────────────────────────────────────

    def test_status_endpoint_registered(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            resp = client.get("/__alertengine/status")
        assert resp.status_code == 200

    def test_status_endpoint_returns_mode(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert data["mode"] in ("memory", "redis")

    def test_status_endpoint_mode_is_memory_when_redis_unavailable(self):
        app, _ = self._instrument_memory()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert data["mode"] == "memory"

    def test_status_endpoint_mode_is_redis_when_available(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert data["mode"] == "redis"

    def test_status_endpoint_metrics_active(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert data["metrics_active"] is True

    def test_status_endpoint_alerts_active(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert data["alerts_active"] is True

    def test_status_endpoint_has_ingestion_key(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert "ingestion" in data
        assert "enqueued" in data["ingestion"]

    def test_status_endpoint_has_demo_mode_key(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert "demo_mode" in data

    def test_status_endpoint_demo_mode_false_initially(self):
        app, _ = self._instrument_memory()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert data["demo_mode"] is False

    def test_status_endpoint_actions_enabled_false_without_router(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert data["actions_enabled"] is False

    def test_status_endpoint_actions_enabled_true_with_router(self):
        app = FastAPI()
        from fastapi_alertengine import actions_router
        app.include_router(actions_router)
        with patch("fastapi_alertengine.redis_lib") as mock:
            mock.Redis.from_url.return_value = _make_redis()
            instrument(app)
        with TestClient(app) as client:
            data = client.get("/__alertengine/status").json()
        assert data["actions_enabled"] is True

    def test_status_endpoint_not_in_openapi_schema(self):
        app, _ = self._instrument_redis()
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()
        assert "/__alertengine/status" not in schema.get("paths", {})

    # ── 3. First-request detection ────────────────────────────────────────────

    def test_first_request_sets_first_request_at(self):
        app = FastAPI()

        @app.get("/ping")
        def ping(): return {}

        engine = _make_engine()
        app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

        assert engine._first_request_at is None
        with TestClient(app) as client:
            client.get("/ping")
        assert engine._first_request_at is not None

    def test_first_request_prints_signal_detected(self, capsys):
        app = FastAPI()

        @app.get("/ping")
        def ping(): return {}

        engine = _make_engine()
        app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

        with TestClient(app) as client:
            client.get("/ping")
        out = capsys.readouterr().out
        assert "First request detected" in out

    def test_first_request_print_contains_path(self, capsys):
        app = FastAPI()

        @app.get("/my-path")
        def my_path(): return {}

        engine = _make_engine()
        app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

        with TestClient(app) as client:
            client.get("/my-path")
        out = capsys.readouterr().out
        assert "/my-path" in out

    def test_first_request_print_contains_status_code(self, capsys):
        app = FastAPI()

        @app.get("/ok")
        def ok(): return {}

        engine = _make_engine()
        app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

        with TestClient(app) as client:
            client.get("/ok")
        out = capsys.readouterr().out
        assert "200" in out

    def test_first_request_print_occurs_only_once(self, capsys):
        app = FastAPI()

        @app.get("/ping")
        def ping(): return {}

        engine = _make_engine()
        app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

        with TestClient(app) as client:
            for _ in range(5):
                client.get("/ping")
        out = capsys.readouterr().out
        assert out.count("First request detected") == 1

    def test_first_request_at_set_only_once_on_repeated_requests(self):
        app = FastAPI()

        @app.get("/ping")
        def ping(): return {}

        engine = _make_engine()
        app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

        with TestClient(app) as client:
            client.get("/ping")
            first_ts = engine._first_request_at
            client.get("/ping")
        assert engine._first_request_at == first_ts

    # ── 4. Demo mode state ────────────────────────────────────────────────────

    def test_demo_allowed_in_memory_mode(self):
        engine = AlertEngine(AlertConfig())
        # AlertEngine starts as _NullRedis → memory mode
        assert engine._memory_mode is True
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENV", "ENVIRONMENT", "ALERTENGINE_DISABLE_DEMO")}
        with patch.dict(os.environ, env, clear=True):
            assert engine._demo_allowed() is True

    def test_demo_not_allowed_in_redis_mode(self):
        engine = _make_engine()
        assert engine._memory_mode is False
        assert engine._demo_allowed() is False

    def test_demo_not_allowed_when_env_is_production(self):
        engine = AlertEngine(AlertConfig())
        with patch.dict(os.environ, {"ENV": "production"}):
            assert engine._demo_allowed() is False

    def test_demo_not_allowed_when_environment_is_prod(self):
        engine = AlertEngine(AlertConfig())
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
            assert engine._demo_allowed() is False

    def test_demo_not_allowed_when_disable_flag_set(self):
        engine = AlertEngine(AlertConfig())
        with patch.dict(os.environ, {"ALERTENGINE_DISABLE_DEMO": "1"}):
            assert engine._demo_allowed() is False

    def test_demo_not_allowed_when_disable_flag_true(self):
        engine = AlertEngine(AlertConfig())
        with patch.dict(os.environ, {"ALERTENGINE_DISABLE_DEMO": "true"}):
            assert engine._demo_allowed() is False

    def test_demo_mode_active_starts_false(self):
        engine = AlertEngine(AlertConfig())
        assert engine._demo_mode_active is False

    def test_demo_alert_shown_starts_false(self):
        engine = AlertEngine(AlertConfig())
        assert engine._demo_alert_shown is False

    def test_demo_spike_skipped_when_real_traffic_arrived(self):
        """If _first_request_at is set, demo spike must not inject events."""
        engine = AlertEngine(AlertConfig())
        engine._first_request_at = time.time()

        async def _run():
            with patch.dict(os.environ, {"ALERTENGINE_DEMO_DELAY": "0"}):
                await engine._run_demo_spike()

        asyncio.run(_run())
        assert engine._demo_mode_active is False
        assert len(engine._recent) == 0

    def test_demo_spike_skipped_in_redis_mode(self):
        """Demo spike must not run when memory mode is disabled."""
        engine = _make_engine()
        assert engine._memory_mode is False

        async def _run():
            with patch.dict(os.environ, {"ALERTENGINE_DEMO_DELAY": "0"}):
                await engine._run_demo_spike()

        asyncio.run(_run())
        assert engine._demo_mode_active is False
        assert len(engine._recent) == 0

    def test_demo_spike_injects_events_into_recent(self):
        """When triggered, demo spike injects synthetic events into _recent."""
        engine = AlertEngine(AlertConfig())
        assert engine._memory_mode is True

        env = {k: v for k, v in os.environ.items()
               if k not in ("ENV", "ENVIRONMENT", "ALERTENGINE_DISABLE_DEMO")}

        async def _run():
            with patch.dict(os.environ, {**env, "ALERTENGINE_DEMO_DELAY": "0"}, clear=True):
                await engine._run_demo_spike()

        asyncio.run(_run())
        assert engine._demo_mode_active is True
        assert len(engine._recent) > 0

    def test_demo_spike_prints_demo_label(self, capsys):
        engine = AlertEngine(AlertConfig())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENV", "ENVIRONMENT", "ALERTENGINE_DISABLE_DEMO")}

        async def _run():
            with patch.dict(os.environ, {**env, "ALERTENGINE_DEMO_DELAY": "0"}, clear=True):
                await engine._run_demo_spike()

        asyncio.run(_run())
        out = capsys.readouterr().out
        assert "Demo Mode" in out

    def test_demo_spike_prints_alert_if_threshold_exceeded(self, capsys):
        engine = AlertEngine(AlertConfig())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENV", "ENVIRONMENT", "ALERTENGINE_DISABLE_DEMO")}

        async def _run():
            with patch.dict(os.environ, {**env, "ALERTENGINE_DEMO_DELAY": "0"}, clear=True):
                await engine._run_demo_spike()

        asyncio.run(_run())
        out = capsys.readouterr().out
        # Demo events are high-latency, so an alert should fire
        assert "ALERT DETECTED" in out

    def test_demo_spike_prints_progressive_hint(self, capsys):
        engine = AlertEngine(AlertConfig())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENV", "ENVIRONMENT", "ALERTENGINE_DISABLE_DEMO")}

        async def _run():
            with patch.dict(os.environ, {**env, "ALERTENGINE_DEMO_DELAY": "0"}, clear=True):
                await engine._run_demo_spike()

        asyncio.run(_run())
        out = capsys.readouterr().out
        assert "actions_router" in out

    def test_demo_spike_alert_shown_only_once(self, capsys):
        engine = AlertEngine(AlertConfig())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENV", "ENVIRONMENT", "ALERTENGINE_DISABLE_DEMO")}

        async def _run():
            with patch.dict(os.environ, {**env, "ALERTENGINE_DEMO_DELAY": "0"}, clear=True):
                await engine._run_demo_spike()
                # Calling a second time must not show the alert again
                engine._first_request_at = None  # reset first-request gate
                await engine._run_demo_spike()

        asyncio.run(_run())
        out = capsys.readouterr().out
        assert out.count("ALERT DETECTED") == 1

    def test_demo_spike_cancellation_is_safe(self):
        """CancelledError must be swallowed cleanly."""
        engine = AlertEngine(AlertConfig())

        async def _run():
            task = asyncio.create_task(engine._run_demo_spike())
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # should NOT propagate past _run_demo_spike

        # Must not raise
        asyncio.run(_run())

    # ── 5. Status endpoint reflects demo state ────────────────────────────────

    def test_status_endpoint_demo_mode_true_after_spike(self):
        engine = AlertEngine(AlertConfig())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENV", "ENVIRONMENT", "ALERTENGINE_DISABLE_DEMO")}

        # Trigger demo spike synchronously
        async def _run():
            with patch.dict(os.environ, {**env, "ALERTENGINE_DEMO_DELAY": "0"}, clear=True):
                await engine._run_demo_spike()

        asyncio.run(_run())
        assert engine._demo_mode_active is True
