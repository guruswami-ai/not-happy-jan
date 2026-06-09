"""Notification adapter registry.

Adapters are discovered via pkg_resources entry points (nhj.adapters) so
third-party packages can register new device types with:

    [project.entry-points."nhj.adapters"]
    mydevice = "mypkg.adapters:MyAdapter"
"""
from __future__ import annotations

from nhj.adapters.base import NotificationAdapter


def load_adapters(adapter_names: list[str]) -> list[NotificationAdapter]:
    """Instantiate adapters by name, skipping any that aren't available."""
    from importlib.metadata import entry_points

    eps = {ep.name: ep for ep in entry_points(group="nhj.adapters")}
    result: list[NotificationAdapter] = []
    for name in adapter_names:
        ep = eps.get(name)
        if ep is None:
            continue
        try:
            cls = ep.load()
            adapter = cls()
            if adapter.available():
                result.append(adapter)
        except Exception:
            pass
    return result


__all__ = ["NotificationAdapter", "load_adapters"]
