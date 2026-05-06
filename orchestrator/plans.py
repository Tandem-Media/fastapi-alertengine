# orchestrator/plans.py
"""
Plan definitions and tenant plan lookup.

Plans (in ascending order):
  solo       — free tier, 1 service, 50 incidents/month, no DLQ, no voice escalation
  startup    — 5 services, 200 incidents/month, DLQ + voice escalation
  scale      — 20 services, 1000 incidents/month, full feature set
  enterprise — unlimited services and incidents, full feature set
"""

import copy
import time

from pydantic import BaseModel

from tenants import save_tenant


class TenantPlan(BaseModel):
    name: str
    max_services: int               # -1 = unlimited
    included_incidents: int         # -1 = unlimited
    overage_fee_per_incident: float
    has_dlq_access: bool
    has_claude_decision: bool
    has_voice_escalation: bool
    has_custom_thresholds: bool


_PLANS: dict[str, TenantPlan] = {
    "solo": TenantPlan(
        name="solo",
        max_services=1,
        included_incidents=50,
        overage_fee_per_incident=0.0,
        has_dlq_access=False,
        has_claude_decision=True,
        has_voice_escalation=False,
        has_custom_thresholds=False,
    ),
    "startup": TenantPlan(
        name="startup",
        max_services=5,
        included_incidents=200,
        overage_fee_per_incident=0.05,
        has_dlq_access=True,
        has_claude_decision=True,
        has_voice_escalation=True,
        has_custom_thresholds=False,
    ),
    "scale": TenantPlan(
        name="scale",
        max_services=20,
        included_incidents=1000,
        overage_fee_per_incident=0.03,
        has_dlq_access=True,
        has_claude_decision=True,
        has_voice_escalation=True,
        has_custom_thresholds=True,
    ),
    "enterprise": TenantPlan(
        name="enterprise",
        max_services=-1,
        included_incidents=-1,
        overage_fee_per_incident=0.0,
        has_dlq_access=True,
        has_claude_decision=True,
        has_voice_escalation=True,
        has_custom_thresholds=True,
    ),
}

_DEFAULT_PLAN = _PLANS["solo"]


def get_tenant_plan(tenant: dict) -> TenantPlan:
    """Return the TenantPlan for the given tenant dict."""
    plan_name = tenant.get("plan", "solo")
    return _PLANS.get(plan_name, _DEFAULT_PLAN)


def can_monitor_more_services(tenant: dict) -> bool:
    """Return True if the tenant is allowed to monitor an additional service."""
    plan = get_tenant_plan(tenant)
    if plan.max_services == -1:
        return True
    current = tenant.get("service_count", 1)
    return int(current) < plan.max_services


def incident_quota_remaining(tenant: dict) -> int:
    """Return how many incidents remain in the tenant's monthly quota.

    Returns -1 when the plan has unlimited incidents.
    """
    plan = get_tenant_plan(tenant)
    if plan.included_incidents == -1:
        return -1
    used = tenant.get("incident_count", 0)
    return max(plan.included_incidents - int(used), 0)


def increment_incident_count(tenant: dict) -> dict:
    """Increment the tenant's incident counter, persist, and return the updated record."""
    tenant = copy.deepcopy(tenant)
    tenant["incident_count"] = tenant.get("incident_count", 0) + 1
    tenant["last_updated"] = time.time()
    save_tenant(tenant)
    return tenant
