"""Local secret-shape detector — so Karren can scold you for pasting a credential.

SECURITY / TRUST (read this — it's the whole point):
  This is a DETECTOR, not a harvester. It only matches the *shape* of a secret with
  local regular expressions and returns a CATEGORY label (e.g. "an API key"). It
  NEVER captures, returns, logs, stores, or transmits the secret value. There is no
  network access here and nothing is persisted. The only downstream effect is a local
  spoken nudge ("you dropped a key in the chat") that contains no secret.

  `scan()` can only ever return a fixed category string or None — by construction it
  cannot leak what it matched. Audit it; it's deliberately tiny and obvious.
"""
from __future__ import annotations

import re

# (compiled pattern, human category). Order = priority. The matched text is never
# bound to a variable or returned — `re.search` is used purely as a boolean.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),                        "a private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                      "an AWS access key"),
    # Classic short-prefix GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_) and
    # fine-grained PATs (github_pat_) introduced 2022 — the latter has no \b prefix
    # so we anchor on the literal underscore boundary instead.
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),                            "a GitHub token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),                              "a GitHub token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),                          "a Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),                               "a Google API key"),
    # Stripe secret/restricted keys — must come before the generic sk- rule.
    (re.compile(r"\bsk_(live|test)_[A-Za-z0-9]{24,}\b"),                       "a Stripe secret key"),
    (re.compile(r"\brk_live_[A-Za-z0-9]{24,}\b"),                              "a Stripe restricted key"),
    # npm access tokens (exactly 36 alphanumeric chars after the prefix).
    (re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),                                   "an npm token"),
    # Generic sk- style (OpenAI, Anthropic, etc.).  Require a sub-prefix segment
    # (e.g. sk-proj-, sk-ant-) OR a long enough suffix to avoid false positives
    # against package names like "sk-learn" (which are typically ≤15 chars).
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),                                "an API key"),
    # JWT: three base64url segments separated by dots.  Both the header AND payload
    # are JSON objects, so both base64url-encode to a leading `eyJ` — requiring it on
    # the payload segment too avoids false positives on arbitrary dotted base64.  No
    # trailing \b (base64url chars are word chars); a non-word-char/EOS lookahead ends it.
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?=[^A-Za-z0-9_\-]|$)"), "a token"),
    (re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret|access[_-]?token|bearer)\b\s*[:=]\s*\S{8,}"), "a password"),
]


def scan(text: str) -> str | None:
    """Return a CATEGORY label if the text contains something secret-shaped, else None.

    Never returns the matched value — only the category. No logging, no network.
    """
    if not text:
        return None
    for pattern, category in _PATTERNS:
        if pattern.search(text):     # boolean only — the match object is discarded
            return category
    return None
