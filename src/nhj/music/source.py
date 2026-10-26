"""The stem source: renders the director's arrangement into the mixer's music buffer.

A drop-in for the recorded-tracks `_Decoder` (same thread contract: push 48 kHz stereo float32
blocks into `Mixer.buf`, block while it is full or paused, stop on `stop`). Layer gains change
only at bar lines, with a half-beat fade; at phrase lines it may splice in a *section* (a
longer, non-repeating passage produced by the continuation composer) in place of the stems.
`iter_blocks` is the pure renderer, so tests and `nhj music demo` can run it offline.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy.signal import lfilter

from nhj import resources
from nhj.duck_player import BLOCK, SR, _Decoder
from nhj.music.director import Director, Plan
from nhj.music.pack import Pack

PHRASE_BARS = 4
_SECTION_FADE = int(0.02 * SR)              # 20 ms cross-fade at section edges (they sit on bar lines)


@dataclass
class Section:
    audio: np.ndarray                      # (bars * samples_per_bar, 2) float32, level-matched to the stems
    bars: int
    label: str = ""


class SectionProvider(Protocol):
    def observe(self, plan: Plan, bar: int) -> None:
        raise NotImplementedError

    def take(self, plan: Plan, bar: int) -> Section | None:
        raise NotImplementedError


class StemSource(_Decoder):
    """Plays the bed. When `accent_gain > 0` and `on_accent` is set, the bar an err/ok accent starts on
    also calls `on_accent(stinger, gain)` so the player can lay a one-shot over the bed (it is
    called at render time, which runs ahead of playback; the player owns that offset)."""

    def __init__(self, pack: Pack, director: Director, buf, paused, sections: SectionProvider | None = None,
                 accent_gain: float = 0.0):
        super().__init__([], buf, paused)
        self.pack, self.director, self.sections = pack, director, sections
        self.accent_gain = max(0.0, float(accent_gain))
        self.on_accent = None                                  # callable(np.ndarray, float) | None
        self._stingers: dict[str, np.ndarray] = {}
        self.plan_log: list[tuple[int, str]] = []              # (bar, label); small, for the demo and tests

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                for block in self.iter_blocks():
                    if self.stop.is_set():
                        return
                    self._push(block)
            except Exception as e:                             # one bad block must not silence the bed
                resources.debug(f"music: stem source error, restarting the arrangement: {type(e).__name__}: {e}")
                time.sleep(0.2)

    def _hit(self, intent: str) -> None:
        if self.accent_gain <= 0.0 or self.on_accent is None:
            return
        try:
            if intent not in self._stingers:
                from nhj.music import synth
                self._stingers[intent] = synth.render_stinger(intent)
            self.on_accent(self._stingers[intent], self.accent_gain)
        except Exception as e:                                 # an accent must never silence the bed
            resources.debug(f"music: accent stinger failed: {type(e).__name__}: {e}")

    def iter_blocks(self):
        pack, spb, n_loop = self.pack, self.pack.spb, self.pack.n
        fade = max(1, int(0.5 * 60.0 / pack.bpm * SR))
        start_of = lambda b: int(round(b * spb))                # noqa: E731
        t = 0
        bar = -1
        nxt = 0
        frm: dict[str, float] = {}
        to: dict[str, float] = {}
        boundary = 0
        tens_from = tens_to = 0.0
        sec: Section | None = None
        sec_start = sec_len = 0
        zi = [np.zeros(1), np.zeros(1)]
        while True:
            if t >= nxt:                                       # a new bar starts here
                bar += 1
                boundary, nxt = nxt, start_of(bar + 1)
                plan = self.director.at_bar(bar)
                self.plan_log.append((bar, plan.label))
                if plan.accent:
                    self._hit(plan.accent)
                frm = {k: to.get(k, 0.0) for k in pack.layers}
                to = {k: (1.0 if k in plan.layers else 0.0) for k in pack.layers}
                tens_from, tens_to = tens_to, plan.tension
                if sec is not None and t >= sec_start + sec_len:
                    sec = None
                if bar % PHRASE_BARS == 0 and sec is None and self.sections is not None and plan.layers:
                    self.sections.observe(plan, bar)
                    got = self.sections.take(plan, bar)
                    if got is not None and got.bars > 0:
                        sec, sec_start, sec_len = got, boundary, len(got.audio)
            n = min(BLOCK, nxt - t)
            ts = np.arange(t, t + n)
            idx = ts % n_loop
            r = np.clip((ts - boundary) / fade, 0.0, 1.0)[:, None]
            rest = np.zeros((n, 2), np.float32)
            kick = np.zeros((n, 2), np.float32)
            for name, data in pack.layers.items():
                g0, g1 = frm.get(name, 0.0), to.get(name, 0.0)
                if g0 == 0.0 and g1 == 0.0:
                    continue
                part = data[idx] * (g0 + (g1 - g0) * r)
                if name == "kick":
                    kick += part
                else:
                    rest += part
            tens = tens_from + (tens_to - tens_from) * min(1.0, max(0.0, (t + n / 2 - boundary) / spb))
            if tens > 0.002:                                   # rising high-pass sweep on everything but the kick
                a = float(np.exp(-2 * np.pi * (30.0 * 40.0 ** tens) / SR))
                for c in (0, 1):
                    rest[:, c], zi[c] = lfilter([a, -a], [1.0, -a], rest[:, c], zi=zi[c])
            else:
                zi = [np.zeros(1), np.zeros(1)]
            block = (kick + rest).astype(np.float32)
            if sec is not None:
                off = ts - sec_start
                inside = (off >= 0) & (off < sec_len)
                if inside.any():
                    o = off[inside]
                    gs = np.zeros(n, np.float32)
                    gs[inside] = np.minimum(np.clip(o / _SECTION_FADE, 0, 1), np.clip((sec_len - o) / _SECTION_FADE, 0, 1))
                    seg = np.zeros((n, 2), np.float32)
                    seg[inside] = sec.audio[o]
                    block = block * (1.0 - gs)[:, None] + seg * gs[:, None]
            yield np.ascontiguousarray(block, dtype=np.float32)
            t += n


def render_timeline(pack: Pack, director: Director, script: list[tuple[float, int, float, str | None]],
                    sections: SectionProvider | None = None,
                    accent_gain: float = 0.0) -> tuple[np.ndarray, list[tuple[int, str]]]:
    """Offline render. *script* = [(seconds, busy, elapsed_s, accent), ...] applied in order; the
    director state changes at each entry's start (a real controller updates continuously, the
    bar lines decide when it is heard). Returns (audio, plan log)."""
    src = StemSource(pack, director, [], threading.Event(), sections, accent_gain=accent_gain)
    hits: list[tuple[int, np.ndarray]] = []
    emitted = 0
    src.on_accent = lambda data, gain: hits.append((emitted, data * gain))
    gen = src.iter_blocks()
    out: list[np.ndarray] = []
    for seconds, busy, elapsed, accent in script:
        director.update(busy, elapsed)
        if accent:
            director.accent(accent)
        need = int(seconds * SR)
        got = 0
        while got < need:
            blk = next(gen)
            out.append(blk)
            got += len(blk)
            emitted += len(blk)
    audio = np.concatenate(out)
    for at, hit in hits:                                       # stingers land on their bar line
        end = min(len(audio), at + len(hit))
        audio[at:end] += hit[: end - at]
    return audio, src.plan_log
