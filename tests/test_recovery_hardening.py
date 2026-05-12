import ast
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"


@pytest.fixture
def orchestrator_path():
    path = str(ORCHESTRATOR_DIR)
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path = [p for p in sys.path if p != path]
        for module_name in ("main", "action_generator", "loop", "onboard"):
            sys.modules.pop(module_name, None)


def test_action_recover_preview_is_side_effect_free_and_confirm_consumes_token(orchestrator_path, monkeypatch):
    import action_generator
    import audit
    import main

    consumed_tokens = set()
    consume_call_count = 0
    audit_calls = []

    def fake_verify_recovery_token(token):
        if token != "signed-token":
            return None
        return {
            "incident_id": "inc-tenant-123-1",
            "tenant_id": "tenant-123",
            "action": "restart",
        }

    def fake_validate_and_consume(token, expected_tenant_id=None):
        nonlocal consume_call_count
        consume_call_count += 1
        assert expected_tenant_id is None
        if token in consumed_tokens:
            return False, None, "Token already used"
        consumed_tokens.add(token)
        return True, {
            "incident_id": "inc-tenant-123-1",
            "tenant_id": "tenant-123",
            "action": "restart",
        }, "ok"

    def fake_append_event(**kwargs):
        audit_calls.append(kwargs)
        return True

    monkeypatch.setattr(action_generator, "verify_recovery_token", fake_verify_recovery_token)
    monkeypatch.setattr(action_generator, "validate_and_consume", fake_validate_and_consume)
    monkeypatch.setattr(audit, "append_event", fake_append_event)

    client = TestClient(main.health_app)
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "/action/recover" in openapi.json()["paths"]
    assert "/action/recover/confirm" in openapi.json()["paths"]

    first_preview = client.get("/action/recover", params={"token": "signed-token"})
    assert first_preview.status_code == 200
    assert "AlertEngine Recovery Authorization" in first_preview.text
    assert "Incident: inc-tenant-123-1" in first_preview.text
    assert "Action: restart" in first_preview.text

    second_preview = client.get("/action/recover", params={"token": "signed-token"})
    assert second_preview.status_code == 200
    assert consume_call_count == 0
    assert len(audit_calls) == 0

    first_confirm = client.post("/action/recover/confirm", params={"token": "signed-token"})
    assert first_confirm.status_code == 200
    assert first_confirm.json()["authorized"] is True
    assert first_confirm.json()["incident_id"] == "inc-tenant-123-1"
    assert consume_call_count == 1
    assert len(audit_calls) == 1
    assert audit_calls[0]["stage"] == "AUTHORIZED"

    second_confirm = client.post("/action/recover/confirm", params={"token": "signed-token"})
    assert second_confirm.status_code == 401
    assert second_confirm.json()["detail"] == "Token already used"


def test_generate_recovery_token_embeds_actual_tenant_id(orchestrator_path, monkeypatch):
    monkeypatch.setenv("ALERT_SECRET", "test-secret-with-at-least-32-bytes")
    import action_generator
    import jwt

    token = action_generator.generate_recovery_token(
        "inc-tenant-abc-1",
        tenant_id="tenant-abc",
        action="restart",
        ttl=300,
    )
    payload = jwt.decode(token, "test-secret-with-at-least-32-bytes", algorithms=["HS256"])

    assert payload["incident_id"] == "inc-tenant-abc-1"
    assert payload["tenant_id"] == "tenant-abc"
    assert payload["tenant_id"] != "default"
    assert payload["action"] == "restart"


def test_recover_action_writes_audit_entry(orchestrator_path, monkeypatch):
    """Verify /action/recover/confirm writes an AUTHORIZED audit entry."""
    import action_generator
    import main

    audit_calls = []

    def fake_validate_and_consume(token, expected_tenant_id=None):
        return True, {
            "incident_id": "inc-test-001",
            "tenant_id":   "tenant-test",
            "action":      "restart",
        }, "ok"

    def fake_verify_recovery_token(token):
        if token != "valid-token":
            return None
        return {
            "incident_id": "inc-test-001",
            "tenant_id": "tenant-test",
            "action": "restart",
        }

    def fake_append_event(**kwargs):
        audit_calls.append(kwargs)
        return True

    monkeypatch.setattr(action_generator, "verify_recovery_token", fake_verify_recovery_token)
    monkeypatch.setattr(action_generator,
                        "validate_and_consume",
                        fake_validate_and_consume)

    # Patch audit.append_event inside main module scope
    import audit
    monkeypatch.setattr(audit, "append_event", fake_append_event)

    from fastapi.testclient import TestClient
    client = TestClient(main.health_app)

    preview_resp = client.get("/action/recover", params={"token": "valid-token"})
    assert preview_resp.status_code == 200
    assert len(audit_calls) == 0

    resp = client.post("/action/recover/confirm", params={"token": "valid-token"})
    assert resp.status_code == 200
    assert resp.json()["authorized"] is True

    # Verify audit entry was written
    assert len(audit_calls) == 1
    assert audit_calls[0]["stage"] == "AUTHORIZED"
    assert audit_calls[0]["tenant_id"] == "tenant-test"
    assert audit_calls[0]["incident_id"] == "inc-test-001"


def test_all_orchestrator_recovery_token_call_sites_pass_tenant_id():
    for rel_path in ("orchestrator/loop.py", "orchestrator/onboard.py"):
        source = (Path(__file__).resolve().parents[1] / rel_path).read_text()
        tree = ast.parse(source, filename=rel_path)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None)) == "generate_recovery_token"
        ]
        assert calls, f"expected at least one generate_recovery_token call in {rel_path}"
        for call in calls:
            keyword_names = {kw.arg for kw in call.keywords}
            assert "tenant_id" in keyword_names, f"{rel_path} omits tenant_id when generating recovery token"


def test_recovery_url_base_prefers_action_base_url_with_alertengine_fallback(orchestrator_path, monkeypatch):
    monkeypatch.setenv("ACTION_BASE_URL", "https://actions.example.test")
    monkeypatch.setenv("ALERTENGINE_BASE_URL", "https://fallback.example.test")
    import loop

    assert loop.ACTION_BASE_URL == "https://actions.example.test"

    sys.modules.pop("loop", None)
    monkeypatch.delenv("ACTION_BASE_URL", raising=False)
    loop = importlib.import_module("loop")
    assert loop.ACTION_BASE_URL == "https://fallback.example.test"

    onboard_source = (Path(__file__).resolve().parents[1] / "orchestrator/onboard.py").read_text()
    assert 'os.getenv("ACTION_BASE_URL", os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000"))' in onboard_source
