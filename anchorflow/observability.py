# anchorflow/observability.py
"""
Observability wiring for AnchorFlow via fastapi-alertengine.

Call setup_observability(app) once at startup to:
  - Register request-metrics middleware for latency and error-rate tracking
  - Start the background aggregation and alert-delivery worker
  - Mount the following endpoints on *app*:
      GET  /health/alerts      — current alert status
      POST /alerts/evaluate    — trigger evaluation + optional Slack delivery
      GET  /metrics/history    — aggregated per-bucket metrics
      GET  /metrics/ingestion  — queue counters (enqueued / dropped)

Configuration is read entirely from environment variables (no code changes
needed per environment).  Redis and Slack are both optional; the engine
falls back to in-memory mode when Redis is unavailable.
"""

import logging
import os
import socket

from fastapi import FastAPI

from fastapi_alertengine import AlertConfig, instrument

logger = logging.getLogger(__name__)

# ── Sensible threshold defaults ────────────────────────────────────────────────
_P95_WARNING_MS = 1_000.0
_P95_CRITICAL_MS = 3_000.0
# Stored as percentages (0-100) to match AlertConfig.error_rate_*_pct fields
_ERROR_RATE_WARNING_PCT = 5.0
_ERROR_RATE_CRITICAL_PCT = 15.0


def setup_observability(app: FastAPI) -> None:
    """
    Wire fastapi-alertengine into the AnchorFlow FastAPI application.

    This function registers the request-metrics middleware, starts the
    background worker, and mounts the observability endpoints.  It must
    be called once, immediately after the FastAPI instance is created.

    Configuration is driven entirely by environment variables:

    ``ALERTENGINE_REDIS_URL``
        Redis connection string (default: ``redis://localhost:6379/0``).
        When Redis is unreachable the engine runs in-memory mode.

    ``ALERTENGINE_SERVICE``
        Logical service name reported in metrics (default: ``"anchorflow"``).

    ``ALERTENGINE_INSTANCE``
        Instance / host identifier (default: current hostname or ``"local"``).

    ``ALERTENGINE_SLACK_WEBHOOK_URL``
        Optional Slack incoming-webhook URL for alert delivery.  Omit to
        disable Slack notifications.

    No additional setup is required beyond the environment variables above.
    """
    service = os.getenv("ALERTENGINE_SERVICE", "anchorflow")
    instance = os.getenv("ALERTENGINE_INSTANCE", _default_instance())
    slack_webhook_url = os.getenv("ALERTENGINE_SLACK_WEBHOOK_URL")

    config = AlertConfig(
        service_name=service,
        instance_id=instance,
        p95_warning_ms=_P95_WARNING_MS,
        p95_critical_ms=_P95_CRITICAL_MS,
        error_rate_warning_pct=_ERROR_RATE_WARNING_PCT,
        error_rate_critical_pct=_ERROR_RATE_CRITICAL_PCT,
        slack_webhook_url=slack_webhook_url,
    )

    instrument(app, config=config)

    logger.info("Observability initialized (fastapi-alertengine)")


def _default_instance() -> str:
    """Return the current hostname, falling back to 'local'."""
    try:
        return socket.gethostname()
    except OSError:
        return "local"
