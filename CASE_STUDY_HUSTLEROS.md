# Case Study: HustlerOS

## What is HustlerOS?
WhatsApp-native operational platform for informal
businesses in Zimbabwe. Merchants manage orders,
payments, and deliveries entirely through WhatsApp.

## Integration
from fastapi_alertengine import instrument
instrument(app)

Exposes /health/alerts. AlertEngine orchestrator
polls every 5 seconds.

## Live Production State
https://hustleros-production.up.railway.app

- Database: connected
- Redis: connected
- AlertEngine: instrumented
- Arq worker: running
- Queues: healthy

## Business Incidents
HustlerOS emits business incidents to AlertEngine:
payment timeouts, delivery failures, webhook drops.
These degrade /health/alerts and trigger WhatsApp
recovery alerts.

## Outcome
Every AlertEngine feature validated against real
production workload in Zimbabwe.
