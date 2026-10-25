"""Optional shared secret for the MCP and TTS network listeners.

A blank ``NHJ_SHARED_SECRET`` means no check. Loopback is the safe default.
When the secret is set, a network caller sends it as ``X-NHJ-Token`` or as
``Authorization: Bearer``. The stdio MCP transport is a local process and
does not send a header.
"""
from __future__ import annotations

import hashlib
import hmac
import os

HEADER = "X-NHJ-Token"


def secret() -> str:
    return os.getenv("NHJ_SHARED_SECRET", "").strip()


def matches(supplied: str, expected: str) -> bool:
    """True when *supplied* is the secret. Equal-length digests, so a short guess is not faster."""
    if not expected:
        return True
    got = hashlib.sha256(supplied.encode()).digest()
    want = hashlib.sha256(expected.encode()).digest()
    return hmac.compare_digest(got, want)


def allows(headers, expected: str | None = None) -> bool:
    """True when no secret is configured, or *headers* carries the right one."""
    expected = secret() if expected is None else expected
    if not expected:
        return True
    for key, value in headers.items():
        name = key.lower()
        if name == HEADER.lower() and matches(str(value).strip(), expected):
            return True
        if name == "authorization":
            parts = str(value).split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer" and matches(parts[1].strip(), expected):
                return True
    return False
