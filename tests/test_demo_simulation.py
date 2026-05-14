import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from fastapi import FastAPI
from fastapi_alertengine import instrument

def make_app(env="development"):
    app = FastAPI()
    with patch.dict(os.environ,
                    {"ENVIRONMENT": env}):
        instrument(app)
    return app, env

def test_demo_returns_200_in_development():
    with patch.dict(os.environ,
                    {"ENVIRONMENT": "development",
                     "ALERTENGINE_DISABLE_DEMO": "false"}):
        app = FastAPI()
        instrument(app)
        client = TestClient(app)
        res = client.post("/demo/simulate", json={
            "scenario": "latency_spike",
            "intensity": "moderate"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["injected_samples"] > 0
        assert data["monitor_at"] == "/health/alerts"

def test_demo_disabled_in_production():
    with patch.dict(os.environ, {
        "ENVIRONMENT": "production",
        "ALERTENGINE_ENABLE_DEMO": "false",
        "ALERTENGINE_DISABLE_DEMO": "false",
    }):
        app = FastAPI()
        instrument(app)
        client = TestClient(app)
        res = client.post("/demo/simulate", json={
            "scenario": "latency_spike"
        })
        assert res.status_code == 403

def test_invalid_scenario_returns_422():
    with patch.dict(os.environ,
                    {"ENVIRONMENT": "development"}):
        app = FastAPI()
        instrument(app)
        client = TestClient(app)
        res = client.post("/demo/simulate", json={
            "scenario": "not_a_real_scenario"
        })
        assert res.status_code == 422

def test_recovery_scenario_accepted():
    with patch.dict(os.environ,
                    {"ENVIRONMENT": "development"}):
        app = FastAPI()
        instrument(app)
        client = TestClient(app)
        res = client.post("/demo/simulate", json={
            "scenario": "recovery",
            "intensity": "severe"
        })
        assert res.status_code == 200
