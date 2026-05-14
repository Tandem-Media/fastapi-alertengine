# Incident Console — Architecture Specification

## Philosophy
The Incident Console is NOT a dashboard.
It is NOT a chart wall.
It is NOT a Grafana replacement.

It is a sparse, mobile-first operational surface
for incident cognition and recovery authorization.

Design principles:
- Every element earns its place by reducing
  cognitive load during an incident
- No charts unless they directly inform a decision
- Optimized for reading on a phone at 2am
- Information hierarchy: current state → decision
  needed → history
- Never show data that does not drive action

## Information Architecture

### 1. Command Strip (always visible, top)
- Current system status: HEALTHY / DEGRADED / INCIDENT
- Active incident count
- Last updated timestamp
- Tenant name

### 2. Active Incident Card (center, dominant)
Visible only when incident is active.

Fields:
- Incident ID
- Service name
- Health score (large number, color-coded)
- Claude diagnosis (plain English, 2-3 sentences)
- Confidence score (0-100%)
- Stage: DETECTED / PROPOSED / VALIDATED /
         AUTHORIZED / EXECUTED / RESOLVED
- Duration (counting up)
- Recovery URL status: PENDING / PREVIEW / AUTHORIZED

### 3. Approval Panel
Visible only when recovery is pending authorization.

Elements:
- What will happen (plain English)
- Who requested it (Claude AI)
- Confidence level
- Expiry countdown (5 minutes)
- [Preview Recovery] button → GET (safe, no side effects)
- [Authorize Recovery] button → POST (irreversible)

Design rule:
The Authorize button must require deliberate action.
Consider: hold-to-confirm, not single tap.

### 4. Incident Timeline (below active card)
Horizontal strip showing lifecycle stages:

DETECTED → PROPOSED → VALIDATED → AUTHORIZED
→ EXECUTED → RESOLVED

Rules:
- Completed stages: solid color with timestamp
- Active stage: pulsing glow
- Future stages: dim

### 5. Delivery State (collapsed by default)
Shows notification delivery attempts:
- Provider (WhatsApp/Telegram/Slack/Webhook)
- Status (delivered/failed/pending)
- Timestamp
- Error if failed

### 6. Audit Trail (collapsed by default)
Immutable log of all events:
- Stage transitions
- Authorization events
- Recovery executions
- Tenant: who approved, when, from what token

### 7. DLQ Panel (Startup+ only)
Shows failed jobs available for manual replay.
Empty state is a positive signal.

### 8. Health Trend (minimal, optional)
If shown: single sparkline only.
Last 60 minutes. No axes labels.
Purpose: show trajectory, not data.

## What Should NOT Appear
- Raw request logs
- Trace waterfalls
- Infrastructure topology maps
- Multiple chart panels
- Alert configuration forms
- User management
- Billing information
- Generic metrics dashboards

## Mobile-First Rules
- Primary use case: phone, one hand, under stress
- Font sizes: minimum 16px for all operational data
- Buttons: minimum 48px touch targets
- Color coding: green / amber / red only
- No hover states as primary interaction
- Recovery authorization must work on mobile Safari

## Free SDK vs Paid Orchestrator Visibility

| Element | Free SDK | Paid Orchestrator |
|---|---|---|
| Health score | Via /health/alerts | Full console |
| Active incident | Not shown | Full card |
| Claude diagnosis | Not shown | Full panel |
| Approval panel | Not shown | Full panel |
| Timeline | Not shown | Full strip |
| Delivery state | Not shown | Full panel |
| Audit trail | Not shown | Full log |
| DLQ | Not shown | Startup+ only |

## Operational Memory (Future)
The Incident Console will eventually surface
tenant-specific operational intelligence:

- "This service typically degrades after deploys"
- "Redis saturation historically precedes queue collapse"
- "This class of incident self-resolves in 4 minutes"
- "These alerts are noise for this tenant"

This accumulated operational memory per tenant
is the long-term product moat.
It cannot be replicated without tenure.

## Terminology
Do NOT use: dashboard, metrics, telemetry, observability
DO use: incident console, operational state, incident
        cognition, recovery authorization, audit trail

## Implementation Status
Planned. Not yet built.
This document defines the architecture for the
first production Incident Console implementation.
