"""Comedy censor — bleep strong swears in the final line before it's spoken.

Full-bogan Jan (ockerism 11) swears; this layer quacks the strong ones so the
default experience is safe-to-share *and* funnier ("get absolutely quacked, ya
legend"). Mild Aussie words (bloody, bugger, bastard, crap, arse, piss) pass.

Works on a text level (swaps the swear stem for a spoken token), so it covers
both static phrases and dynamic LLM output, with any TTS.

Mode: state `censor` (set via `nhj censor ...`) or env NHJ_CENSOR; default "quack".
  quack | beep | honk  → spoken token over the swear      off → raw swears
"""
from __future__ import annotations

import os
import re

# Strong swears (stem + any suffix). Mild Aussie staples are deliberately absent.
# Markdown emphasis (* _ ~ `) may be woven between a stem's letters
# (e.g. "f**uck", "f_uck"), so allow optional emphasis chars between each letter.
_STEMS = ("fuck", "shit", "cunt", "wank", "prick", "dickhead",
          "arsehole", "bullshit", "bollocks")
_EMPH = r"[*_~`]*"
_STRONG = re.compile(
    r"\b(" + "|".join(_EMPH.join(re.escape(c) for c in s) for s in _STEMS) + r")(\w*)",
    re.IGNORECASE,
)
_TOKENS = {"quack": "quack", "beep": "beep", "honk": "honk"}


def mode() -> str:
    try:
        from nhj.state import get_setting
        m = get_setting("censor")
    except Exception:
        m = None
    return (m or os.getenv("NHJ_CENSOR", "quack") or "quack").lower()


def apply(text: str) -> str:
    m = mode()
    if not text or m == "off":
        return text
    tok = _TOKENS.get(m, "quack")

    def _sub(mt: re.Match) -> str:
        t = tok.capitalize() if mt.group(1)[:1].isupper() else tok
        return t + mt.group(2)   # "fucked" -> "quacked", "fucking" -> "quacking"

    return _STRONG.sub(_sub, text)
