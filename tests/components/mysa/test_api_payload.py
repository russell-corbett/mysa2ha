"""Unit tests for mysa API payload and signing helpers."""

from __future__ import annotations

from custom_components.mysa.api import MysaApiClient


def test_build_change_state_payload_maps_modes() -> None:
    """Mode/fan/setpoint should map to expected wire values."""
    client = object.__new__(MysaApiClient)
    client.username = "user@example.com"

    payload = client._build_change_state_payload(
        device_id="device-1",
        model="AC-V1",
        setpoint=21.5,
        mode="cool",
        fan_speed="high",
    )

    assert payload["msg"] == 44
    assert payload["body"]["type"] == 2
    assert payload["body"]["cmd"][0]["sp"] == 21.5
    assert payload["body"]["cmd"][0]["md"] == 4
    assert payload["body"]["cmd"][0]["fn"] == 7


def test_build_change_state_payload_omits_none_fields() -> None:
    """Optional command fields should be omitted when not set."""
    client = object.__new__(MysaApiClient)
    client.username = "user@example.com"

    payload = client._build_change_state_payload(
        device_id="device-1",
        model="BB-V2-L",
        setpoint=None,
        mode="off",
        fan_speed=None,
    )

    cmd = payload["body"]["cmd"][0]
    assert payload["body"]["type"] == 5
    assert cmd["md"] == 1
    assert "sp" not in cmd
    assert "fn" not in cmd


