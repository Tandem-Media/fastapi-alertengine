from dataclasses import dataclass
from enum import Enum
from typing import Optional


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


@dataclass
class HealthInput:
    """
    Input signals used to compute system health.
    These should come from your AlertEngine evaluation layer.
    """
    p95_latency_ms: float
    baseline_p95_ms: float
    error_rate: float  # 0.0 → 1.0


@dataclass
class HealthResult:
    health_score: int
    status: HealthStatus
    reasons: list[str]


class HealthEngine:
    """
    Converts raw observability signals into a single health score.

    This is NOT ML.
    It is deterministic operational scoring for production systems.
    """

    def __init__(
        self,
        error_weight: float = 200.0,
        latency_weight: float = 0.5,
    ):
        self.error_weight = error_weight
        self.latency_weight = latency_weight

    def calculate(self, data: HealthInput) -> HealthResult:
        score = 100
        reasons = []

        # ----------------------------
        # 1. Error rate penalty
        # ----------------------------
        if data.error_rate > 0:
            penalty = data.error_rate * self.error_weight
            score -= penalty
            reasons.append(f"error_rate penalty: -{penalty:.2f}")

        # ----------------------------
        # 2. Latency deviation penalty
        # ----------------------------
        latency_delta = data.p95_latency_ms - data.baseline_p95_ms

        if latency_delta > 0:
            penalty = latency_delta * self.latency_weight
            penalty = min(penalty, 40)  # cap impact to avoid overreaction
            score -= penalty
            reasons.append(f"latency deviation: -{penalty:.2f}")

        # ----------------------------
        # 3. Clamp score
        # ----------------------------
        score = max(0, min(100, int(score)))

        # ----------------------------
        # 4. Determine status
        # ----------------------------
        if score >= 80:
            status = HealthStatus.HEALTHY
        elif score >= 50:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.CRITICAL

        return HealthResult(
            health_score=score,
            status=status,
            reasons=reasons
        )