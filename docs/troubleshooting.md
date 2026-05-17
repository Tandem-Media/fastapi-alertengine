# Troubleshooting

Quick fixes for the most common FastAPI AlertEngine
onboarding and operational issues.

---

## 1. /health/alerts returns no data

**Why this happens:**
The SDK tracks real traffic through your FastAPI app.
If no requests have been made yet, sample_size will
be 0 and metrics will be null.

**Fix:**
Make a few requests to your app first:

```bash
curl http://localhost:8000/your-endpoint
curl http://localhost:8000/your-endpoint
curl http://localhost:8000/your-endpoint
```

Then check health:

```bash
curl http://localhost:8000/health/alerts
```

**Expected healthy response:**
```json
{
  "status": "ok",
  "health_score": {
    "score": 94,
    "status": "healthy",
    "trend": "stable"
  },
  "metrics": {
    "overall_p95_ms": 84.2,
    "error_rate": 0.0,
    "anomaly_score": 0.1,
    "sample_size": 3
  },
  "alerts": [],
  "mode": "redis",
  "timestamp": "2026-05-14T10:00:00Z"
}
```

**Shortcut — use demo simulation:**
```bash
curl -X POST http://localhost:8000/demo/simulate \
  -H "Content-Type: application/json" \
  -d '{"scenario": "latency_spike", "intensity": "moderate"}'
```

---

## 2. Redis unavailable — memory mode activated

**Why this happens:**
AlertEngine detected that Redis is unreachable and
switched to memory mode. This is intentional
graceful degradation — your app continues running.

**How to verify:**
```bash
curl http://localhost:8000/health/alerts
```

Look for:
```json
{ "mode": "memory" }
```

**What still works in memory mode:**
- Health scoring
- P95 latency tracking
- Error rate detection
- /health/alerts responses
- Demo simulation

**What is affected:**
- Metrics do not persist across restarts
- Shared metric state across multiple instances
  is unavailable

**Fix:**
Set REDIS_URL in your environment:
```bash
export REDIS_URL=redis://localhost:6379/0
```

Or use a managed Redis (Railway, Upstash, Redis Cloud).

---

## 3. Demo simulation returns 403

**Why this happens:**
The /demo/simulate endpoint is disabled in production
or explicitly disabled via environment variable.

**Fix for local development:**
Ensure ENVIRONMENT is not set to "production":
```bash
export ENVIRONMENT=development
```

**Fix for explicit disable:**
Check if ALERTENGINE_DISABLE_DEMO=true is set.
Remove it or set to false.

**To enable demo in production (not recommended):**
```bash
export ALERTENGINE_ENABLE_DEMO=true
```

**Verify:**
```bash
curl -X POST http://localhost:8000/demo/simulate \
  -H "Content-Type: application/json" \
  -d '{"scenario": "latency_spike", "intensity": "mild"}'
```

Expected: HTTP 200 with injected_samples &gt; 0

---

## 4. Orchestrator cannot reach health_url

**Why this happens:**
The most common causes:
- health_url points to localhost (not publicly accessible)
- Wrong port
- Missing /health/alerts path
- Firewall or reverse proxy blocking

**Checklist:**

1. Verify the URL is publicly accessible:
```bash
curl https://your-app.railway.app/health/alerts
```

2. Confirm the response shape is valid:
```json
{
  "status": "ok",
  "health_score": { "score": 94 },
  "metrics": { "overall_p95_ms": 84 }
}
```

3. Check the onboarding response for warnings:
```json
{
  "health_url_reachable": false,
  "health_url_warning": "Could not reach health_url..."
}
```

**Common mistakes:**
- Using http://localhost:8000 instead of the public URL
- Missing the /health/alerts path suffix
- Using the internal Railway URL instead of the
  public domain

**Fix:**
Use the full public URL when onboarding:
```bash
curl -X POST https://orchestrator.up.railway.app/onboard \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "My FastAPI App",
    "health_url": "https://my-app.up.railway.app/health/alerts",
    "whatsapp_numbers": ["+1234567890"],
    "notification_channel": "whatsapp",
    "plan": "developer"
  }'
```

---

## 5. WhatsApp alerts not arriving

**Check these in order:**

**1. Verify the number is verified:**
```bash
curl https://orchestrator.up.railway.app/tenant/{tenant_id}/contacts
```
Expected: verified: 1

**2. Check delivery ledger after triggering a test:**
```bash
curl -X POST https://orchestrator.up.railway.app/tenant/{tenant_id}/test

curl "https://orchestrator.up.railway.app/delivery/{incident_id}?tenant_id={tenant_id}"
```

Look for error field in the delivery log.

**3. Common errors:**

credentials_missing:
TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, or
TWILIO_WHATSAPP_FROM not set in orchestrator
environment.

no_recipients:
No verified WhatsApp numbers on the tenant.
Re-verify the contact.

sandbox_expired (Twilio sandbox):
Send "join {keyword}" to +14155238886 on WhatsApp
to rejoin the sandbox.

**4. Timing:**
WhatsApp delivery typically takes 5-30 seconds
after incident detection. The orchestrator polls
every 5 seconds.

**Do not expose credentials in support requests.**

---

## 6. Recovery approval rejected

**Token already used (409):**
```json
{ "detail": "This recovery token has already been used." }
```
Tokens are single-use for security. Trigger a new
test incident to get a fresh token.

**Token expired (403):**
Recovery tokens expire after 5 minutes. Trigger a
new incident.

**Tenant mismatch (403):**
```json
{ "detail": "You do not have access to this resource." }
```
The token was generated for a different tenant_id.
Use the correct tenant credentials.

**Preview vs authorization:**
- GET /action/recover — preview only, safe to open
- POST /action/recover/confirm — irreversible authorization

Opening the link in WhatsApp previews it safely.
You must tap Authorize Recovery to execute.

---

## 7. Health score remains degraded

**Why this happens:**
AlertEngine uses adaptive thresholds based on your
traffic baseline. After injecting synthetic load or
experiencing a real incident, the score may remain
degraded while the rolling metric window stabilizes.

**How long does recovery take:**
Typically 1-3 minutes after normal traffic resumes,
depending on your rolling window configuration.

**To accelerate recovery:**
Use the recovery simulation:
```bash
curl -X POST http://localhost:8000/demo/simulate \
  -H "Content-Type: application/json" \
  -d '{"scenario": "recovery", "intensity": "severe"}'
```

**Verify recovery:**
```bash
curl http://localhost:8000/health/alerts
```
Watch health_score.trend change from "degrading"
to "stable" or "improving".

---

## 8. What FastAPI AlertEngine is — and is not

**AlertEngine is NOT:**
- A telemetry explorer
- A metrics warehouse
- A Grafana replacement
- A distributed tracing system
- A log aggregation pipeline
- A general observability platform

**AlertEngine IS:**
- Incident intelligence for FastAPI
- Operational cognition — what broke and why
- Human-authorized recovery orchestration
- A sparse, mobile-first operational layer

If you need long-term metric storage, trace
waterfalls, or complex query dashboards, use
Datadog, Grafana, or Prometheus alongside
AlertEngine.

AlertEngine answers: "Is my API degraded right now,
what caused it, and what should I do?"

It does not try to answer everything else.

---

Still stuck? Contact: anchorflowalertengine@outlook.com
