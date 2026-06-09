"""Jan's chaos engine — turns a faithful alert into a scatty, half-remembered one.

Jan is not the sharpest receptionist. With her `chaos` dial up, she mangles the
jargon she's relaying ("inference" -> "in-fur-france"), umms and ahhs, and trails
off ("...or whatever"). Purely a TEXT transform — works in clone mode, no model
change. Only applied to characters with chaos > 0 (i.e. Jan; Karren/Bazza relay
faithfully).

    garble("inference service is down", chaos=7)
    -> "Er, the in-fur-france service is down, or whatever..."
"""
from __future__ import annotations

import random
import re

# Jargon -> the way Jan mishears/mangles it. Picked at random when she garbles a word.
_JARGON: dict[str, list[str]] = {
    "inference":      ["in-fur-france", "the inferring thingo", "infer-ence or whatever"],
    "kubernetes":     ["koobernetties", "that container thingo", "kuber-whatsit"],
    "deployment":     ["deploy-ment thingo", "the deploy-a-ment", "deployment, I think?"],
    "deploy":         ["deploy thingo", "the deploy whatsit"],
    "database":       ["data-base-ay", "the data thingo", "the data whatsit"],
    "pipeline":       ["pipey thing", "the pipe-a-line", "pipeline thingo"],
    "repository":     ["repo thingo", "the repo whatsit"],
    "repo":           ["repo thingo", "the repo whatsit"],
    "authentication": ["auth-en-whatsit", "the login thingo"],
    "api":            ["A.P.I. or whatever", "the A-P-I thingo"],
    "docker":         ["docker thingy", "the docker whatsit"],
    "production":     ["the real one", "pro-duction", "prod or whatever"],
    "prod":           ["the real one", "prod thingo"],
    "server":         ["server thingo", "the server whatsit"],
    "build":          ["the build thingo", "build whatsit"],
    "commit":         ["that commit thing", "the commit whatsit"],
    "merge":          ["the merge whatsit", "that merge thingo"],
    "staging":        ["the staging thingo", "staging, I think?"],
    "endpoint":       ["end-point-y thing", "the endpoint thingo"],
    "webhook":        ["web-hooky thing", "the webhook whatsit"],
    "cluster":        ["that cluster thingo", "the cluster whatsit"],
    "container":      ["container thingy", "the container whatsit"],
    "latency":        ["late-ency", "the slow thingo"],
}

_HESITATIONS = ["Er,", "Umm,", "Yeah so,", "Oh,", "Look,", "Hang on,", "Right, so,"]
_FILLERS = ["or whatever", "or somethin'", "I dunno", "I think?", "don't quote me",
            "the umm, thingo", "or whatever it's called", "anyway"]

# Peak chaos: Jan didn't catch it at all and just says something oblivious,
# ignoring the actual message. Used at high chaos with a rising probability.
_OBLIVIOUS = [
    "Something happened, I was watchin' ticky-tockies… so yeah.",
    "I was doin' me nails and this went beep. Must be for you.",
    "Dunno love, it beeped. I wasn't really payin' attention.",
    "Yeah, somethin' came up I reckon. Have a squiz yourself.",
    "Oh, that thing did the thing again. Or didn't. Hard to say.",
    "Look, I was on me lunch. It made a noise though.",
    "It went off, I ignored it. You're welcome.",
    "Eh, I was scrollin'. There's a thing for ya I think.",
]


def default_oblivious() -> list[str]:
    return list(_OBLIVIOUS)


def oblivious_chance(chaos: int) -> float:
    """Probability Jan ignores the message entirely. Kicks in at chaos >= 8."""
    return max(0.0, (chaos - 7)) / 3.0 * 0.5    # 8 -> .17, 9 -> .33, 10 -> .50


def garble(message: str, chaos: int = 5, rng: random.Random | None = None,
           allow_prefix: bool = True, allow_trail: bool = True) -> str:
    """Mangle a message in proportion to `chaos` (0 = faithful, 10 = shambles).

    allow_prefix/allow_trail let the caller suppress the "umm…"/"…or whatever"
    framing when the message sits MID-sentence inside a template (so it doesn't
    double up). Word-mangling always applies.
    """
    if not message or chaos <= 0:
        return message
    r = rng or random
    p = max(0.0, min(1.0, chaos / 10.0))

    # 1. mangle known jargon words (always)
    def _maybe_mangle(tok: str) -> str:
        key = re.sub(r"[^a-z]", "", tok.lower())
        if key in _JARGON and r.random() < p:
            return r.choice(_JARGON[key])
        return tok

    text = " ".join(_maybe_mangle(t) for t in message.split())

    # 2. hesitation prefix (only if the message starts the utterance)
    if allow_prefix and r.random() < p:
        text = f"{r.choice(_HESITATIONS)} {text[:1].lower()}{text[1:]}"

    # 3. trail off with a filler (only if the message ends the utterance)
    if allow_trail and r.random() < p * 0.9:
        text = f"{text.rstrip('.!?')}, {r.choice(_FILLERS)}..."

    return text
