# orchestrator/plans.py
from typing import Literal, Dict
from pydantic import BaseModel, Field
from datetime import datetime


PlanName = Literal["solo", "startup", "teams", "enterprise"]


class TenantPlan(BaseModel):
    name: PlanName
    max_services: int          # -1 = unlimited
    included_incidents: int    # -1 = unlimited
    overage_fee_per_incident: float
    has_dlq_access: bool = False
    has_claude_decision: bool = True
    has_voice_escalation: bool = False
    has_custom_thresholds: bool = False


PLANS: Dict[str, TenantPlan] = {
    "solo": TenantPlan(
        name="solo",
        max_services=1,
        included_incidents=50,
        overage_fee_per_incident=0.10,
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
    "teams": TenantPlan(
        name="teams",
        max_services=20,
        included_incidents=1000,
        overage_fee_per_incident=0.02,
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

DEFAULT_PLAN = "solo"


def get_plan(plan_name: str) -> TenantPlan:
    """Return plan by name. Falls back to solo if unknown."""
    return PLANS.get(plan_name, PLANS[DEFAULT_PLAN])


def get_tenant_plan(tenant: dict) -> TenantPlan:
    """Extract and return the TenantPlan from a tenant dict."""
    plan_name = tenant.get("plan", DEFAULT_PLAN)
    return get_plan(plan_name)


def can_monitor_more_services(tenant: dict) -> bool:
    """Returns True if tenant has not reached their service limit."""
    plan = get_tenant_plan(tenant)
    if plan.max_services == -1:
        return True
    services = tenant.get("services_monitored", [])
    return len(services) < plan.max_services


def incident_quota_remaining(tenant: dict) -> int:
    """
    Returns remaining incident quota for this billing cycle.
    Returns -1 if unlimited.
    """
    plan = get_tenant_plan(tenant)
    if plan.included_incidents == -1:
        return -1
    used = tenant.get("incidents_this_month", 0)
    return max(0, plan.included_incidents - used)


def increment_incident_count(tenant: dict) -> dict:
    """
    Increment the tenant's monthly incident counter.
    Returns updated tenant dict. Caller must persist.
    """
    tenant = {**tenant}
    tenant["incidents_this_month"] = tenant.get("incidents_this_month", 0) + 1
    return tenant
