"""Give NHJ's long-lived processes clear names so they show as ``NHJ TTS`` / ``NHJ MCP``
/ ``NHJ LLM`` / ``NHJ worker`` in Activity Monitor, `ps`, and the macOS background-items
list — instead of a generic ``Python``. Best-effort: a no-op if setproctitle is absent."""
from __future__ import annotations


def set_title(name: str) -> None:
    try:
        import setproctitle
        setproctitle.setproctitle(name)
    except Exception:
        pass
