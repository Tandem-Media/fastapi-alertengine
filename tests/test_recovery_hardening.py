import asyncio
import importlib
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"


@contextmanager
def _orchestrator_path():
    sys.path.insert(0, str(ORCHESTRATOR_DIR))
    try:
        yield
    finally:
        sys.path = [p for p in sys.path if p != str(ORCHESTRATOR_DIR)]


def _import_orchestrator_module(name: str):
    with _orchestrator_path():
        with patch.dict(sys.modules, {"uvicorn": MagicMock()}):
            module = importlib.import_module(name)
    return module


def test_recover_action_success():
    main = _import_orchestrator_module("main")
    payload = {"incident_id": "inc-123", "tenant_id": "tenant-42", "action": "restart"}

    with _orchestrator_path():
        with patch("action_generator.validate_and_consume", return_value=(True, payload, "ok")):
            result = asyncio.run(main.recover_action("tok"))

    assert result["authorized"] is True
    assert result["incident_id"] == "inc-123"
    assert result["tenant_id"] == "tenant-42"
    assert result["action"] == "restart"
    assert isinstance(result["authorized_at"], float)
    assert result["message"] == "Recovery action authorized. System will execute fix."


def test_recover_action_invalid_token():
    main = _import_orchestrator_module("main")

    with _orchestrator_path():
        with patch("action_generator.validate_and_consume", return_value=(False, None, "bad token")):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(main.recover_action("tok"))

    assert exc.value.status_code == 401
    assert exc.value.detail == "bad token"


def test_onboard_test_incident_passes_tenant_id_to_token_generation():
    onboard = _import_orchestrator_module("onboard")
    tenant_id = "tenant-42"
    generated_token = MagicMock(return_value="token-123")
    fake_pipeline = SimpleNamespace(
        open_incident=lambda incident_id, *_args: {"incident_id": incident_id},
        decide_new_incident=lambda *_args: {"decision": "open"},
        validate_decision_schema=lambda _decision: (True, "ok"),
    )
    fake_memory = SimpleNamespace(
        save_incident=lambda _incident: None,
        get_active_incident=lambda tenant_id=None: None,
    )
    fake_notifications = SimpleNamespace(
        fire=lambda _msg: None,
        send_detection=lambda *_args: {"kind": "detection"},
        send_validation=lambda *_args: {"kind": "validation"},
    )
    fake_action_generator = SimpleNamespace(generate_recovery_token=generated_token)

    with (
        patch.object(onboard, "get_tenant", return_value={"status": "active", "plan": "solo"}),
        patch.object(onboard, "get_tenant_plan", return_value=SimpleNamespace(name="solo")),
        patch.object(onboard, "incident_quota_remaining", return_value=1),
        patch.object(onboard, "get_verified_numbers", return_value=["whatsapp:+15555550123"]),
        patch.dict(
            sys.modules,
            {
                "pipeline": fake_pipeline,
                "memory": fake_memory,
                "notifications": fake_notifications,
                "action_generator": fake_action_generator,
            },
        ),
    ):
        result = asyncio.run(onboard.test_incident(tenant_id))

    args, kwargs = generated_token.call_args
    assert args and args[0].startswith(f"test-{tenant_id}-")
    assert kwargs == {"tenant_id": tenant_id}
    assert result["recovery_url"].endswith("token-123")


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
