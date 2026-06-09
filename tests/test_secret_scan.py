"""Tests for the local secret-shape detector (nhj/secret_scan.py).

The detector must return only fixed category labels, never matched secret text.
These tests cover realistic positive cases, false-positive boundaries, and a
source-level audit that prevents binding the match object.
"""
from __future__ import annotations

import inspect
import re

import pytest

from nhj.secret_scan import _PATTERNS, scan


KNOWN_CATEGORIES = {category for _, category in _PATTERNS}


@pytest.mark.parametrize("text,expected", [
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEo...", "a private key"),
    ("-----BEGIN DSA PRIVATE KEY-----", "a private key"),
    ("-----BEGIN PRIVATE KEY-----\nMIIE...", "a private key"),
    ("-----BEGIN EC PRIVATE KEY-----", "a private key"),
    ("AKIAIOSFODNN7EXAMPLE", "an AWS access key"),
    ("AKIA123456789012ABCD", "an AWS access key"),
    ("key=AKIAIOSFODNN7EXAMPLE rest of line", "an AWS access key"),
    ("ghp_" + "A" * 36, "a GitHub token"),
    ("gho_" + "B" * 30, "a GitHub token"),
    ("ghu_" + "C" * 25, "a GitHub token"),
    ("ghs_" + "D" * 20, "a GitHub token"),
    ("ghr_" + "E" * 20, "a GitHub token"),
    ("github_pat_" + "x" * 30, "a GitHub token"),
    ("token=github_pat_" + "y" * 25, "a GitHub token"),
    ("xox" + "b-12345678-12345678-abcdefghijklmnopqr", "a Slack token"),
    ("xox" + "p-12345678901-12345678901-AAABBBBCCCCC", "a Slack token"),
    ("xox" + "a-abcdefghijklmnopqrstuvwxyz1234", "a Slack token"),
    ("xox" + "r-abcdefghijklmnopqrstuvwxyz1234", "a Slack token"),
    ("xox" + "s-abcdefghijklmnopqrstuvwxyz1234", "a Slack token"),
    ("AIza" + "A" * 35, "a Google API key"),
    ("sk_live_" + "a" * 24, "a Stripe secret key"),
    ("sk_test_" + "b" * 24, "a Stripe secret key"),
    ("rk_live_" + "c" * 24, "a Stripe restricted key"),
    ("npm_" + "A" * 36, "an npm token"),
    ("sk-proj-" + "A" * 48, "an API key"),
    ("sk-ant-api03-" + "x" * 40, "an API key"),
    ("sk-" + "x" * 20, "an API key"),
    ("eyJ" + "A" * 20 + ".eyJ" + "B" * 20 + "." + "C" * 20, "a token"),
    ("header: eyJ" + "A" * 20 + ".eyJ" + "B" * 20 + "." + "C" * 20 + " end", "a token"),
    ("passwd=hunter2securepwd", "a password"),
    ("password : supersecret123!", "a password"),
    ("Password = supersecret123!", "a password"),
    ("API_KEY=abcdef1234567890", "a password"),
    ("api-key=MyS3cr3tK3yValue!!", "a password"),
    ("apikey=MyS3cr3tK3yValue!!", "a password"),
    ("secret: myreallysecretvalue", "a password"),
    ("access-token=AbcdefghijklMNOPQ", "a password"),
    ("bearer: Abcdefghijklmnopqrstuvwxyz01234", "a password"),
    ("access_token=ghp_verylongtoken12345678901", "a GitHub token"),
])
def test_true_positives(text: str, expected: str) -> None:
    assert scan(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "I just pushed a fix for the auth bug",
    "The password strength requirements are 8+ chars",
    "the bearer of bad news",
    "api_key documentation",
    "secret agent man",
    "sk-learn is a great ML library",
    "sklearn",
    "sk-",
    "sk_sandbox_abc",
    "npm_" + "A" * 10,
    "npm_" + "A" * 35,
    "npm_" + "A" * 37,
    "AKIA123",
    "eyJhbGci.eyJzdWIi",
    "eyJhbGc.eyJzdWI.SflK",
    "eyJ" + "A" * 20 + "." + "B" * 20 + "." + "C" * 20,  # dotted base64, non-JWT payload (#70)
    "ghp_short",
    "password = abc",
])
def test_true_negatives(text: str) -> None:
    assert scan(text) is None


def test_scan_returns_string_or_none() -> None:
    assert scan("AKIAIOSFODNN7EXAMPLE") in KNOWN_CATEGORIES
    assert scan("totally fine text") is None


def test_scan_return_values_are_all_from_known_category_set() -> None:
    inputs = [
        "-----BEGIN PRIVATE KEY-----",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZab",
        "github_pat_" + "x" * 30,
        "xox" + "b-1234567890-abcdefghijklmno",
        "AIzaSyD-9tSrke72I6ox_kAkzAkEfGHIJKLMNOPQRS",
        "sk_live_" + "a" * 24,
        "rk_live_" + "c" * 24,
        "npm_" + "A" * 36,
        "sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMTIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf",
        "password=MySecretPass123",
        "clean text with no secrets",
    ]
    for text in inputs:
        result = scan(text)
        assert result is None or result in KNOWN_CATEGORIES


@pytest.mark.parametrize("secret", [
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEo...",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef",
    "github_pat_" + "x" * 30,
    "xox" + "b-1234567890-abcdefghijklmno",
    "AIzaSyD-9tSrke72I6ox_kAkzAkEfGHIJKLMNO",
    "sk_live_" + "a" * 24,
    "npm_" + "A" * 36,
    "sk-abcdefghijklmnopqrstuvwxyzABCD",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMTIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf",
    "password=MySecretPass123",
])
def test_scan_never_returns_the_secret_value(secret: str) -> None:
    result = scan(f"my key is {secret}")
    assert result is not None
    assert secret not in result
    assert len(result) < 50


def test_scan_source_does_not_bind_match_object() -> None:
    src = inspect.getsource(scan)
    traditional = re.search(r"\b\w+\s*=\s*\w+\.search\(", src)
    walrus = re.search(r"\b\w+\s*:=\s*\w+\.search\(", src)
    assert traditional is None and walrus is None


def test_scan_multiline_prompt_with_secret_embedded() -> None:
    prompt = (
        "Here is my code:\n\n"
        "  client = boto3.client('s3', aws_access_key_id='AKIAIOSFODNN7EXAMPLE')\n\n"
        "Can you review it?"
    )
    result = scan(prompt)
    assert result == "an AWS access key"
    assert "AKIAIOSFODNN7EXAMPLE" not in result


def test_scan_multiline_clean_prompt() -> None:
    prompt = (
        "Please refactor this function:\n\n"
        "def greet(name):\n"
        "    return f'Hello, {name}'\n"
    )
    assert scan(prompt) is None


def test_scan_returns_first_matching_category_only() -> None:
    multi = (
        "-----BEGIN PRIVATE KEY-----\n"
        "AKIAIOSFODNN7EXAMPLE\n"
    )
    result = scan(multi)
    assert result == "a private key"
