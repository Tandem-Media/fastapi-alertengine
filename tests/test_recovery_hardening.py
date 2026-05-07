import asyncio
import importlib
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"


def _import_orchestrator_module(name: str):
    sys.path.insert(0, str(ORCHESTRATOR_DIR))
    try:
        with patch.dict(sys.modules, {"uvicorn": MagicMock()}):
            module = importlib.import_module(name)
    finally:
        sys.path = [p for p in sys.path if p != str(ORCHESTRATOR_DIR)]
    return module


def test_recover_action_success():
    main = _import_orchestrator_module("main")
    payload = {"incident_id": "inc-123", "tenant_id": "tenant-42", "action": "restart"}

    sys.path.insert(0, str(ORCHESTRATOR_DIR))
    try:
        with patch("action_generator.validate_and_consume", return_value=(True, payload, "ok")):
            result = asyncio.run(main.recover_action("tok"))
    finally:
        sys.path = [p for p in sys.path if p != str(ORCHESTRATOR_DIR)]

    assert result["authorized"] is True
    assert result["incident_id"] == "inc-123"
    assert result["tenant_id"] == "tenant-42"
    assert result["action"] == "restart"
    assert isinstance(result["authorized_at"], float)
    assert result["message"] == "Recovery action authorized. System will execute fix."


def test_recover_action_invalid_token():
    main = _import_orchestrator_module("main")

    sys.path.insert(0, str(ORCHESTRATOR_DIR))
    try:
        with patch("action_generator.validate_and_consume", return_value=(False, None, "bad token")):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(main.recover_action("tok"))
    finally:
        sys.path = [p for p in sys.path if p != str(ORCHESTRATOR_DIR)]

    assert exc.value.status_code == 401
    assert exc.value.detail == "bad token"


def test_onboard_test_incident_passes_tenant_id_to_token_generation():
    onboard = _import_orchestrator_module("onboard")
    source = inspect.getsource(onboard.test_incident)
    assert "generate_recovery_token(incident_id, tenant_id=tenant_id)" in source


def test_env_example_documents_action_base_url_primary_and_alertengine_fallback():
    env_example = (
        Path(__file__).resolve().parents[1]
        / "orchestrator"
        / ".env.example"
    ).read_text(encoding="utf-8")

    assert "ACTION_BASE_URL" in env_example
    assert "primary base URL for recovery links" in env_example
    assert "ALERTENGINE_BASE_URL" in env_example
    assert "fallback base URL for recovery links" in env_example
