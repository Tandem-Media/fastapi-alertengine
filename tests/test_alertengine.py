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
