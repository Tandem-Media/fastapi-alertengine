# tests/test_actions.py
"""
Tests for the AnchorFlow remote-action system.

Covers:
  - JWT generation and verification (tokens.py)
  - Audit logging (audit.py)
  - Service handler (services.py)
  - REST endpoint (router.py) — happy path, expiry, bad sig, malformed
  - WhatsApp URL builder (whatsapp.py)
"""

import json
import logging
import os
import subprocess
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_alertengine.actions.audit import log_action
from fastapi_alertengine.actions.replay import _reset as reset_replay_store
from fastapi_alertengine.actions.router import router as actions_router
from fastapi_alertengine.actions.services import restart_container
from fastapi_alertengine.actions.tokens import (
    _ALGORITHM,
    _TOKEN_TTL_SECONDS,
    generate_action_token,
    verify_action_token,
)
from fastapi_alertengine.actions.whatsapp import build_action_message

# ── Fixtures ──────────────────────────────────────────────────────────────────

_SECRET = "test-secret-key-for-pytest"


@pytest.fixture(autouse=True)
def set_secret(monkeypatch):
    """Inject ACTION_SECRET_KEY for every test in this module."""
    monkeypatch.setenv("ACTION_SECRET_KEY", _SECRET)


@pytest.fixture(autouse=True)
def clear_replay_store():
    """Reset the in-memory JTI store before each test to prevent pollution."""
    reset_replay_store()
    yield
    reset_replay_store()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(actions_router)
    return TestClient(app, raise_server_exceptions=False)


# ── tokens.py ─────────────────────────────────────────────────────────────────


class TestGenerateActionToken:
    def test_returns_string(self):
        token = generate_action_token("restart", "svc", "u1")
        assert isinstance(token, str) and len(token) > 10

    def test_payload_fields(self):
        token = generate_action_token("restart", "payments-api", "user-42")
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
        assert payload["action"] == "restart"
        assert payload["service"] == "payments-api"
        assert payload["user_id"] == "user-42"
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload
        # jti must be a non-empty string (UUID4)
        assert isinstance(payload["jti"], str) and len(payload["jti"]) > 0

    def test_expiry_within_window(self):
        before = int(time.time())
        token = generate_action_token("restart", "svc", "u1")
        after = int(time.time())
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
        assert before + _TOKEN_TTL_SECONDS <= payload["exp"] <= after + _TOKEN_TTL_SECONDS

    def test_missing_secret_raises(self, monkeypatch):
        monkeypatch.delenv("ACTION_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ACTION_SECRET_KEY"):
            generate_action_token("restart", "svc", "u1")


class TestVerifyActionToken:
    def test_valid_token_round_trips(self):
        token = generate_action_token("restart", "api", "u99")
        payload = verify_action_token(token)
        assert payload["action"] == "restart"
        assert payload["service"] == "api"
        assert payload["user_id"] == "u99"

    def test_expired_token_raises(self):
        # Manually craft a token already expired
        payload = {
            "action": "restart",
            "service": "svc",
            "user_id": "u1",
            "iat": int(time.time()) - 200,
            "exp": int(time.time()) - 100,
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)
        with pytest.raises(jwt.ExpiredSignatureError):
            verify_action_token(token)

    def test_wrong_signature_raises(self):
        token = generate_action_token("restart", "svc", "u1")
        with pytest.raises(jwt.InvalidTokenError):
            # Decode with a different secret
            jwt.decode(token, "wrong-secret", algorithms=[_ALGORITHM])

    def test_tampered_token_raises(self):
        token = generate_action_token("restart", "svc", "u1")
        # Flip a character in the signature segment
        parts = token.split(".")
        parts[-1] = parts[-1][:-4] + "XXXX"
        bad_token = ".".join(parts)
        with pytest.raises(jwt.InvalidTokenError):
            verify_action_token(bad_token)

    def test_garbage_input_raises(self):
        with pytest.raises(jwt.InvalidTokenError):
            verify_action_token("not.a.jwt")


# ── audit.py ──────────────────────────────────────────────────────────────────


class TestLogAction:
    def test_success_logged_at_info(self, caplog):
        with caplog.at_level(logging.INFO, logger="fastapi_alertengine.audit"):
            log_action(
                user_id="u1",
                action="restart",
                service="payments-api",
                result="success",
                detail="all good",
            )
        assert len(caplog.records) == 1
        record = json.loads(caplog.records[0].message)
        assert record["user_id"] == "u1"
        assert record["action"] == "restart"
        assert record["service"] == "payments-api"
        assert record["result"] == "success"
        assert record["detail"] == "all good"
        assert record["timestamp"].endswith("Z")

    def test_failure_logged_at_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="fastapi_alertengine.audit"):
            log_action(
                user_id="u2",
                action="restart",
                service="db",
                result="failure",
                detail="connection refused",
            )
        assert caplog.records[0].levelno == logging.WARNING

    def test_timestamp_format(self, caplog):
        with caplog.at_level(logging.INFO, logger="fastapi_alertengine.audit"):
            log_action(user_id="u", action="a", service="s", result="success")
        record = json.loads(caplog.records[0].message)
        # ISO 8601 UTC with microseconds, ending in Z
        assert "T" in record["timestamp"]
        assert record["timestamp"].endswith("Z")


# ── services.py ───────────────────────────────────────────────────────────────

_DOCKER_PATCH = "fastapi_alertengine.actions.services.subprocess.run"


def _ok_run(container_id="abc123"):
    """Return a mock CompletedProcess that represents a successful docker restart."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = container_id
    m.stderr = ""
    return m


def _fail_run(stderr="No such container", returncode=1):
    """Return a mock CompletedProcess that represents a failed docker restart."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = ""
    m.stderr = stderr
    return m


class TestRestartContainer:
    async def test_success_returns_service_and_id(self):
        with patch(_DOCKER_PATCH, return_value=_ok_run("abc123")) as mock_run:
            result = await restart_container("payments-api")
        assert "payments-api" in result
        assert "abc123" in result
        # Verify docker was called correctly (no shell, correct command list)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "restart", "payments-api"]
        assert mock_run.call_args.kwargs.get("shell") is not True

    async def test_different_service_names_accepted(self):
        for svc in ("redis", "worker-1", "my-namespace/my-deploy"):
            with patch(_DOCKER_PATCH, return_value=_ok_run()):
                result = await restart_container(svc)
            assert svc in result

    async def test_docker_failure_raises_runtime_error(self):
        with patch(_DOCKER_PATCH, return_value=_fail_run("No such container: svc")):
            with pytest.raises(RuntimeError, match="exited with code"):
                await restart_container("svc")

    async def test_docker_timeout_raises_runtime_error(self):
        with patch(_DOCKER_PATCH, side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=30)):
            with pytest.raises(RuntimeError, match="timed out"):
                await restart_container("svc")

    async def test_docker_not_found_raises_runtime_error(self):
        with patch(_DOCKER_PATCH, side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="not found on PATH"):
                await restart_container("svc")

    async def test_invalid_service_name_raises_value_error(self):
        for bad in ("", "-starts-with-dash", "name with spaces", "a" * 300, "svc;rm -rf /"):
            with pytest.raises(ValueError, match="Invalid service name"):
                await restart_container(bad)

    async def test_valid_service_name_at_max_length_accepted(self):
        # 254-character name: 1 leading alphanum + 253 valid chars = right at the boundary
        long_name = "a" + "b" * 253
        assert len(long_name) == 254
        with patch(_DOCKER_PATCH, return_value=_ok_run()) as mock_run:
            result = await restart_container(long_name)
        assert long_name in result
        mock_run.assert_called_once()

    async def test_shell_false_by_default(self):
        """Ensure subprocess.run is never called with shell=True."""
        with patch(_DOCKER_PATCH, return_value=_ok_run()) as mock_run:
            await restart_container("safe-svc")
        kwargs = mock_run.call_args.kwargs
        assert kwargs.get("shell") is not True


# ── router.py — integration ───────────────────────────────────────────────────


class TestActionRestartEndpoint:
    def _valid_token(self, action="restart", service="payments-api", user_id="u1"):
        return generate_action_token(action, service, user_id)

    @pytest.fixture(autouse=True)
    def mock_docker(self):
        """Prevent real Docker calls in router integration tests."""
        with patch(_DOCKER_PATCH, return_value=_ok_run("abc123")):
            yield

    def test_happy_path(self, client):
        token = self._valid_token()
        resp = client.get(f"/action/restart?token={token}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["service"] == "payments-api"
        assert body["action"] == "restart"

    def test_expired_token_returns_403(self, client):
        payload = {
            "action": "restart",
            "service": "svc",
            "user_id": "u1",
            "iat": int(time.time()) - 200,
            "exp": int(time.time()) - 100,
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)
        resp = client.get(f"/action/restart?token={token}")
        assert resp.status_code == 403

    def test_invalid_signature_returns_403(self, client):
        bad_token = jwt.encode(
            {
                "action": "restart",
                "service": "svc",
                "user_id": "u1",
                "exp": int(time.time()) + 90,
            },
            "wrong-secret",
            algorithm=_ALGORITHM,
        )
        resp = client.get(f"/action/restart?token={bad_token}")
        assert resp.status_code == 403

    def test_missing_token_returns_422(self, client):
        # FastAPI returns 422 for missing required query param
        resp = client.get("/action/restart")
        assert resp.status_code == 422

    def test_garbage_token_returns_403(self, client):
        resp = client.get("/action/restart?token=totalgarbagevalue")
        assert resp.status_code == 403

    def test_wrong_action_in_token_returns_400(self, client):
        token = self._valid_token(action="delete")  # not "restart"
        resp = client.get(f"/action/restart?token={token}")
        assert resp.status_code == 400

    def test_missing_payload_fields_returns_400(self, client):
        # Token with incomplete payload (missing user_id)
        payload = {
            "action": "restart",
            # service and user_id omitted
            "exp": int(time.time()) + 90,
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)
        resp = client.get(f"/action/restart?token={token}")
        assert resp.status_code == 400

    def test_audit_log_written_on_success(self, client, caplog):
        token = self._valid_token()
        with caplog.at_level(logging.INFO, logger="fastapi_alertengine.audit"):
            client.get(f"/action/restart?token={token}")
        audit_records = [
            json.loads(r.message)
            for r in caplog.records
            if r.name == "fastapi_alertengine.audit"
        ]
        assert len(audit_records) == 1
        assert audit_records[0]["result"] == "success"
        assert audit_records[0]["action"] == "restart"

    def test_no_secret_returns_500(self, client, monkeypatch):
        monkeypatch.delenv("ACTION_SECRET_KEY", raising=False)
        # Any token will fail because the secret is gone
        resp = client.get("/action/restart?token=anything")
        assert resp.status_code == 500


# ── whatsapp.py ───────────────────────────────────────────────────────────────


class TestBuildActionMessage:
    def test_returns_all_fields(self):
        msg = build_action_message("restart", "payments-api", "u1", base_url="https://example.com")
        assert msg.token
        assert msg.signed_url.startswith("https://example.com/action/confirm?token=")
        assert "restart" in msg.body
        assert "payments-api" in msg.body

    def test_token_is_valid_jwt(self):
        msg = build_action_message("restart", "svc", "u42", base_url="https://x.io")
        payload = verify_action_token(msg.token)
        assert payload["action"] == "restart"
        assert payload["service"] == "svc"
        assert payload["user_id"] == "u42"

    def test_signed_url_contains_token(self):
        msg = build_action_message("restart", "svc", "u1", base_url="https://x.io")
        assert f"token={msg.token}" in msg.signed_url

    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("BASE_URL", "https://env-base.example.com")
        msg = build_action_message("restart", "svc", "u1")
        # Verify the env-var base URL is honoured as a prefix of the path component
        expected_prefix = "https://env-base.example.com/action/confirm"
        assert msg.signed_url.startswith(expected_prefix)

    def test_base_url_trailing_slash_stripped(self):
        msg = build_action_message("restart", "svc", "u1", base_url="https://x.io/")
        assert "//action" not in msg.signed_url

    def test_body_contains_expiry_notice(self):
        msg = build_action_message("restart", "svc", "u1", base_url="https://x.io")
        assert "90 seconds" in msg.body or "expire" in msg.body.lower()

    def test_signed_url_routes_through_confirm(self):
        msg = build_action_message("restart", "svc", "u1", base_url="https://x.io")
        assert "/action/confirm?" in msg.signed_url


# ── replay.py ─────────────────────────────────────────────────────────────────


class TestReplayProtection:
    """Verify that each token can only be successfully used once."""

    @pytest.fixture(autouse=True)
    def mock_docker(self):
        with patch(_DOCKER_PATCH, return_value=_ok_run("abc123")):
            yield

    def test_first_use_succeeds(self, client):
        token = generate_action_token("restart", "payments-api", "u1")
        resp = client.get(f"/action/restart?token={token}")
        assert resp.status_code == 200

    def test_second_use_of_same_token_returns_403(self, client):
        token = generate_action_token("restart", "payments-api", "u1")
        first = client.get(f"/action/restart?token={token}")
        assert first.status_code == 200
        second = client.get(f"/action/restart?token={token}")
        assert second.status_code == 403
        assert "already been used" in second.json()["detail"]

    def test_different_tokens_do_not_interfere(self, client):
        token_a = generate_action_token("restart", "svc-a", "u1")
        token_b = generate_action_token("restart", "svc-b", "u1")
        assert client.get(f"/action/restart?token={token_a}").status_code == 200
        assert client.get(f"/action/restart?token={token_b}").status_code == 200

    def test_token_without_jti_returns_400(self, client):
        # Manually craft a token with no jti
        payload = {
            "action": "restart",
            "service": "svc",
            "user_id": "u1",
            "exp": int(time.time()) + 90,
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)
        resp = client.get(f"/action/restart?token={token}")
        assert resp.status_code == 400
        assert "jti" in resp.json()["detail"].lower()

    def test_replay_store_cleared_between_tests(self, client):
        # A token used in a *previous* test should not block this one
        # because clear_replay_store resets the store before each test.
        token = generate_action_token("restart", "payments-api", "u1")
        resp = client.get(f"/action/restart?token={token}")
        assert resp.status_code == 200


# ── /action/confirm endpoint ──────────────────────────────────────────────────


class TestConfirmEndpoint:
    def _valid_token(self, action="restart", service="payments-api", user_id="u1"):
        return generate_action_token(action, service, user_id)

    @pytest.fixture(autouse=True)
    def mock_docker(self):
        with patch(_DOCKER_PATCH, return_value=_ok_run("abc123")):
            yield

    def test_valid_token_returns_html(self, client):
        token = self._valid_token()
        resp = client.get(f"/action/confirm?token={token}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_confirm_page_contains_action_and_service(self, client):
        token = self._valid_token(action="restart", service="payments-api")
        resp = client.get(f"/action/confirm?token={token}")
        body = resp.text
        assert "restart" in body
        assert "payments-api" in body

    def test_confirm_page_has_form_with_token(self, client):
        token = self._valid_token()
        resp = client.get(f"/action/confirm?token={token}")
        assert f'value="{token}"' in resp.text

    def test_expired_token_returns_403_html(self, client):
        payload = {
            "action": "restart",
            "service": "svc",
            "user_id": "u1",
            "jti": "some-id",
            "iat": int(time.time()) - 200,
            "exp": int(time.time()) - 100,
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)
        resp = client.get(f"/action/confirm?token={token}")
        assert resp.status_code == 403

    def test_invalid_token_returns_403_html(self, client):
        resp = client.get("/action/confirm?token=notavalidtoken")
        assert resp.status_code == 403

    def test_confirm_does_not_consume_token(self, client):
        """Viewing the confirm page must NOT mark the token as used."""
        token = self._valid_token()
        # Visit confirm page
        confirm_resp = client.get(f"/action/confirm?token={token}")
        assert confirm_resp.status_code == 200
        # Token must still be usable for the actual execution
        exec_resp = client.get(f"/action/restart?token={token}")
        assert exec_resp.status_code == 200

    def test_restart_still_blocked_after_execution(self, client):
        """After executing via /action/restart, reusing the token is blocked."""
        token = self._valid_token()
        client.get(f"/action/restart?token={token}")  # consume
        # Viewing confirm with a used token is allowed (page shows, no action taken)
        # but executing again must fail
        second_exec = client.get(f"/action/restart?token={token}")
        assert second_exec.status_code == 403
