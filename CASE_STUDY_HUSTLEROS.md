# Case Study: HustlerOS

## What is HustlerOS?
HustlerOS is a WhatsApp-native operational platform for
informal businesses in Zimbabwe. Merchants create orders,
report payments, assign deliveries, and manage customers
entirely through WhatsApp messages — no app required.

## Why AlertEngine?
HustlerOS runs on FastAPI with PostgreSQL and Redis.
A payment timeout, delivery failure, or webhook drop
is a real business event — not just a system metric.
AlertEngine monitors HustlerOS infrastructure and
business health simultaneously.

## Integration
HustlerOS instruments AlertEngine with one line:

from fastapi_alertengine import instrument
instrument(app)

This exposes /health/alerts with live health scoring.
AlertEngine orchestrator polls this endpoint every 5
seconds from the managed control plane.

## Live Production State
HustlerOS is monitored in production at:
https://hustleros-production.up.railway.app

Live status:
- Database: connected
- Redis: connected
- AlertEngine: instrumented
- Arq worker: running (payment timeout detection)
- Queues: healthy

## Business Incident Integration
HustlerOS emits business incidents to AlertEngine:

emit_incident(
    type="PAYMENT_TIMEOUT",
    severity="high",
    service="hustleros",
    metadata={
        "order_id": order_id,
        "amount": amount,
        "timeout_after_s": 86400,
    }
)

This degrades the /health/alerts health score,
which AlertEngine detects and escalates to WhatsApp.

## Recovery Flow
When HustlerOS degrades:
1. AlertEngine detects within 5 seconds
2. Claude diagnoses root cause in plain English
3. WhatsApp alert sent to verified operator number
4. Operator taps recovery link (preview only)
5. Operator confirms authorization (POST — irreversible)
6. Recovery executes
7. Audit trail written

## Outcome
HustlerOS is the first real AlertEngine tenant.
Every feature of the orchestrator has been validated
against a live production workload in Zimbabwe.
