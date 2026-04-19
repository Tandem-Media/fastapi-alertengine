import time
import statistics
from typing import Dict, List, Any, Optional

import redis.asyncio as redis

from .health_engine import HealthEngine, HealthInput
from .actions import suggest_action


class AlertEngine:
    """
    Core evaluation engine for fastapi-alertengine.

    Responsibilities:
    - Compute P95 latency
    - Evaluate error rates
    - Store/retrieve events from Redis Streams
    - Generate structured alerts
    - Compute system health (v1.6)
    - Suggest recovery actions (v1.6)
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        stream_key: str = "alertengine:events",
    ):
        self.redis = redis_client
        self.stream_key = stream_key

        # ----------------------------
        # v1.6 additions
        # ----------------------------
        self.health_engine = HealthEngine()

        # baseline tracking (v1.5 foundation)
        self.baseline_p95: float = 300.0  # default fallback, should be learned

    # --------------------------------------------------------
    # CORE EVALUATION
    # --------------------------------------------------------

    async def evaluate(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate system health from collected metrics.
        """

        p95 = metrics.get("p95", 0)
        error_rate = metrics.get("error_rate", 0)

        # --------------------------------------------------------
        # 1. HEALTH ENGINE (v1.6 CORE ADDITION)
        # --------------------------------------------------------

        health_result = self.health_engine.calculate(
            HealthInput(
                p95_latency_ms=p95,
                baseline_p95_ms=self.baseline_p95,
                error_rate=error_rate
            )
        )

        # --------------------------------------------------------
        # 2. ACTION SUGGESTION (v1.6)
        # --------------------------------------------------------

        recommended_action = suggest_action(health_result.health_score)

        # --------------------------------------------------------
        # 3. ALERT STRUCTURE
        # --------------------------------------------------------

        alerts: List[Dict[str, Any]] = []

        if error_rate > 0.05:
            alerts.append({
                "type": "error_rate",
                "message": f"High error rate detected: {error_rate:.2%}"
            })

        if p95 > self.baseline_p95 * 2:
            alerts.append({
                "type": "latency",
                "message": f"P95 latency spike: {p95}ms"
            })

        # --------------------------------------------------------
        # 4. ENRICHED OUTPUT
        # --------------------------------------------------------

        result = {
            "timestamp": time.time(),
            "metrics": metrics,

            # core observability
            "p95": p95,
            "error_rate": error_rate,

            # health layer (v1.6)
            "health_score": health_result.health_score,
            "health_status": health_result.status.value,
            "health_reasons": health_result.reasons,

            # decision layer (v1.6)
            "recommended_action": recommended_action,

            # alerts
            "alerts": alerts,
        }

        # --------------------------------------------------------
        # 5. STORE TO REDIS STREAM (non-blocking pipeline)
        # --------------------------------------------------------

        await self._store_event(result)

        return result

    # --------------------------------------------------------
    # REDIS STREAM STORAGE
    # --------------------------------------------------------

    async def _store_event(self, event: Dict[str, Any]) -> None:
        """
        Append event to Redis Stream for incident timeline replay.
        """

        try:
            await self.redis.xadd(
                self.stream_key,
                {"data": str(event)},
                maxlen=10000,
                approximate=True
            )
        except Exception:
            # CRITICAL: never break request path
            # observability must fail silently, not systemically
            pass

    # --------------------------------------------------------
    # INCIDENT REPLAY (USED IN v1.6)
    # --------------------------------------------------------

    async def replay_incident(self, trace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Reconstruct incident timeline from Redis stream.
        """

        try:
            events = await self.redis.xrange(self.stream_key, count=500)

            parsed = []
            for _, data in events:
                parsed.append(eval(data[b"data"].decode()))  # replace with safe parser in production

            if trace_id:
                parsed = [e for e in parsed if e.get("metrics", {}).get("trace_id") == trace_id]

            return parsed

        except Exception:
            return []