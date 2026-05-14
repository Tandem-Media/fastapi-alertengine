# Known Limitations

FastAPI AlertEngine is deliberately focused.
This document explains what the product does not do
and why — intentionally.

Operational trust increases when founders are honest
about tradeoffs.

---

## What AlertEngine does not do

### Not a full observability suite
AlertEngine does not replace Datadog, Prometheus,
or Grafana. It does not provide:
- Distributed tracing
- Log aggregation
- Long-term metric warehousing
- Arbitrary query builders
- Custom dashboard widgets

**Why:** These tools solve a different problem.
AlertEngine solves incident cognition and recovery
coordination. Use both if you need both.

### Not Kubernetes-native
AlertEngine does not provide:
- Kubernetes operator
- Helm charts
- Pod-level health monitoring
- Container orchestration integration

**Why:** Operational simplicity is a design
principle. Kubernetes adds complexity that conflicts
with the plug-and-play philosophy.

### Not distributed tracing
AlertEngine does not instrument:
- Individual function calls
- Database query traces
- External API call chains
- Span-level latency breakdown

**Why:** Trace-level debugging is a different
workflow. AlertEngine focuses on service-level
health, not request-level traces.

### Not a log pipeline
AlertEngine does not:
- Ingest application logs
- Provide log search or filtering
- Aggregate structured log events
- Replace Loki, Papertrail, or CloudWatch Logs

**Why:** Log archaeology is separate from incident
intelligence. AlertEngine tells you the service is
degraded — logs tell you why at the line level.

### Not autonomous
AlertEngine does not:
- Execute recovery actions automatically
- Scale infrastructure without approval
- Restart services without human authorization
- Make operational decisions autonomously

**Why:** Human authorization is a product principle,
not a limitation. Autonomous systems create trust
problems in production. AlertEngine keeps humans
in the loop.

### Not a multi-cloud infrastructure monitor
AlertEngine monitors FastAPI applications.
It does not monitor:
- Cloud provider health (AWS, GCP, Azure)
- Database server health directly
- CDN or DNS health
- Network infrastructure

**Why:** Scope matters. FastAPI application health
is the focus. Infrastructure health is a different
layer.

### Not long-term analytics
AlertEngine's metric windows are optimized for
real-time incident detection, not historical
analysis. It does not provide:
- Week-over-week trend analysis
- SLA reporting
- Capacity planning data
- Business intelligence dashboards

**Why:** Operational intelligence is time-sensitive.
Historical analysis belongs in a data warehouse,
not an incident response layer.

---

## Intentional design decisions

### Sparse UI
The Incident Console is intentionally minimal.
No drag-and-drop widget systems. No chart walls.
No telemetry archaeology interfaces.

**Why:** Cognitive load during incidents should be
minimized, not maximized. Every UI element must
reduce time-to-decision.

### WhatsApp as interrupt layer
AlertEngine uses WhatsApp (and Telegram/Slack) as
the primary notification channel — not email.

**Why:** WhatsApp reaches engineers where they
actually are, especially on mobile. Email does not
interrupt reliably. Slack requires workspace access.

### No auto-remediation
Recovery requires explicit human authorization.
The system proposes — you approve.

**Why:** Autonomous remediation in production
creates liability, trust issues, and unexpected
cascading failures. The human stays in control.

### Memory fallback over failure
When Redis is unavailable, AlertEngine switches to
memory mode rather than crashing or returning errors.

**Why:** The monitor should never become the outage.
Graceful degradation is more important than strict
Redis dependency.

### Claude AI dependency
The managed orchestrator uses Claude AI for root-cause
diagnosis. If Claude is unavailable:
- Incident detection continues normally
- WhatsApp/Telegram alerts still fire
- Diagnosis falls back to rule-based classification
- Human authorization is still required

Core incident response never depends solely on AI
availability.

---

## Roadmap items (not yet built)

These are known gaps being actively considered:

- Self-hosted orchestrator option
- Incident memory (tenant-specific operational history)
- Webhook-based health push (vs poll-based)
- Multi-region orchestrator deployment
- Native Slack app (beyond webhook integration)
- Incident Console web UI (production-grade)
- SDK support for other Python frameworks (Django,
  Flask, Starlette)

---

## Summary

AlertEngine does one thing well:

**FastAPI incident intelligence with human-approved
recovery.**

If you need something outside that scope, use the
right tool for the job. AlertEngine is designed to
complement your stack, not replace it.

Questions: anchorflow@outlook.com
