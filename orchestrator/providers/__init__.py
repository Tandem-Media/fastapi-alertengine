"""
Notification provider abstraction layer.
All providers implement NotificationProvider.
"""

from .base import NotificationProvider, DeliveryResult
from .whatsapp import WhatsAppProvider
from .telegram import TelegramProvider
from .webhook import WebhookProvider

__all__ = [
    "NotificationProvider",
    "DeliveryResult",
    "WhatsAppProvider",
    "TelegramProvider",
    "WebhookProvider",
]
