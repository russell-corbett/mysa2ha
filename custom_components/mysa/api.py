"""API client for Mysa cloud services."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientError

from homeassistant.const import CONTENT_TYPE_JSON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE_URL,
    AWS_REGION,
    COGNITO_CLIENT_ID,
    COGNITO_IDENTITY_ENDPOINT,
    COGNITO_IDENTITY_POOL_ID,
    COGNITO_IDP_ENDPOINT,
    COGNITO_LOGIN_KEY,
    FAN_TO_RAW,
    IOT_DATA_ENDPOINT,
    IOT_DATA_HOST,
    MODE_TO_RAW,
)

_LOGGER = logging.getLogger(__name__)

_AUTH_ERROR_CODES = {
    "NotAuthorizedException",
    "UserNotFoundException",
    "ResourceNotFoundException",
}


class MysaError(Exception):
    """Base exception for Mysa API errors."""


class MysaCannotConnect(MysaError):
    """Cannot connect to Mysa service."""


class MysaAuthError(MysaError):
    """Authentication failure."""


@dataclass
class SessionTokens:
    """Session tokens from Cognito."""

    id_token: str
    access_token: str
    refresh_token: str
    expires_at: float


@dataclass
class IotCredentials:
    """Temporary credentials for IoT publish."""

    access_key_id: str
    secret_key: str
    session_token: str
    expires_at: float


class MysaApiClient:
    """Mysa cloud API client."""

    def __init__(self, hass: HomeAssistant, username: str, password: str) -> None:
        self.hass = hass
        self.username = username
        self.password = password
        self.logger = _LOGGER

        self._tokens: SessionTokens | None = None
        self._identity_id: str | None = None
        self._iot_credentials: IotCredentials | None = None

    @property
    def has_tokens(self) -> bool:
        """Return whether auth tokens are loaded."""
        return self._tokens is not None

    async def async_login(self) -> None:
        """Authenticate with Cognito username/password."""
        data = await self._async_cognito_idp(
            target="AWSCognitoIdentityProviderService.InitiateAuth",
            payload={
                "ClientId": COGNITO_CLIENT_ID,
                "AuthFlow": "USER_PASSWORD_AUTH",
                "AuthParameters": {
                    "USERNAME": self.username,
                    "PASSWORD": self.password,
                },
            },
        )
        self._set_tokens_from_auth_result(data.get("AuthenticationResult", {}), require_refresh=True)

    async def async_get_devices(self) -> dict[str, Any]:
        """Get all devices."""
        return await self._async_get_json("/devices")

    async def async_get_device_states(self) -> dict[str, Any]:
        """Get current state for all devices."""
        return await self._async_get_json("/devices/state")

    async def async_set_device_state(
        self,
        device: Mapping[str, Any],
        *,
        setpoint: float | None = None,
        mode: str | None = None,
        fan_speed: str | None = None,
    ) -> None:
        """Publish command to a thermostat via AWS IoT data plane."""
        await self._async_ensure_tokens()
        payload = self._build_change_state_payload(
            device_id=str(device["Id"]),
            model=str(device.get("Model", "")),
            setpoint=setpoint,
            mode=mode,
            fan_speed=fan_speed,
        )
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        topic = f"/v1/dev/{device['Id']}/in"
        encoded_topic = urllib.parse.quote(topic, safe="/")
        path = f"/topics/{encoded_topic}"
        query = "qos=1"
        session = async_get_clientsession(self.hass)

        for attempt in (1, 2):
            creds = await self._async_get_iot_credentials()
            amz_date, date_stamp = _aws_timestamps()
            signed_headers = {
                "content-type": "application/octet-stream",
                "host": IOT_DATA_HOST,
                "x-amz-date": amz_date,
                "x-amz-security-token": creds.session_token,
            }
            authorization = _aws_sigv4_authorization(
                method="POST",
                canonical_uri=path,
                canonical_query=query,
                headers=signed_headers,
                payload=payload_bytes,
                access_key_id=creds.access_key_id,
                secret_key=creds.secret_key,
                region=AWS_REGION,
                service="iotdevicegateway",
                amz_date=amz_date,
                date_stamp=date_stamp,
            )

            headers = {
                "Content-Type": "application/octet-stream",
                "X-Amz-Date": amz_date,
                "X-Amz-Security-Token": creds.session_token,
                "Authorization": authorization,
            }

            try:
                response = await session.post(
                    f"{IOT_DATA_ENDPOINT}{path}?{query}",
                    data=payload_bytes,
                    headers=headers,
                )
            except (ClientError, TimeoutError) as err:
                raise MysaCannotConnect(f"Unable to publish command to Mysa IoT: {err}") from err

            if response.status == 403 and attempt == 1:
                self._iot_credentials = None
                continue

            if response.status == 403:
                raise MysaAuthError("Mysa IoT credentials rejected")

            if response.status >= 400:
                body = await response.text()
                raise MysaError(f"Mysa IoT publish failed ({response.status}): {body}")

            return

    async def _async_get_json(self, path: str, *, retried: bool = False) -> dict[str, Any]:
        """Perform authenticated GET request."""
        await self._async_ensure_tokens()
        assert self._tokens is not None

        session = async_get_clientsession(self.hass)
        try:
            response = await session.get(
                f"{API_BASE_URL}{path}",
                headers={"Authorization": self._tokens.id_token},
            )
        except (ClientError, TimeoutError) as err:
            raise MysaCannotConnect(f"Mysa API request failed: {err}") from err

        if response.status == 401:
            if retried:
                raise MysaAuthError("Mysa authentication expired")
            await self._async_refresh_tokens(force=True)
            return await self._async_get_json(path, retried=True)

        if response.status >= 400:
            body = await response.text()
            raise MysaError(f"Mysa API request failed ({response.status}): {body}")

        return await response.json()

    async def _async_ensure_tokens(self) -> None:
        """Ensure current tokens are available and valid."""
        if not self._tokens:
            await self.async_login()
            return

        if self._tokens.expires_at <= time.time() + 60:
            await self._async_refresh_tokens(force=False)

    async def _async_refresh_tokens(self, force: bool) -> None:
        """Refresh Cognito tokens."""
        if not self._tokens:
            await self.async_login()
            return

        if not force and self._tokens.expires_at > time.time() + 60:
            return

        data = await self._async_cognito_idp(
            target="AWSCognitoIdentityProviderService.InitiateAuth",
            payload={
                "ClientId": COGNITO_CLIENT_ID,
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "AuthParameters": {
                    "REFRESH_TOKEN": self._tokens.refresh_token,
                },
            },
        )
        self._set_tokens_from_auth_result(data.get("AuthenticationResult", {}), require_refresh=False)

    async def _async_get_iot_credentials(self) -> IotCredentials:
        """Get temporary IAM credentials via Cognito identity pool."""
        await self._async_ensure_tokens()
        assert self._tokens is not None

        if self._iot_credentials and self._iot_credentials.expires_at > time.time() + 60:
            return self._iot_credentials

        if not self._identity_id:
            id_data = await self._async_cognito_identity(
                target="com.amazonaws.cognito.identity.model.AWSCognitoIdentityService.GetId",
                payload={
                    "IdentityPoolId": COGNITO_IDENTITY_POOL_ID,
                    "Logins": {COGNITO_LOGIN_KEY: self._tokens.id_token},
                },
            )
            self._identity_id = id_data.get("IdentityId")
            if not self._identity_id:
                raise MysaError("Missing IdentityId in Cognito response")

        creds_data = await self._async_cognito_identity(
            target="com.amazonaws.cognito.identity.model.AWSCognitoIdentityService.GetCredentialsForIdentity",
            payload={
                "IdentityId": self._identity_id,
                "Logins": {COGNITO_LOGIN_KEY: self._tokens.id_token},
            },
        )
        raw_creds = creds_data.get("Credentials", {})
        access_key_id = raw_creds.get("AccessKeyId")
        secret_key = raw_creds.get("SecretKey")
        session_token = raw_creds.get("SessionToken")
        expiration = raw_creds.get("Expiration")

        if not access_key_id or not secret_key or not session_token or not expiration:
            raise MysaError("Invalid Cognito identity credentials response")

        expires_at = _parse_aws_datetime(expiration)
        self._iot_credentials = IotCredentials(
            access_key_id=access_key_id,
            secret_key=secret_key,
            session_token=session_token,
            expires_at=expires_at,
        )
        return self._iot_credentials

    async def _async_cognito_idp(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call Cognito IDP JSON-RPC API."""
        return await self._async_aws_json_rpc(
            endpoint=COGNITO_IDP_ENDPOINT,
            target=target,
            payload=payload,
        )

    async def _async_cognito_identity(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call Cognito Identity JSON-RPC API."""
        return await self._async_aws_json_rpc(
            endpoint=COGNITO_IDENTITY_ENDPOINT,
            target=target,
            payload=payload,
        )

    async def _async_aws_json_rpc(self, endpoint: str, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call AWS JSON-RPC endpoint and return decoded JSON."""
        session = async_get_clientsession(self.hass)
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": target,
            "Accept": CONTENT_TYPE_JSON,
        }

        try:
            response = await session.post(endpoint, headers=headers, json=payload)
            data = await response.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as err:
            raise MysaCannotConnect(f"AWS endpoint request failed: {err}") from err

        if response.status < 400:
            return data

        code = _extract_aws_error_code(data)
        message = data.get("message") or data.get("Message") or str(data)
        if code in _AUTH_ERROR_CODES:
            raise MysaAuthError(message)
        raise MysaError(f"AWS request failed ({response.status} {code}): {message}")

    def _set_tokens_from_auth_result(self, auth_result: dict[str, Any], *, require_refresh: bool) -> None:
        """Store fresh session tokens."""
        id_token = auth_result.get("IdToken")
        access_token = auth_result.get("AccessToken")
        refresh_token = auth_result.get("RefreshToken") if require_refresh else self._tokens.refresh_token if self._tokens else None
        if not id_token or not access_token or not refresh_token:
            raise MysaAuthError("Invalid authentication response from Cognito")

        expires_in = int(auth_result.get("ExpiresIn", 3600))
        self._tokens = SessionTokens(
            id_token=id_token,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in - 60,
        )
        self._iot_credentials = None

    @staticmethod
    def _device_type_from_model(model: str) -> int:
        """Map model string to command payload type."""
        if model.startswith("BB-V1"):
            return 1
        if model.startswith("AC-V1"):
            return 2
        if model.startswith("BB-V2"):
            return 5 if model.endswith("-L") else 4
        return 0

    def _build_change_state_payload(
        self,
        *,
        device_id: str,
        model: str,
        setpoint: float | None,
        mode: str | None,
        fan_speed: str | None,
    ) -> dict[str, Any]:
        """Build change-state message compatible with mysa-js-sdk."""
        now_epoch_ms = int(time.time() * 1000)
        now_epoch = int(time.time())

        payload: dict[str, Any] = {
            "msg": 2,
            "id": now_epoch_ms,
            "time": now_epoch,
            "ver": "1.0",
            "src": {
                "ref": self.username,
                "type": 100,
            },
            "dest": {
                "ref": device_id,
                "type": 1,
            },
            "resp": 2,
            "body": {
                "ver": 1,
                "type": self._device_type_from_model(model),
                "cmd": [
                    {
                        "tm": -1,
                        "sp": setpoint,
                        "md": MODE_TO_RAW.get(mode) if mode else None,
                        "fn": FAN_TO_RAW.get(fan_speed) if fan_speed else None,
                    }
                ],
            },
        }

        return _strip_none(payload)


def _extract_aws_error_code(data: dict[str, Any]) -> str:
    """Extract AWS error code from JSON error payload."""
    raw = str(data.get("__type") or data.get("code") or "UnknownError")
    return raw.split("#")[-1]


def _parse_aws_datetime(raw: str | float | int) -> float:
    """Parse AWS datetime string to epoch seconds."""
    if isinstance(raw, (float, int)):
        return float(raw)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw).timestamp()


def _aws_timestamps() -> tuple[str, str]:
    """Return AWS timestamp pair (amz_date, date_stamp)."""
    now = datetime.now(UTC)
    return now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")


def _aws_sigv4_authorization(
    *,
    method: str,
    canonical_uri: str,
    canonical_query: str,
    headers: dict[str, str],
    payload: bytes,
    access_key_id: str,
    secret_key: str,
    region: str,
    service: str,
    amz_date: str,
    date_stamp: str,
) -> str:
    """Build SigV4 Authorization header."""
    normalized_headers = {k.lower(): v.strip() for k, v in headers.items()}
    signed_header_names = sorted(normalized_headers)
    canonical_headers = "".join(f"{name}:{normalized_headers[name]}\n" for name in signed_header_names)
    signed_headers = ";".join(signed_header_names)

    payload_hash = hashlib.sha256(payload).hexdigest()

    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    signing_key = _aws_signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )


def _aws_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    """Derive AWS SigV4 signing key."""
    k_date = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    return _hmac_sha256(k_service, "aws4_request")


def _hmac_sha256(key: bytes, value: str) -> bytes:
    """Compute HMAC-SHA256."""
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _strip_none(value: Any) -> Any:
    """Recursively remove None values from dicts/lists."""
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value if v is not None]
    return value
