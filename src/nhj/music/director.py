"""The music director: NHJ state in, a musical plan out. Deterministic, no audio, no LLM.

The controller feeds it what is happening (`update`: how many sessions are busy and for how long;
`accent`: an event intent); the stem source asks it for a `Plan` at every bar line (`at_bar`), so
the arrangement only ever changes on the beat grid. That is what makes it sound like a track.

    busy   layers
    1      kick + bass
    2      + hats + clap
    3      + arp
    4+     + pad (full arrangement)
    long   tension ramps 0 -> 1 (a rising high-pass sweep) after `long_after_s`
    err    breakdown for `breakdown_bars` (pad + arp, no drums or bass), then back
    ok     one bar of release (the full arrangement, whatever was playing), then back

The bar an accent starts on is flagged (`Plan.accent`) exactly once, so the player can lay a
one-shot stinger on top of it: layer changes alone are quieter, not louder.

Nobody busy means silence; the controller pauses the bed then, exactly as for recorded music.
`warn` and `attn` accents are accepted and ignored for now.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

_LADDER = (
    frozenset({"kick", "bass"}),
    frozenset({"kick", "bass", "hats", "clap"}),
    frozenset({"kick", "bass", "hats", "clap", "arp"}),
    frozenset({"kick", "bass", "hats", "clap", "arp", "pad"}),
)
_BREAKDOWN = frozenset({"pad", "arp"})
_RELEASE = _LADDER[-1]
MANUAL_BUSY = 4                      # `nhj mode rave` / `nhj muzak start`: the full arrangement


@dataclass(frozen=True)
class Plan:
    layers: frozenset
    tension: float                   # 0..1
    label: str                       # "busy:2", "breakdown", "release", ... (for logs and the demo)
    accent: str = ""                 # "err" | "ok" on the single bar an accent starts, else ""


class Director:
    def __init__(self, long_after_s: float = 30.0, long_ramp_s: float = 30.0, breakdown_bars: int = 4,
                 accent_ttl_s: float = 8.0, clock=time.monotonic, arrangement: dict | None = None):
        arr = arrangement or {}                       # a pack's own arrangement (see pack.py); {} = the trance defaults
        self._ladder = tuple(frozenset(step) for step in arr.get("ladder") or ()) or _LADDER
        self._breakdown = frozenset(arr["breakdown"]) if arr.get("breakdown") else _BREAKDOWN
        self._release = frozenset(arr["release"]) if arr.get("release") else self._ladder[-1]
        self._tension_max = max(0.0, min(1.0, float(arr.get("tension", 1.0))))
        self.long_after_s = long_after_s
        self.long_ramp_s = max(1.0, long_ramp_s)
        self.breakdown_bars = int(arr["breakdown_bars"]) if arr.get("breakdown_bars") else max(1, breakdown_bars)
        self.accent_ttl_s = accent_ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._busy = 0
        self._elapsed = 0.0
        self._pending: list[tuple[str, float]] = []       # (intent, when it was requested)
        self._hold_until = -1                       # bar index (exclusive) an accent holds the plan to
        self._hold_layers: frozenset = frozenset()
        self._hold_label = ""

    def update(self, busy: int, elapsed_s: float | None) -> None:
        """Latest state; cheap, called every controller tick."""
        with self._lock:
            self._busy = max(0, int(busy))
            self._elapsed = max(0.0, float(elapsed_s or 0.0))

    def accent(self, intent: str) -> None:
        if intent in ("err", "ok"):
            with self._lock:
                self._pending.append((intent, self._clock()))

    def at_bar(self, bar: int) -> Plan:
        """The plan for the bar that starts now. Consumes pending accents."""
        with self._lock:
            fired = ""
            while self._pending:
                intent, asked = self._pending.pop(0)
                if self._clock() - asked > self.accent_ttl_s:
                    continue                          # stale: it arrived while the bed was paused
                if intent == "err":
                    self._hold_until, self._hold_layers, self._hold_label = (
                        bar + self.breakdown_bars, self._breakdown, "breakdown")
                    fired = "err"
                elif intent == "ok" and bar >= self._hold_until:       # never cut a breakdown short
                    self._hold_until, self._hold_layers, self._hold_label = bar + 1, self._release, "release"
                    fired = fired or "ok"                  # an err in the same tick wins
            tension = 0.0
            if self._elapsed > self.long_after_s:
                tension = min(1.0, (self._elapsed - self.long_after_s) / self.long_ramp_s) * self._tension_max
            if bar < self._hold_until:
                return Plan(self._hold_layers, 0.0, self._hold_label, fired)
            if self._busy <= 0:
                return Plan(frozenset(), 0.0, "idle")
            return Plan(self._ladder[min(self._busy, len(self._ladder)) - 1], tension, f"busy:{self._busy}")
