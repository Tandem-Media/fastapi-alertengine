# orchestrator/loop.py
"""
Orchestrator Control Loop — Hardened v2

Fixes applied vs v1:
1. State cache          — last-known-good health when AlertEngine is unreachable
2. Hysteresis           — trigger at 75, recover at 68 (prevents oscillation)
3. Action lock          — one active suggestion at a time (prevents collision)
4. Incident memory      — adaptive recall of prior incidents + outcomes
"""

import asyncio
import logging
import os
import time
from typing import Optional

from .alertengine_client import AlertEngineClient
from .action_generator   import ActionGenerator
from .audit              import log_cycle
from .claude_engine      import reason, OrchestratorDecision
from .memory             import IncidentMemory
from .policy             import evaluate as policy_evaluate, PolicyDecision
from .state_cache        import StateCache

logger = logging.getLogger("orchestrator.loop")

POLL_INTERVAL_S   = int(os.getenv("ORCHESTRATOR_POLL_S",          "30"))
TRIGGER_SCORE     = float(os.getenv("ORCHESTRATOR_MIN_SCORE",      "75"))
RECOVERY_SCORE    = float(os.getenv("ORCHESTRATOR_RECOVERY_SCORE", "68"))
ESCALATE_SCORE    = float(os.getenv("ORCHESTRATOR_ESCALATE_SCORE", "25"))
MAX_CYCLES        = int(os.getenv("ORCHESTRATOR_MAX_CYCLES",       "0"))
STAGNATION_LIMIT  = int(os.getenv("ORCHESTRATOR_STAGNATION_LIMIT", "3"))
ACTION_LOCK_TTL   = int(os.getenv("ORCHESTRATOR_ACTION_LOCK_TTL",  "120"))
SERVICE_NAME      = os.getenv("ALERTENGINE_SERVICE_NAME", "default")


class OrchestratorLoop:

    def __init__(self):
        self.client    = AlertEngineClient()
        self.generator = ActionGenerator(self.client)
        self.cache     = StateCache()
        self.memory    = IncidentMemory()
        self.cycle     = 0
        self._activated      = False
        self._prev_scores:   list = []
        self._stagnation_n   = 0
        self._active_action:    Optional[str]  = None
        self._active_action_at: float          = 0.0
        self._active_sig:       Optional[str]  = None

    async def run(self):
        logger.info(
            "Orchestrator started | poll=%ds trigger=%.0f recovery=%.0f",
            POLL_INTERVAL_S, TRIGGER_SCORE, RECOVERY_SCORE,
        )
        while True:
            self.cycle += 1
            if MAX_CYCLES > 0 and self.cycle > MAX_CYCLES:
                logger.info("Max cycles (%d) reached — stopping.", MAX_CYCLES)
                break
            await self._tick()
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _tick(self):
        logger.info("-- Cycle %d --", self.cycle)

        # 1. Fetch with cache fallback
        raw = await self.client.get_health()
        if raw is not None:
            self.cache.update(raw)
            health, freshness = raw, "fresh"
        else:
            health, freshness = self.cache.get()

        if freshness == "empty":
            logger.warning("No health data — skipping.")
            return
        if freshness == "expired":
            logger.error("Cache expired (>10min) — orchestrator idling.")
            return
        if freshness == "stale":
            logger.warning("Using STALE cache (age=%ss)", health.get("_cache_age_s"))

        hs    = health.get("health_score", {})
        score = hs.get("score", 100) if isinstance(hs, dict) else 100
        trend = hs.get("trend", "stable") if isinstance(hs, dict) else "stable"

        # 2. Resolve pending action outcome
        self._maybe_resolve_action(score)

        # 3. Hysteresis
        if not self._activated:
            if score < TRIGGER_SCORE or trend == "degrading":
                self._activated = True
                logger.info("ACTIVATED: score=%.1f trend=%s", score, trend)
            else:
                logger.info("Stable (score=%.1f) — idle.", score)
                return
        else:
            if score >= RECOVERY_SCORE and trend != "degrading":
                self._activated = False
                logger.info("RECOVERED: score=%.1f — idle.", score)
                return
            logger.info("Still degraded: score=%.1f trend=%s", score, trend)

        # 4. Action lock
        if self._action_locked():
            remaining = ACTION_LOCK_TTL - int(time.time() - self._active_action_at)
            logger.info("Action lock active (%ds remaining) — skipping.", remaining)
            return

        # 5. Auto-escalate
        if score < ESCALATE_SCORE:
            logger.critical("Score=%.1f below %.0f — MANUAL INTERVENTION REQUIRED",
                            score, ESCALATE_SCORE)

        # 6. Context
        timeline       = await self.client.get_timeline(limit=10)
        memory_summary = self.memory.summary(health, SERVICE_NAME)
        if memory_summary:
            health["_memory_context"] = memory_summary
        if freshness == "stale":
            health["_warning"] = (
                f"Health data STALE (age={health.get('_cache_age_s')}s). "
                "AlertEngine may be unreachable. Lower confidence accordingly."
            )

        # 7. Claude reasoning
        decision = await reason(health=health, timeline=timeline)
        if decision is None:
            logger.error("Claude failed — skipping.")
            log_cycle(self.cycle, health, None, None, None)
            return

        logger.info("Claude: action=%s confidence=%s stop=%s",
                    decision.action_type, decision.confidence, decision.stop_condition)

        # 8. Stop/Escalate
        if decision.stop_condition == "Stop":
            log_cycle(self.cycle, health, decision, None, None)
            return
        if decision.stop_condition == "Escalate":
            logger.warning("ESCALATE: %s", decision.synthesis)
            log_cycle(self.cycle, health, decision, None, None)
            return

        # 9. Policy
        policy = policy_evaluate(decision, score)
        if not policy.permitted:
            logger.warning("Policy BLOCKED: %s", policy.reason)
            log_cycle(self.cycle, health, decision, policy, None)
            return

        # 10. Generate confirm URL
        action_result = None
        if decision.action_type not in ("none", "investigate"):
            action_result = await self.generator.get_confirm_url(
                action_type=decision.action_type,
            )
            if action_result:
                self._set_action_lock(decision.action_type)
                self._active_sig = self.memory.record(
                    health=health, action=decision.action_type,
                    confidence=decision.confidence, service=SERVICE_NAME,
                )
                self._surface_action(decision, policy, action_result, freshness)
            else:
                logger.warning("No confirm URL for action=%s", decision.action_type)
        else:
            logger.info("Action=%s — no token needed.", decision.action_type)

        self._check_stagnation(score)
        log_cycle(self.cycle, health, decision, policy, action_result)

    def _action_locked(self) -> bool:
        if self._active_action is None:
            return False
        if time.time() - self._active_action_at >= ACTION_LOCK_TTL:
            self._active_action = None
            return False
        return True

    def _set_action_lock(self, action: str):
        self._active_action    = action
        self._active_action_at = time.time()
        logger.info("Action lock SET: %s ttl=%ds", action, ACTION_LOCK_TTL)

    def _maybe_resolve_action(self, score: float):
        if self._active_sig and self._active_action:
            if time.time() - self._active_action_at > 30:
                self.memory.resolve(self._active_sig, score, SERVICE_NAME)
                self._active_sig = None

    def _surface_action(self, decision, policy, action_result, freshness):
        sep = "-" * 60
        stale = "  WARNING: Health data is STALE\n" if freshness == "stale" else ""
        expires = action_result.get("expires_at")
        remaining = max(0, int(expires - time.time())) if expires else "?"
        print(f"\n{sep}")
        print(f"RECOVERY ACTION REQUIRED  (Cycle {self.cycle})")
        print(f"{sep}")
        print(f"  Action:     {action_result['action'].upper()}")
        print(f"  Priority:   {action_result['priority']}")
        print(f"  Risk:       {policy.risk}")
        print(f"  Confidence: {decision.confidence}")
        print(stale, end="")
        print(f"  Diagnosis:  {decision.root_cause}")
        print(f"")
        print(f"  APPROVE HERE:")
        print(f"  {action_result['confirm_url']}")
        print(f"  Token expires in ~{remaining}s")
        print(f"{sep}\n")
        # TODO: Replace with Twilio WhatsApp send when wired
        logger.info("ACTION SURFACED: %s", action_result["confirm_url"])

    def _check_stagnation(self, score: float):
        self._prev_scores.append(score)
        if len(self._prev_scores) > STAGNATION_LIMIT:
            self._prev_scores.pop(0)
        if len(self._prev_scores) < STAGNATION_LIMIT:
            return
        if max(self._prev_scores) - min(self._prev_scores) < 2.0:
            self._stagnation_n += 1
            logger.warning("Score stagnating over %d cycles.", STAGNATION_LIMIT)
        else:
            self._stagnation_n = 0
