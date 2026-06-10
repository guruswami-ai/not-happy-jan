"""Regression: the Ulanzi/AWTRIX adapter must not swallow a failed publish silently.

A dark screen with a healthy queue used to be undiagnosable because _fire_mqtt/_fire_http
returned False on any exception without a word in the log. Now failures surface on stderr
(which the worker captures into worker.log)."""
from __future__ import annotations

from nhj.adapters.ulanzi import UlanziAdapter


def _adapter_with(displays, transport="http"):
    a = UlanziAdapter.__new__(UlanziAdapter)   # skip env-driven __init__
    a.displays = displays
    a.transport = transport
    a.mqtt_host = "broker.invalid"
    a.mqtt_port = 1883
    a.mqtt_user = ""
    a.mqtt_pass = ""
    return a


def test_http_publish_failure_is_logged(capsys, monkeypatch):
    a = _adapter_with([{"prefix": None, "http": "10.0.0.9"}], transport="http")

    def boom(*args, **kwargs):
        raise OSError("connection refused")
    monkeypatch.setattr("nhj.adapters.ulanzi.requests.post", boom)

    assert a._fire_http({"text": "hi"}) is False
    err = capsys.readouterr().err
    assert "ulanzi" in err and "10.0.0.9" in err, f"failure not surfaced: {err!r}"


def test_mqtt_broker_down_is_logged(capsys, monkeypatch):
    a = _adapter_with([{"prefix": "awtrix1", "http": None}], transport="mqtt")
    monkeypatch.setattr(UlanziAdapter, "_broker_up", lambda self: False)

    assert a._fire_mqtt({"text": "hi"}) is False
    err = capsys.readouterr().err
    assert "ulanzi" in err and "unreachable" in err, f"broker-down not surfaced: {err!r}"
