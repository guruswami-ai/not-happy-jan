"""Base class for all Not-Happy-Jan notification adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nhj.characters import Character


class NotificationAdapter(ABC):
    """A single notification channel (haptic, visual display, audio, etc.).

    Implement this to add a new device. Register it via pyproject.toml:

        [project.entry-points."nhj.adapters"]
        mydevice = "mypkg.adapters:MyDeviceAdapter"

    The worker calls fire() on each enabled adapter in config order.
    """

    @abstractmethod
    def fire(
        self,
        intent: str,
        message: str,
        character: "Character",
        vibe_level: int = 5,
        **kwargs,
    ) -> bool:
        """Fire the notification. Return True on success, False on failure."""

    @abstractmethod
    def available(self) -> bool:
        """Return True if this adapter is configured and its device is reachable."""
