import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("fastapi_alertengine.demo")

DEMO_DISABLED = os.getenv(
    "ALERTENGINE_DISABLE_DEMO", ""
).lower() in ("true", "1", "yes")

INTENSITY_MULTIPLIERS = {
    "mild":     {"latency": 1.5, "error_rate": 0.05,
                 "samples": 50},
    "moderate": {"latency": 3.0, "error_rate": 0.15,
                 "samples": 100},
    "severe":   {"latency": 6.0, "error_rate": 0.35,
                 "samples": 150},
}

VALID_SCENARIOS = {
    "latency_spike", "error_surge", "anomaly", "recovery"
}
VALID_INTENSITIES = {"mild", "moderate", "severe"}


def _is_demo_allowed() -> bool:
    if DEMO_DISABLED:
        return False
    env = os.getenv("ENVIRONMENT", "development").lower()
    if env == "production":
        return os.getenv(
            "ALERTENGINE_ENABLE_DEMO", ""
        ).lower() in ("true", "1", "yes")
    return True


def register_demo_routes(app: Any, engine: Any) -> None:
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    class SimulateRequest(BaseModel):
        scenario: str = "latency_spike"
        duration_seconds: int = 30
        intensity: str = "moderate"

    @app.post(
        "/demo/simulate",
        include_in_schema=False,
        response_class=JSONResponse,
    )
    async def simulate_degradation(
        req: SimulateRequest,
    ) -> JSONResponse:
        if not _is_demo_allowed():
            raise HTTPException(
                status_code=403,
                detail={
                    "detail": "Demo simulation disabled.",
                    "code": "DEMO_DISABLED",
                },
            )

        scenario = req.scenario.lower()
        intensity = req.intensity.lower()

        if scenario not in VALID_SCENARIOS:
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": f"Invalid scenario. "
                    f"Valid: {sorted(VALID_SCENARIOS)}",
                    "code": "INVALID_SCENARIO",
                },
            )

        if intensity not in VALID_INTENSITIES:
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": f"Invalid intensity. "
                    f"Valid: {sorted(VALID_INTENSITIES)}",
                    "code": "INVALID_INTENSITY",
                },
            )

        multipliers = INTENSITY_MULTIPLIERS[intensity]
        sample_count = multipliers["samples"]
        now = time.time()
        samples = []

        if scenario == "latency_spike":
            baseline = 80.0
            peak = baseline * multipliers["latency"] * 10
            for i in range(sample_count):
                progress = i / max(sample_count - 1, 1)
                latency = baseline + (
                    peak - baseline) * progress
                latency += random.gauss(0, latency * 0.1)
                samples.append({
                    "latency_ms": max(1.0, round(
                        latency, 1)),
                    "status_code": 200,
                    "path": "/api/demo",
                    "method": "GET",
                    "timestamp": now - (
                        sample_count - i) * 0.2,
                })

        elif scenario == "error_surge":
            for i in range(sample_count):
                is_error = random.random() < multipliers[
                    "error_rate"]
                status = random.choice(
                    [500, 502, 503]) if is_error else 200
                latency = random.gauss(
                    50, 10) if is_error else random.gauss(
                    150, 30)
                samples.append({
                    "latency_ms": max(1.0, round(
                        latency, 1)),
                    "status_code": status,
                    "path": "/api/demo",
                    "method": "POST",
                    "timestamp": now - (
                        sample_count - i) * 0.2,
                })

        elif scenario == "anomaly":
            for i in range(sample_count):
                latency = random.gauss(
                    20, 5) if random.random() < 0.4 \
                    else random.gauss(2000, 300)
                samples.append({
                    "latency_ms": max(1.0, round(
                        latency, 1)),
                    "status_code": 200,
                    "path": "/api/demo",
                    "method": "GET",
                    "timestamp": now - (
                        sample_count - i) * 0.2,
                })

        elif scenario == "recovery":
            for i in range(sample_count):
                progress = i / max(sample_count - 1, 1)
                latency = 3000 - (3000 - 80) * progress
                latency += random.gauss(0, latency * 0.05)
                samples.append({
                    "latency_ms": max(1.0, round(
                        latency, 1)),
                    "status_code": 200,
                    "path": "/api/demo",
                    "method": "GET",
                    "timestamp": now - (
                        sample_count - i) * 0.2,
                })

        injected = 0
        try:
            for sample in samples:
                if hasattr(engine, "enqueue"):
                    engine.enqueue(sample)
                else:
                    engine.enqueue_metric(sample)
                injected += 1
        except Exception as exc:
            logger.warning(
                "Demo injection partial: %s", exc)

        expected = {
            "latency_spike": {
                "mild": "warning",
                "moderate": "degraded",
                "severe": "critical"
            },
            "error_surge": {
                "mild": "warning",
                "moderate": "critical",
                "severe": "critical"
            },
            "anomaly": {
                "mild": "warning",
                "moderate": "warning",
                "severe": "degraded"
            },
            "recovery": {
                "mild": "improving",
                "moderate": "improving",
                "severe": "improving"
            },
        }.get(scenario, {}).get(intensity, "degraded")

        return JSONResponse(content={
            "scenario": scenario,
            "intensity": intensity,
            "duration_seconds": req.duration_seconds,
            "injected_samples": injected,
            "expected_health_drop": expected,
            "monitor_at": "/health/alerts",
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(timespec="seconds").replace(
                "+00:00", "Z"),
            "message": (
                f"Synthetic {scenario} injected. "
                f"Watch /health/alerts for changes. "
                f"Expected state: {expected}."
            ),
            "next_steps": [
                "GET /health/alerts — watch score change",
                "POST /demo/simulate with "
                "scenario=recovery to restore health",
            ],
        })

    logger.info(
        "Demo endpoint registered: POST /demo/simulate"
    )
