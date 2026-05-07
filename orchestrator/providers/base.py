"""
Base provider interface and DeliveryResult contract.
All notification providers must implement this interface.
"""

from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class DeliveryResult:
    """Structured result for every notification attempt."""
    provider:     str
    tenant_id:    str
    incident_id:  str
    success:      bool
    channel:      str           # "whatsapp" | "telegram" | "webhook"
    attempted_at: float = field(default_factory=time.time)
    error:        Optional[str] = None
    message_id:   Optional[str] = None   # provider message SID/ID if available

    def to_dict(self) -> dict:
        return {
            "provider":     self.provider,
            "tenant_id":    self.tenant_id,
            "incident_id":  self.incident_id,
            "success":      self.success,
            "channel":      self.channel,
            "attempted_at": self.attempted_at,
            "error":        self.error,
            "message_id":   self.message_id,
        }


class NotificationProvider:
    """
    Abstract base class for all notification providers.
    All providers must implement send().
    All providers must never raise — catch all exceptions internally.
    """

    channel: str = "base"

    async def send(
        self,
        tenant: dict,
        incident_id: str,
        message: str,
    ) -> "DeliveryResult":
        """
        Send a notification message.
        Returns DeliveryResult — never raises.
        """
        raise NotImplementedError

    def _make_result(
        self,
        tenant: dict,
        incident_id: str,
        success: bool,
        error: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> "DeliveryResult":
        return DeliveryResult(
            provider=self.channel,
            tenant_id=tenant.get("tenant_id", "unknown"),
            incident_id=incident_id,
            success=success,
            channel=self.channel,
            error=error,
            message_id=message_id,
        )
