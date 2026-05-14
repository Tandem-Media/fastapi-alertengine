import os
import pytest
from unittest.mock import patch

def test_insecure_default_raises_in_production():
    with patch.dict(os.environ, {
        "ALERT_SECRET": "changeme",
        "ENVIRONMENT": "production"
    }):
        with pytest.raises(RuntimeError, match="insecure"):
            from orchestrator import main
            main._validate_alert_secret()

def test_short_secret_raises_in_production():
    with patch.dict(os.environ, {
        "ALERT_SECRET": "tooshort",
        "ENVIRONMENT": "production"
    }):
        with pytest.raises(RuntimeError, match="32 characters"):
            from orchestrator import main
            main._validate_alert_secret()

def test_weak_secret_warns_in_development(caplog):
    with patch.dict(os.environ, {
        "ALERT_SECRET": "changeme",
        "ENVIRONMENT": "development"
    }):
        import logging
        with caplog.at_level(logging.WARNING):
            from orchestrator import main
            main._validate_alert_secret()
        assert "weak" in caplog.text.lower() or \
               "insecure" in caplog.text.lower()

def test_strong_secret_passes():
    with patch.dict(os.environ, {
        "ALERT_SECRET": "a" * 32,
        "ENVIRONMENT": "production"
    }):
        from orchestrator import main
        main._validate_alert_secret()  # must not raise
