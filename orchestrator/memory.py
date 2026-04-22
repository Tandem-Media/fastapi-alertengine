# orchestrator/memory.py
"""
Incident Memory Layer

Turns the orchestrator from reactive → adaptive.

Every incident cycle is stored with:
    incident_signature  →  what was wrong (hash of alert types + severity)
    last_action         →  what we suggested
    outcome             →  did health improve after?
    timestamp           →  when it happened

This lets the orchestrator answer:
    "Has this happened before?"
    "Did restart actually help last time?"
    "How long did recovery take?"

Storage: Redis hash + ZSET
    Key pattern: orchestrator:memory:{service}:{signature}
    Index:       orchestrator:memory:index:{service}  (ZSET by ts)

Falls back to in-memory dict when Redis is unavailable.
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional

import redis as redis_lib

logger = logging.getLogger("orchestrator.memory")

REDIS_URL      = os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0")
MEMORY_TTL     = 86_400 * 30   # 30 days
MEMORY_MAX     = 500
KEY_PREFIX     = "orchestrator:memory"
INDEX_PREFIX   = "orchestrator:memory:index"
SERVICE_NAME   = os.getenv("ALERTENGINE_SERVICE_NAME", "default")


@dataclass
class IncidentRecord:
    signature:    str
    service:      str
    action:       str
    confidence:   str
    health_before: float
    health_after:  Optional[float]
    improved:      Optional[bool]
    recovery_s:    Optional[float]
    alert_types:  list
    timestamp:    float

    def as_dict(self) -> dict:
        return asdict(self)


def _make_signature(health: dict) -> str:
    """
    Create a stable signature from the current alert pattern.
    Same alert types + severities = same signature.
    Allows matching recurring incidents.
    """
    alerts = health.get("alerts", [])
    parts  = sorted(
        f"{a.get('type','?')}:{a.get('severity','?')}"
        for a in alerts
    )
    roc = health.get("rate_of_change", [])
    parts += sorted(f"roc:{r.get('metric','?')}" for r in roc)
    raw = "|".join(parts) or "no_alerts"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class IncidentMemory:

    def __init__(self):
        self._rdb: Optional[redis_lib.Redis] = None
        self._fallback: dict = {}  # in-memory when Redis unavailable
        self._connect()

    def _connect(self):
        try:
            r = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
            r.ping()
            self._rdb = r
            logger.info("Incident memory: Redis connected")
        except Exception as exc:
            logger.warning("Incident memory: Redis unavailable (%s) — using memory fallback", exc)
            self._rdb = None

    # ── Write ──────────────────────────────────────────────────────────────────

    def record(
        self,
        health:      dict,
        action:      str,
        confidence:  str,
        service:     str = SERVICE_NAME,
    ) -> str:
        """Record an incident and return its signature."""
        sig = _make_signature(health)
        hs  = health.get("health_score", {})
        score = hs.get("score", 0) if isinstance(hs, dict) else 0
        alerts = health.get("alerts", [])

        record = IncidentRecord(
            signature     = sig,
            service       = service,
            action        = action,
            confidence    = confidence,
            health_before = score,
            health_after  = None,
            improved      = None,
            recovery_s    = None,
            alert_types   = [a.get("type") for a in alerts],
            timestamp     = time.time(),
        )

        self._store(sig, service, record)
        logger.info("Memory recorded: sig=%s action=%s score=%.1f", sig, action, score)
        return sig

    def resolve(
        self,
        signature:    str,
        health_after: float,
        service:      str = SERVICE_NAME,
    ) -> None:
        """Update a record with the outcome after action was taken."""
        existing = self._load(signature, service)
        if existing is None:
            return

        improved   = health_after > existing["health_before"]
        recovery_s = time.time() - existing["timestamp"]

        existing["health_after"] = health_after
        existing["improved"]     = improved
        existing["recovery_s"]   = round(recovery_s, 1)

        record = IncidentRecord(**existing)
        self._store(signature, service, record)
        logger.info(
            "Memory resolved: sig=%s improved=%s recovery=%.0fs",
            signature, improved, recovery_s,
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    def recall(
        self,
        health:  dict,
        service: str = SERVICE_NAME,
        last_n:  int = 3,
    ) -> list[dict]:
        """
        Return the most recent incidents with the same signature.
        Used to tell Claude whether this pattern has occurred before
        and what happened when we acted on it.
        """
        sig = _make_signature(health)
        return self._recent(sig, service, last_n)

    def summary(self, health: dict, service: str = SERVICE_NAME) -> str:
        """
        Return a human-readable memory summary for injection into Claude's prompt.
        Empty string if no prior incidents match.
        """
        records = self.recall(health, service)
        if not records:
            return ""

        lines = [f"## INCIDENT MEMORY ({len(records)} prior similar incidents)"]
        for r in records:
            ago    = round((time.time() - r["timestamp"]) / 60, 0)
            result = "improved" if r.get("improved") else (
                     "no improvement" if r.get("improved") is False else "outcome unknown")
            lines.append(
                f"- {ago:.0f} min ago: action={r['action']} outcome={result} "
                f"score_before={r['health_before']:.0f} "
                f"score_after={r.get('health_after', '?')}"
            )
        return "\n".join(lines)

    # ── Storage helpers ────────────────────────────────────────────────────────

    def _store(self, sig: str, service: str, record: IncidentRecord) -> None:
        data = json.dumps(record.as_dict())
        if self._rdb:
            try:
                key   = f"{KEY_PREFIX}:{service}:{sig}:{record.timestamp}"
                index = f"{INDEX_PREFIX}:{service}"
                pipe  = self._rdb.pipeline(transaction=False)
                pipe.set(key, data, ex=MEMORY_TTL)
                pipe.zadd(index, {key: record.timestamp})
                pipe.zremrangebyrank(index, 0, -(MEMORY_MAX + 1))
                pipe.execute()
                return
            except Exception as exc:
                logger.warning("Memory Redis write failed: %s", exc)
        # Fallback
        key = f"{sig}:{record.timestamp}"
        self._fallback[key] = record.as_dict()

    def _load(self, sig: str, service: str) -> Optional[dict]:
        if self._rdb:
            try:
                index = f"{INDEX_PREFIX}:{service}"
                keys  = self._rdb.zrevrangebyscore(index, "+inf", "-inf", start=0, num=10)
                for k in keys:
                    if f":{sig}:" in k:
                        raw = self._rdb.get(k)
                        if raw:
                            return json.loads(raw)
            except Exception as exc:
                logger.warning("Memory Redis load failed: %s", exc)
        # Fallback
        matches = {k: v for k, v in self._fallback.items() if k.startswith(sig)}
        if matches:
            return sorted(matches.values(), key=lambda x: x["timestamp"])[-1]
        return None

    def _recent(self, sig: str, service: str, n: int) -> list[dict]:
        results = []
        if self._rdb:
            try:
                index = f"{INDEX_PREFIX}:{service}"
                keys  = self._rdb.zrevrangebyscore(index, "+inf", "-inf", start=0, num=50)
                for k in keys:
                    if f":{sig}:" in k:
                        raw = self._rdb.get(k)
                        if raw:
                            results.append(json.loads(raw))
                        if len(results) >= n:
                            break
                return results
            except Exception as exc:
                logger.warning("Memory Redis recall failed: %s", exc)
        # Fallback
        matches = [v for k, v in self._fallback.items() if k.startswith(sig)]
        return sorted(matches, key=lambda x: x["timestamp"], reverse=True)[:n]
