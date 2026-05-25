# AlertEngine — Requirements
Version: 1.0
Status: Live in production
Last updated: May 2026

---

## Purpose Statement

FastAPI AlertEngine detects API degradation in FastAPI apps,
diagnoses root cause with AI, proposes recovery, requires explicit
human authorization via mobile, executes only after approval,
and records everything immutably.

---

## REQ-001: Instrumentation

**REQ-001-A**: The free SDK SHALL instrument any FastAPI app with one line of code.
- Acceptance: `instrument(app)` added to a FastAPI app exposes /health/alerts

**REQ-001-B**: The SDK SHALL track P95 latency on a rolling window.
- Acceptance: /health/alerts returns `overall_p95_ms` reflecting the 95th percentile

**REQ-001-C**: The SDK SHALL track error rate (4xx and 5xx) separately.
- Acceptance: /health/alerts returns `error_rate` as a float between 0.0 and 1.0

**REQ-001-D**: The SDK SHALL compute a composite health score 0-100.
- Acceptance: /health/alerts returns `health_score.score` as an integer

**REQ-001-E**: The SDK SHALL detect anomalies against an adaptive baseline.
- Acceptance: /health/alerts returns `anomaly_score` reflecting deviation from baseline

**REQ-001-F**: The SDK SHALL operate without Redis if unavailable.
- Acceptance: Removing Redis does not crash the host FastAPI application

**REQ-001-G**: The SDK SHALL have zero required dependencies beyond FastAPI.
- Acceptance: pip install fastapi-alertengine installs without pulling additional packages

---

## REQ-002: Detection

**REQ-002-A**: The orchestrator SHALL poll /health/alerts every 5 seconds per tenant.
- Acceptance: loop.py polls each active tenant at LOOP_INTERVAL_S intervals

**REQ-002-B**: The orchestrator SHALL open an incident when health score drops below threshold.
- Acceptance: An incident is created when score < POLICY_MIN_SCORE_TO_ALERT (default 70)

**REQ-002-C**: The orchestrator SHALL prevent duplicate incidents per tenant.
- Acceptance: A second incident cannot be opened while one is active for the same tenant

**REQ-002-D**: The orchestrator SHALL classify incidents by severity (warning/critical).
- Acceptance: Incidents have a severity field populated by policy.py

---

## REQ-003: Diagnosis

**REQ-003-A**: The orchestrator SHALL use Claude AI to diagnose incidents in plain English.
- Acceptance: claude_engine.py returns a diagnosis string and confidence score

**REQ-003-B**: The orchestrator SHALL suppress AI diagnosis below confidence threshold.
- Acceptance: Diagnoses with confidence < 0.6 are suppressed; raw metrics shown instead

**REQ-003-C**: The orchestrator SHALL fall back to rule-based classification if AI is unavailable.
- Acceptance: Incidents are still created and notified if Claude API is unreachable

**REQ-003-D**: The orchestrator SHALL include raw metrics alongside every AI diagnosis.
- Acceptance: WhatsApp/Telegram message includes score, P95, and error rate

---

## REQ-004: Notification

**REQ-004-A**: The orchestrator SHALL deliver alerts via WhatsApp or Telegram.
- Acceptance: Tenant receives a message within 10 seconds of incident detection

**REQ-004-B**: The orchestrator SHALL include a recovery link in every alert.
- Acceptance: Message contains a URL pointing to /action/recover?token=...

**REQ-004-C**: The orchestrator SHALL log every delivery attempt.
- Acceptance: delivery_ledger.py records provider, success/failure, and timestamp

**REQ-004-D**: The orchestrator SHALL fall back to webhook if primary channel fails.
- Acceptance: FALLBACK_WEBHOOK_URL is called if WhatsApp/Telegram delivery fails

**REQ-004-E**: The orchestrator SHALL support Slack for Startup+ plans.
- Acceptance: Tenants on Startup plan receive Slack notifications if configured

---

## REQ-005: Authorization

**REQ-005-A**: No recovery action SHALL execute without explicit human authorization.
- Acceptance: POST /action/recover/confirm requires a valid JWT token

**REQ-005-B**: Recovery tokens SHALL be tenant-scoped.
- Acceptance: A token generated for tenant A cannot authorize an action for tenant B

**REQ-005-C**: Recovery tokens SHALL be single-use.
- Acceptance: A token used once returns 403 on all subsequent requests

**REQ-005-D**: Recovery tokens SHALL expire after 5 minutes.
- Acceptance: Tokens with TTL > 300 seconds return 403

**REQ-005-E**: GET /action/recover SHALL be read-only.
- Acceptance: Fetching the recovery link does not mutate any state

**REQ-005-F**: Token validation SHALL be atomic.
- Acceptance: Concurrent requests with the same token — exactly 1 succeeds

---

## REQ-006: Audit

**REQ-006-A**: Every stage transition SHALL be logged immutably.
- Acceptance: audit.py appends an event for every state change

**REQ-006-B**: The audit log SHALL be append-only.
- Acceptance: No API endpoint allows deletion or modification of audit events

**REQ-006-C**: The audit log SHALL retain events for 7 days.
- Acceptance: Events older than 7 days are expired from Redis

**REQ-006-D**: The full incident timeline SHALL be reconstructable from the audit log.
- Acceptance: GET /audit/{incident_id} returns all events in chronological order

---

## REQ-007: Security

**REQ-007-A**: Cross-tenant data access SHALL be blocked.
- Acceptance: Requesting another tenant's incidents or deliveries returns 403

**REQ-007-B**: The system SHALL pass an adversarial audit.
- Acceptance: 10/10 adversarial checks passed (documented in SECURITY.md)

**REQ-007-C**: The system SHALL fail closed on Redis errors.
- Acceptance: Redis unavailability does not allow unauthorized token reuse

**REQ-007-D**: The system SHALL use Lua scripts for atomic Redis operations.
- Acceptance: Circuit breaker resets and token validation use Redis Lua scripts

---

## REQ-008: Multi-Tenancy

**REQ-008-A**: The orchestrator SHALL support multiple tenants simultaneously.
- Acceptance: loop.py polls all active tenants in parallel

**REQ-008-B**: Tenants SHALL be isolated from each other at every layer.
- Acceptance: Data, incidents, tokens, and audit logs are scoped to tenant_id

**REQ-008-C**: Tenants SHALL be gated by plan features.
- Acceptance: Hobby tenants cannot access AI diagnosis or WhatsApp

---

## REQ-009: Reliability

**REQ-009-A**: The orchestrator SHALL operate in degraded mode during partial outages.
- Acceptance: NORMAL/DEGRADED/EMERGENCY modes with automatic transitions

**REQ-009-B**: Failed notifications SHALL be captured in a dead letter queue.
- Acceptance: dlq.py captures undeliverable incidents for replay

**REQ-009-C**: The circuit breaker SHALL be distributed across workers.
- Acceptance: circuit_breaker.py uses Redis, not in-memory state

---

## REQ-010: Compliance

**REQ-010-A**: The system SHALL be suitable for SOC 2 audit preparation.
- Acceptance: Human authorization records, immutable audit trail, and tenant isolation
  are all present and documented

**REQ-010-B**: The system SHALL support fintech use cases.
- Acceptance: Deployed and tested on live fintech infrastructure in Zimbabwe

