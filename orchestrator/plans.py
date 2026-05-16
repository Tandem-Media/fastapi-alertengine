# orchestrator/plans.py
"""
Plan definitions and tenant plan lookup.

Plans (in ascending order):
  hobby      — entry tier, 1 service, 5 incidents/month, Telegram, no AI decisions
  developer  — developer tier, 1 service, 10 incidents/month, WhatsApp, AI decisions
  solo       — free tier, 1 service, 50 incidents/month, no DLQ, no voice escalation
  startup    — 5 services, 200 incidents/month, DLQ + voice escalation
  teams      — 20 services, 1000 incidents/month, full feature set
  enterprise — unlimited services and incidents, full feature set
"""

import copy
import time
from typing import Literal, Dict

from pydantic import BaseModel


PlanName = Literal["hobby", "developer", "solo", "startup", "scale", "teams", "enterprise"]


class TenantPlan(BaseModel):
    name: PlanName
    max_services: int               # -1 = unlimited
    included_incidents: int         # -1 = unlimited
    overage_fee_per_incident: float
    default_provider: str = "whatsapp"
    has_dlq_access: bool = False
    has_claude_decision: bool = True
    has_voice_escalation: bool = False
    has_custom_thresholds: bool = False
    has_slack: bool = False


PLANS: Dict[str, TenantPlan] = {
    "hobby": TenantPlan(
        name="hobby",
        max_services=1,
        included_incidents=5,
        overage_fee_per_incident=0.0,
        default_provider="telegram",
        has_dlq_access=False,
        has_claude_decision=False,
        has_voice_escalation=False,
        has_custom_thresholds=False,
        has_slack=False,
    ),
    "developer": TenantPlan(
        name="developer",
        max_services=1,
        included_incidents=10,
        overage_fee_per_incident=0.0,
        default_provider="whatsapp",
        has_dlq_access=False,
        has_claude_decision=True,
        has_voice_escalation=False,
        has_custom_thresholds=False,
        has_slack=False,
    ),
    "solo": TenantPlan(
        # Sent.dm recommended for solo tier — zero friction onboarding
        name="solo",
        max_services=1,
        included_incidents=50,
        overage_fee_per_incident=0.10,
        default_provider="sent",
        has_dlq_access=False,
        has_claude_decision=True,
        has_voice_escalation=False,
        has_custom_thresholds=False,
        has_slack=False,
    ),
    "startup": TenantPlan(
        name="startup",
        max_services=5,
        included_incidents=200,
        overage_fee_per_incident=0.05,
        default_provider="whatsapp",
        has_dlq_access=True,
        has_claude_decision=True,
        has_voice_escalation=True,
        has_custom_thresholds=False,
        has_slack=True,
    ),
    "scale": TenantPlan(
        name="scale",
        max_services=20,
        included_incidents=1000,
        overage_fee_per_incident=0.02,
        default_provider="whatsapp",
        has_dlq_access=True,
        has_claude_decision=True,
        has_voice_escalation=True,
        has_custom_thresholds=True,
        has_slack=True,
    ),
    "teams": TenantPlan(
        name="teams",
        max_services=20,
        included_incidents=1000,
        overage_fee_per_incident=0.02,
        default_provider="whatsapp",
        has_dlq_access=True,
        has_claude_decision=True,
        has_voice_escalation=True,
        has_custom_thresholds=True,
        has_slack=True,
    ),
    "enterprise": TenantPlan(
        name="enterprise",
        max_services=-1,
        included_incidents=-1,
        overage_fee_per_incident=0.0,
        default_provider="whatsapp",
        has_dlq_access=True,
        has_claude_decision=True,
        has_voice_escalation=True,
        has_custom_thresholds=True,
        has_slack=True,
    ),
}

DEFAULT_PLAN = "solo"


def get_plan(plan_name: str) -> TenantPlan:
    """Return plan by name. Falls back to solo if unknown."""
    return PLANS.get(plan_name, PLANS[DEFAULT_PLAN])


def get_tenant_plan(tenant: dict) -> TenantPlan:
    """Extract and return the TenantPlan from a tenant dict."""
    plan_name = tenant.get("plan", DEFAULT_PLAN)
    return get_plan(plan_name)


def can_monitor_more_services(tenant: dict) -> bool:
    """Return True if the tenant is allowed to monitor an additional service."""
    plan = get_tenant_plan(tenant)
    if plan.max_services == -1:
        return True
    current = tenant.get("service_count", tenant.get("services_monitored", []))
    count = len(current) if isinstance(current, list) else int(current)
    return count < plan.max_services


def incident_quota_remaining(tenant: dict) -> int:
    """Return how many incidents remain in the tenant's monthly quota.

    Returns -1 when the plan has unlimited incidents.
    """
    plan = get_tenant_plan(tenant)
    if plan.included_incidents == -1:
        return -1
    used = tenant.get("incident_count", tenant.get("incidents_this_month", 0))
    return max(plan.included_incidents - int(used), 0)


def increment_incident_count(tenant: dict) -> dict:
    """Increment the tenant's incident counter and return the updated record.

    Caller is responsible for persisting via save_tenant() if needed.
    """
    tenant = copy.deepcopy(tenant)
    # Support both field names for backwards compatibility
    count = tenant.get("incident_count", tenant.get("incidents_this_month", 0))
    tenant["incident_count"]      = count + 1
    tenant["incidents_this_month"] = count + 1
    tenant["last_updated"] = time.time()
    return tenant
