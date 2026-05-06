# orchestrator/plans.py
"""
Plan definitions and tenant plan lookup.

Plans (in ascending order):
  solo     — free tier, no DLQ access
  startup  — DLQ access enabled
  scale    — DLQ access enabled
  enterprise — DLQ access enabled
"""

from dataclasses import dataclass


@dataclass
class Plan:
    name: str
    has_dlq_access: bool


_PLANS = {
    "solo":       Plan(name="solo",       has_dlq_access=False),
    "startup":    Plan(name="startup",    has_dlq_access=True),
    "scale":      Plan(name="scale",      has_dlq_access=True),
    "enterprise": Plan(name="enterprise", has_dlq_access=True),
}

_DEFAULT_PLAN = _PLANS["solo"]


def get_tenant_plan(tenant: dict) -> Plan:
    """Return the Plan object for the given tenant dict."""
    plan_name = tenant.get("plan", "solo")
    return _PLANS.get(plan_name, _DEFAULT_PLAN)
