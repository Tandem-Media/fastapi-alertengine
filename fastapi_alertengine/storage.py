# fastapi_alertengine/storage.py
"""
Redis Streams read/write for request metrics, plus aggregation helpers.


Public functions:
  write_metric(rdb, config, metric)              -- write one metric dict
  write_batch(rdb, config, metrics)              -- write many metrics via pipeline
  flush_aggregates(rdb, config, snapshot)        -- persist minute-bucket aggregates
  read_aggregates(rdb, config, service, ...)     -> list[dict]
  read_metrics(rdb, config, last_n)              -> list[RequestMetricEvent]
  aggregate(rdb, config, last_n)                 -> dict


All Redis operations fail silently.
"""


import json
import logging
from typing import List, Optional


from .config import AlertConfig
from .schemas import RequestMetricEvent


logger = logging.getLogger(__name__)


# ── Canonical stream field schema ─────────────────────────────────────────
#
#   path        str   request.url.path
#   method      str   HTTP verb, upper-cased
#   status      str   HTTP status code as string  e.g. "200"
#   latency_ms  str   wall-clock ms, 3 d.p.       e.g. "143.720"
#   type        str   "api" | "webhook"
#
# All values are stored as strings because Redis Streams hash values are bytes.




def _classify(path: str) -> str:
