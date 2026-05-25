# AlertEngine — Design
Version: 1.1
Status: Live in production
Last updated: May 2026

---

## System Overview

AlertEngine is a two-layer system:

1. **Free SDK** (`fastapi_alertengine/`) — local incident sensing, MIT licensed
2. **Paid Orchestrator** (`orchestrator/`) — managed incident command, commercial

The SDK runs inside the customer's FastAPI app.
The orchestrator runs as a separate service on Railway.
They communicate only through the /health/alerts HTTP endpoint.
The orchestrator never has direct access to the customer's database or code.

---

## Architecture

```
Customer FastAPI App
├── instrument(app)                    ← SDK middleware
├── GET /health/alerts                 ← SDK endpoint
└── GET /metrics/history               ← SDK endpoint

AlertEngine Orchestrator (Railway)
├── loop.py                            ← polls /health/alerts every 5s
├── pipeline.py                        ← incident state machine
├── policy.py                          ← deterministic threshold evaluation
├── claude_engine.py                   ← AI diagnosis (optional enrichment)
├── baseline.py                        ← per-tenant EMA baseline memory
├── diagnosis_memory.py                ← multi-turn diagnosis history
├── notifications.py                   ← multi-channel dispatch
├── providers/                         ← WhatsApp, Telegram, Slack, Webhook
├── action_generator.py                ← JWT recovery token creation
├── audit.py                           ← immutable forensic log
├── tenants.py                         ← tenant registry
├── plans.py                           ← billing tiers and feature gates
├── circuit_breaker.py                 ← distributed Redis-backed CB
└── degraded.py                        ← degraded mode management
```

---

## Data Flow

### Normal operation
```
FastAPI Request
  → RequestMetricsMiddleware (latency + status code)
  → Redis Streams (append-only event log)
  → Alert Engine (P95 + error rate + anomaly scoring)
  → /health/alerts (status: ok | warning | critical)

Orchestrator loop (every 5s)
  → GET /health/alerts
  → baseline.py (update EMA on healthy polls)
  → policy.py (deterministic threshold check)
  → [if incident] pipeline.py opens incident
  → claude_engine.py (AI diagnosis with baseline context + diagnosis history)
  → notifications.py (WhatsApp/Telegram message)
  → action_generator.py (JWT recovery token)
  → audit.py (DETECTED event logged)
  → diagnosis_memory.py (record Claude turn for continuity)
```

### Recovery flow
```
Engineer receives WhatsApp message
  → taps recovery link
  → GET /action/recover?token=... (preview, read-only)
  → engineer reviews: raw metrics + AI diagnosis + proposed action
  → POST /action/recover/confirm (irreversible authorization)
  → action_generator.py validates JWT (atomic Redis SET NX)
  → recovery executes
  → audit.py (AUTHORIZED + EXECUTED events logged)
  → diagnosis_memory.py (history cleared on resolve)
  → engineer receives confirmation message
```

---

## Incident State Machine

```
[OPEN] → detected, AI diagnosis running
  ↓
[PROPOSED] → WhatsApp/Telegram sent, awaiting authorization
  ↓
[AUTHORIZED] → engineer tapped approve, executing
  ↓
[RESOLVED] → health score recovered, confirmation sent
  ↓
[CLOSED] → final state, audit trail complete

Alternative paths:
[PROPOSED] → [EXPIRED] — token TTL exceeded, no action taken
[ANY] → [FAILED] — unrecoverable error, captured in DLQ
```

---

## Key Components

### SDK: RequestMetricsMiddleware
- Measures latency per request (start/end time)
- Records HTTP status codes
- Writes to Redis Streams (or memory fallback)
- Never raises — logs and continues
- Zero performance overhead design

### SDK: Alert Engine
- Computes P95 latency on rolling window (deque maxlen=1000)
- Computes error rate (4xx/5xx ratio)
- Computes anomaly score (deviation from baseline)
- Computes composite health score 0-100
- Applies adaptive thresholds that learn normal traffic patterns

### Orchestrator: policy.py
- Pure deterministic logic — no AI
- Evaluates P95, error rate, and anomaly score against thresholds
- Returns: escalate | monitor | resolve
- This is the critical path — AI is never on the critical path

### Orchestrator: baseline.py
- Per-tenant Exponential Moving Average (EMA) baseline
- Stores: normal P95, error rate, RPM per tenant
- Redis key per tenant, 24h TTL, O(1) GET + SET per loop tick
- Alpha = 0.3 — adapts to traffic pattern shifts within ~10 samples
- Minimum 10 samples before reporting (avoids cold-start noise)
- Used by claude_engine.py to provide deviation context:
  "P95 is 43x your normal baseline of 120ms"
- Updated on every healthy poll, skipped during incidents

### Orchestrator: diagnosis_memory.py
- Per-incident multi-turn diagnosis history
- Stores last 3 Claude decisions per incident in Redis
- Injected into Claude message list as alternating assistant/user turns
- Prevents diagnosis flip-flopping across polling cycles
- Cleared automatically when incident resolves
- 24h TTL — incidents shouldn't last longer
- ~240-360 extra input tokens per call at MAX_HISTORY=3

### Orchestrator: claude_engine.py
- Receives: health metrics + baseline context + diagnosis history
- Uses native Anthropic tool use — schema-validated output guaranteed
- No JSON parsing, no markdown stripping, no retry for parse failures
- Two few-shot examples in system prompt — reduces Haiku format violations
- Confidence gating: < 0.6 → diagnosis suppressed, raw metrics only
- Claude Haiku for fast text decisions
- Claude Sonnet for vision and complex diagnosis
- Never blocks incident creation or notification
- Returns structured decision: action + reason + confidence + whatsapp_message

### Orchestrator: action_generator.py
- Creates JWT tokens: tenant_id + incident_id + action + exp
- Signed with ALERT_SECRET (HMAC-SHA256)
- TTL: 300 seconds (5 minutes)
- Single-use: validated with Redis SET NX (atomic)
- Fails closed on Redis error

### Orchestrator: audit.py
- Append-only Redis list per incident
- Events: DETECTED, PROPOSED, AUTHORIZED, EXECUTED, RESOLVED, FAILED
- Each event: timestamp, stage, decision, reason, confidence, tenant_id
- Retention: 7 days
- Never mutable after write

### Orchestrator: circuit_breaker.py
- Redis-backed, distributed across all workers
- Scoped per provider per tenant
- Threshold: 3 failures → 60s cooldown
- Reset: Lua atomic script (not r.delete — race condition)
- Fails open on Redis error (allow send)

---

## Security Model

### JWT Recovery Tokens
```
Payload: {
  tenant_id: str,
  incident_id: str,
  action: str,
  exp: timestamp,
  jti: uuid  ← unique token ID for Redis burn
}
Signing: HMAC-SHA256 with ALERT_SECRET
TTL: 300 seconds
Single-use: Redis SET NX on jti
```

### Cross-Tenant Isolation
- tenant_id validated against JWT payload on every endpoint
- No endpoint returns data for a different tenant_id
- Adversarial audit confirmed: 0/20 cross-tenant access attempts succeeded

### Degraded Mode
```
NORMAL    → all systems operational
DEGRADED  → Redis degraded, notifications continue, mutations gated
EMERGENCY → Redis unavailable, SDK memory fallback active
```

---

## Tenant Data Model

```python
tenant = {
  "tenant_id":           str,      # 8-char UUID prefix
  "service_name":        str,
  "health_url":          str,      # /health/alerts endpoint
  "status":              str,      # pending_verification | active | inactive
  "notification_channel": str,     # whatsapp | telegram | slack
  "plan":                str,      # hobby | developer | solo | startup | scale | enterprise
  "incident_count":      int,
  "incidents_this_month": int,
  "incidents_reset_at":  float,    # Unix timestamp of last monthly reset
  "billing_cycle_start": float,
  "schema_version":      str,      # for future migrations
  "created_at":          float,
}
```

---

## Redis Key Space

| Key Pattern | Content | TTL |
|-------------|---------|-----|
| `orchestrator:baseline:{tenant_id}` | EMA baseline dict | 24h |
| `orchestrator:diagnosis_history:{incident_id}` | Last 3 Claude turns | 24h |
| `orchestrator:active_incident:{tenant_id}` | Active incident ID | 24h |
| `orchestrator:incident:{incident_id}` | Incident record | 24h |
| `orchestrator:audit:{incident_id}` | Audit log list | 7d |
| `orchestrator:cb:{provider}:{tenant_id}` | Circuit breaker state | 1h |
| `orchestrator:active_tenants` | Set of active tenant IDs | permanent |
| `tenant:{tenant_id}` | Tenant record | permanent |

---

## Plan Feature Gates

| Feature | Hobby | Developer | Solo | Startup | Scale | Enterprise |
|---------|-------|-----------|------|---------|-------|------------|
| Services | 1 | 1 | 3 | 10 | 20 | Custom |
| Incidents/mo | 5 | 10 | 50 | 200 | 1000 | Custom |
| WhatsApp | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Telegram | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| AI diagnosis | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Baseline memory | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Diagnosis memory | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Slack | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Voice escalation | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ |
| DLQ access | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ |
| Custom thresholds | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| REDIS_URL | Yes | Redis connection URL |
| ALERTENGINE_BASE_URL | Yes | Public orchestrator URL |
| ANTHROPIC_API_KEY | Yes | Claude AI API key |
| ALERT_SECRET | Yes | JWT signing secret (min 32 chars) |
| BASELINE_EMA_ALPHA | No | EMA weight for new samples (default: 0.3) |
| BASELINE_MIN_SAMPLES | No | Min samples before baseline reporting (default: 10) |
| TWILIO_ACCOUNT_SID | Twilio only | Twilio account SID |
| TWILIO_AUTH_TOKEN | Twilio only | Twilio auth token |
| TWILIO_WHATSAPP_FROM | Twilio only | Sender WhatsApp number |
| SENT_API_KEY | Sent.dm only | Sent.dm API key |
| SENT_PHONE_ID | Sent.dm only | Sent.dm phone ID |
| LOOP_INTERVAL_S | No | Polling interval (default: 5) |
| POLICY_MIN_SCORE_TO_ALERT | No | Incident threshold (default: 70) |

---

## Repository Structure

```
fastapi_alertengine/     ← Free SDK (MIT licensed)
  middleware.py          ← RequestMetricsMiddleware
  engine.py             ← Core alert engine
  intelligence.py       ← Adaptive thresholds, health scoring
  actions/              ← Recovery suggestions and JWT tokens
  storage.py            ← Redis Streams persistence

orchestrator/           ← Paid managed service (commercial)
  loop.py              ← Multi-tenant polling
  pipeline.py          ← Incident state machine
  policy.py            ← Deterministic threshold evaluation
  claude_engine.py     ← AI diagnosis (tool use, few-shot, hardened)
  baseline.py          ← Per-tenant EMA baseline memory
  diagnosis_memory.py  ← Multi-turn diagnosis history
  notifications.py     ← Multi-channel dispatch
  providers/           ← WhatsApp, Telegram, Slack, Sent.dm, Webhook
  action_generator.py  ← JWT recovery token creation
  audit.py             ← Immutable forensic log
  delivery_ledger.py   ← Delivery attempt logging
  tenants.py           ← Tenant registry (Redis-backed)
  plans.py             ← Billing tiers and feature gates
  circuit_breaker.py   ← Distributed Redis-backed circuit breaker
  degraded.py          ← Degraded mode management
  dlq.py               ← Dead letter queue
  memory.py            ← Active incident tracking
  onboard.py           ← Standard phone-verified onboarding
  main.py              ← FastAPI app entry point

tests/                  ← 232 tests, Python 3.10/3.11/3.12
docs/                   ← Landing page (GitHub Pages)
examples/               ← Demo scripts
```

---

## Deployment

- **SDK**: PyPI package (`pip install fastapi-alertengine`)
- **Orchestrator**: Railway (auto-deploy from main branch)
- **Redis**: Railway Redis plugin
- **CI**: GitHub Actions (pytest, Python 3.10/3.11/3.12)
- **Docs**: GitHub Pages (docs/index.html)

---

## What Must Not Change Without Constitution Review

- The /health/alerts response schema
- The JWT token structure
- The audit log event format
- The tenant data model schema version
- The GET/POST split for recovery (preview vs execute)
- The confidence gating threshold (0.6)
- The token TTL (300 seconds)
- The EMA alpha default (0.3) — changing affects all tenant baselines
- The diagnosis history MAX_HISTORY (3) — changing affects token costs
