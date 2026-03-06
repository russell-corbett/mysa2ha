"""Unit tests for mysa API payload and signing helpers."""

from __future__ import annotations

from custom_components.mysa.api import MysaApiClient, _aws_sigv4_authorization


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

    assert payload["msg"] == 2
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


def test_sigv4_header_shape() -> None:
    """SigV4 header should include required fields."""
    authorization = _aws_sigv4_authorization(
        method="POST",
        canonical_uri="/topics/test",
        canonical_query="qos=1",
        headers={
            "host": "example.amazonaws.com",
            "content-type": "application/octet-stream",
            "x-amz-date": "20260101T000000Z",
            "x-amz-security-token": "token",
        },
        payload=b"{}",
        access_key_id="AKID",
        secret_key="SECRET",
        region="us-east-1",
        service="iotdevicegateway",
        amz_date="20260101T000000Z",
        date_stamp="20260101",
    )

    assert authorization.startswith("AWS4-HMAC-SHA256 ")
    assert "Credential=AKID/20260101/us-east-1/iotdevicegateway/aws4_request" in authorization
    assert "SignedHeaders=" in authorization
    assert "Signature=" in authorization
