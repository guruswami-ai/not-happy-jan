"""Character/personality routing for Not-Happy-Jan.

A two-/three-rung escalation LADDER (jan -> bazza -> karren). Each character has an
intensity DIAL (1-10) that selects one of three phrase **bands** (low 1-3, mid 4-7,
high 8-10) and a matching DELIVERY (Qwen3-TTS instruct / speed / temperature):

  - Jan    (rung 0, voice=jan)    — "bogan" dial: professional (1) -> ocker, swears (10)
  - Bazza  (rung 1)               — "manager" dial: apathetic (1) -> anxious, fast (10)
  - Karren (rung 2)               — "karen" dial: blunt (1) -> full meltdown, wants the manager (10)

Routing: config/default.yaml `routing.rung_by_intent` maps each intent to a rung.
Levels: config `characters.<name>.level` or env `NHJ_<NAME>_LEVEL` (e.g. NHJ_JAN_LEVEL=8).

Back-compatible: characters with an old flat `phrases:` map still load (wrapped as one
band), and `random_phrase()`/`voice` keep working for adapters that use them.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from nhj import resources

# character.yaml persona defs ship with the package; ref.wav/ref.txt are downloaded
# media (nhj setup-media). In a source checkout both resolve to <repo>/voices.
_DEFS_ROOT   = resources.character_defs_dir()
_VOICES_ROOT = resources.voices_dir()

_BANDS = ("low", "mid", "high")


def band_for_level(level: int) -> str:
    """1-3 -> low, 4-7 -> mid, 8-11 -> high. (11 = swearing tier; see SWEAR_LEVEL.)"""
    level = max(1, min(11, int(level)))
    return "low" if level <= 3 else "mid" if level <= 7 else "high"


SWEAR_LEVEL = 11  # intensity dial value at which the swearing pool activates


@dataclass
class Delivery:
    """Everything the audio adapter needs to synthesise one line."""
    text: str
    voice: str
    instruct: str = ""
    speed: float = 1.0
    temperature: float = 0.8
    model_tier: str = "fast"
    band: str = "mid"
    level: int = 4


@dataclass
class Band:
    phrases: dict[str, list[str]] = field(default_factory=dict)
    instruct: str = ""
    speed: float = 1.0
    temperature: float = 0.8
    swearing: dict[str, list[str]] = field(default_factory=dict)  # opt-in, merged when enabled


@dataclass
class Character:
    name: str
    voice: str
    model_tier: str = "fast"
    preamble: str = ""
    personality: str = ""
    dial: str = ""
    level: int = 4
    chaos: int = 0           # Jan's incompetence dial (0 faithful .. 10 shambles); garbles the message
    triggers_on: list[str] = field(default_factory=list)
    bands: dict[str, Band] = field(default_factory=dict)
    oblivious: list[str] = field(default_factory=list)   # peak-chaos checked-out lines (ignore the message)
    fillers: list[str] = field(default_factory=list)     # "still working on it" stalling lines (voices/<name>/fillers.txt)

    # ---- loading -------------------------------------------------------------
    @classmethod
    def load(cls, name: str, defs_root: Path = _DEFS_ROOT) -> "Character":
        yaml_path = defs_root / name / "character.yaml"
        if not yaml_path.is_file():
            raise FileNotFoundError(f"No character.yaml for {name!r} at {yaml_path}")
        data = yaml.safe_load(yaml_path.read_text()) or {}

        bands: dict[str, Band] = {}
        if isinstance(data.get("bands"), dict):
            for bname, bdata in data["bands"].items():
                bdata = bdata or {}
                bands[bname] = Band(
                    phrases=bdata.get("phrases", {}) or {},
                    instruct=bdata.get("instruct", ""),
                    speed=float(bdata.get("speed", 1.0)),
                    temperature=float(bdata.get("temperature", 0.8)),
                    swearing=bdata.get("swearing", {}) or {},
                )
        else:
            # legacy flat phrases -> single "mid" band, default delivery
            bands["mid"] = Band(phrases=data.get("phrases", {}) or {})

        fillers_path = defs_root / name / "fillers.txt"
        fillers = [
            ln.strip() for ln in fillers_path.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ] if fillers_path.is_file() else []

        return cls(
            name=data.get("name", name),
            voice=data.get("voice", name),
            model_tier=data.get("model_tier", "fast"),
            preamble=data.get("preamble", ""),
            personality=data.get("personality", ""),
            dial=data.get("dial", ""),
            level=int(data.get("level", 4)),
            chaos=int(data.get("chaos", 0)),
            triggers_on=data.get("triggers_on", []),
            bands=bands,
            oblivious=data.get("oblivious", []) or [],
            fillers=fillers,
        )

    # ---- band / delivery resolution -----------------------------------------
    def _band(self) -> Band:
        want = band_for_level(self.level)
        if want in self.bands:
            return self.bands[want]
        # nearest available band (graceful if a character only defines some)
        for b in _BANDS:
            if b in self.bands:
                return self.bands[b]
        return Band()

    def _garble(self, message: str, allow_prefix: bool = True, allow_trail: bool = True) -> str:
        if message and self.chaos > 0:
            from nhj.garble import garble
            return garble(message, self.chaos, allow_prefix=allow_prefix, allow_trail=allow_trail)
        return message

    def _phrase(self, intent: str, message: str) -> str:
        # Peak chaos: Jan didn't catch it and just says something oblivious.
        if message and self.chaos >= 8:
            from nhj.garble import oblivious_chance, default_oblivious
            obliv = self.oblivious or default_oblivious()
            if obliv and random.random() < oblivious_chance(self.chaos):
                return random.choice(obliv)
        band = self._band()
        pool = list(band.phrases.get(intent, []))
        if self.level >= SWEAR_LEVEL:          # ockerism 11 → full swearing bogan
            pool += band.swearing.get(intent, [])
        if not pool:
            msg = self._garble(message)
            return f"{self.preamble}{msg}".strip() if msg else self.preamble.rstrip(" —")
        tpl = random.choice(pool)
        if not message:
            return tpl.replace(" — {message}", "").replace("{message}", "").strip()
        # Position-aware garble: only add umm-prefix / trail-off framing when the
        # {message} actually starts / ends the template (avoids mid-sentence stacking).
        s = tpl.strip()
        msg = self._garble(message,
                           allow_prefix=s.startswith("{message}"),
                           allow_trail=s.endswith("{message}"))
        return tpl.format(message=msg)

    def _dynamic(self, intent: str, message: str, band: "Band") -> Optional[str]:
        """Try the optional LLM rephrase (NHJ_DYNAMIC); None → caller uses static bank."""
        try:
            from nhj import boganify
            if not boganify.enabled():
                return None
            return boganify.generate(self, intent, message, self.level,
                                     self.level >= SWEAR_LEVEL, band.instruct, self.chaos)
        except Exception:
            return None

    def _censor(self, text: str) -> str:
        try:
            from nhj import censor
            return censor.apply(text)
        except Exception:
            return text

    def delivery(self, intent: str, message: str = "") -> Delivery:
        band = self._band()
        text = self._dynamic(intent, message, band) or self._phrase(intent, message)
        return Delivery(
            text=self._censor(text),
            voice=self.voice,
            instruct=band.instruct,
            speed=band.speed,
            temperature=band.temperature,
            model_tier=self.model_tier,
            band=band_for_level(self.level),
            level=self.level,
        )

    # ---- back-compat helpers (used by existing adapters / build-bank) --------
    @property
    def phrases(self) -> dict[str, list[str]]:
        return self._band().phrases

    def random_phrase(self, intent: str, message: str = "") -> str:
        return self._phrase(intent, message)

    def ref_wav(self, voices_root: Path = _VOICES_ROOT) -> Optional[Path]:
        p = voices_root / self.voice / "ref.wav"
        return p if p.is_file() else None

    def ref_txt(self, voices_root: Path = _VOICES_ROOT) -> str:
        p = voices_root / self.voice / "ref.txt"
        return p.read_text().strip() if p.is_file() else ""


# ---- routing -----------------------------------------------------------------
def _cfg() -> dict:
    # The merged defaults + user config.yaml override, so routing/dials honour overrides.
    from nhj import config
    return config.CFG


def _dial_for(name: str, cfg_chars: dict, key: str, fallback: int) -> int:
    """Resolve a per-character dial (level/chaos): runtime state > env > config > fallback."""
    from nhj.state import get_dial
    v = get_dial(name, key)            # live `nhj set` override
    if v is not None:
        return int(v)
    env = os.getenv(f"NHJ_{name.upper()}_{key.upper()}")
    if env and env.strip().isdigit():
        return int(env)
    entry = cfg_chars.get(name) or {}
    return int(entry.get(key, fallback))


def character_by_name(name: str, intent: str, defs_root: Path = _DEFS_ROOT) -> Character:
    """Explicit character override from a [Jan:ok] / [Bazza:err] marker. Loads the named
    character (with its live dials) and ignores intent→rung routing. Unknown name → falls
    back to normal intent routing."""
    name = (name or "").strip().lower()
    if name and name != "vibes":
        cfg_chars = _cfg().get("characters", {}) or {}
        try:
            ch = Character.load(name, defs_root)
            ch.level = _dial_for(ch.name, cfg_chars, "level", ch.level)
            ch.chaos = _dial_for(ch.name, cfg_chars, "chaos", ch.chaos)
            return ch
        except FileNotFoundError:
            pass
    return resolve_character(intent, defs_root)


def resolve_character(intent: str, defs_root: Path = _DEFS_ROOT) -> Character:
    """Pick the ladder rung for this intent and apply its configured level.

    Routing (config/default.yaml `routing`):
      rung_by_intent: {ok: jan, warn: bazza, err: karren, ...}   # preferred
      (legacy: default / on_err / on_attn still honoured as a fallback)
    """
    cfg = _cfg()
    routing = cfg.get("routing", {}) or {}
    cfg_chars = cfg.get("characters", {}) or {}

    rung_by_intent = routing.get("rung_by_intent") or {}
    # legacy fallbacks
    default_override = os.getenv("NHJ_DEFAULT_CHARACTER")
    error_override = os.getenv("NHJ_ERROR_CHARACTER")
    default_name = default_override or routing.get("default", "jan")
    err_name = error_override or routing.get("on_err", "karren")
    attn_name = routing.get("on_attn", err_name)

    if intent == "err" and error_override:
        target = error_override
    elif intent != "err" and default_override:
        target = default_override
    else:
        target = rung_by_intent.get(intent) or (
            err_name if intent == "err" else attn_name if intent == "attn" else default_name
        )

    for candidate in (target, default_name, "jan"):
        try:
            ch = Character.load(candidate, defs_root)
            ch.level = _dial_for(ch.name, cfg_chars, "level", ch.level)
            ch.chaos = _dial_for(ch.name, cfg_chars, "chaos", ch.chaos)
            return ch
        except FileNotFoundError:
            continue
    return Character(name="nhj", voice="nhj")
