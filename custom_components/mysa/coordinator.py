"""Data coordinator for Mysa integration."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

from .api import MysaApiClient, MysaAuthError, MysaCannotConnect, MysaError
from .const import REALTIME_KEEPALIVE_SECONDS, REALTIME_TIMEOUT_SECONDS
from .mqtt_client import MysaMqttClient

DEVICE_REFRESH_SECONDS = 3600  # Re-fetch device list hourly to pick up newly added devices.


class MysaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate Mysa API polling and real-time MQTT updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MysaApiClient,
        update_interval: timedelta,
        selected_device_ids: set[str] | None = None,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name="mysa",
            update_interval=update_interval,
        )
        self.client = client
        self.devices: dict[str, dict[str, Any]] = {}
        self._selected_device_ids = selected_device_ids
        self._last_realtime_keepalive: dict[str, float] = {}
        self._last_device_fetch: float = 0.0
        self._mqtt_client: MysaMqttClient | None = None

    # ------------------------------------------------------------------
    # MQTT real-time lifecycle
    # ------------------------------------------------------------------

    async def async_start_realtime(self) -> None:
        """Start the MQTT real-time subscription after initial data load."""
        self._mqtt_client = MysaMqttClient(
            url_factory=self.client.async_get_signed_ws_url,
            on_message=self._handle_realtime_message,
        )
        await self._mqtt_client.start()

        # Subscribe to all currently selected (active) devices.
        active_ids = (
            self._selected_device_ids
            if self._selected_device_ids is not None
            else set(self.devices)
        )
        for device_id in active_ids:
            await self._mqtt_client.subscribe(device_id)

        _LOGGER.debug(
            "Mysa MQTT real-time subscriptions started for %d device(s)", len(active_ids)
        )

    async def async_stop_realtime(self) -> None:
        """Stop the MQTT real-time connection."""
        if self._mqtt_client:
            await self._mqtt_client.stop()
            self._mqtt_client = None
            _LOGGER.debug("Mysa MQTT real-time subscriptions stopped")

    @callback
    def _handle_realtime_message(self, device_id: str, msg: dict[str, Any]) -> None:
        """Handle an incoming real-time device status message from MQTT.

        Patches the coordinator's state dict for the affected device and
        notifies all entity listeners immediately — no REST poll required.
        """
        patch = _mqtt_message_to_state_patch(msg)
        if not patch:
            return
        if self.data is None:
            return

        current_states = self.data.get("states", {})
        updated_device_state = {**current_states.get(device_id, {}), **patch}
        self.async_set_updated_data(
            {
                **self.data,
                "states": {**current_states, device_id: updated_device_state},
            }
        )
        _LOGGER.debug(
            "Mysa real-time update device=%s fields=%s", device_id, list(patch)
        )

    # ------------------------------------------------------------------
    # Polling update (REST fallback)
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Mysa REST API."""
        try:
            now = time.time()
            if not self.devices or now - self._last_device_fetch >= DEVICE_REFRESH_SECONDS:
                devices_response = await self.client.async_get_devices()
                new_devices = devices_response.get("DevicesObj", {})

                # Subscribe to any devices that appeared since last refresh.
                if self._mqtt_client:
                    active_ids = (
                        self._selected_device_ids
                        if self._selected_device_ids is not None
                        else set(new_devices)
                    )
                    for device_id in active_ids:
                        await self._mqtt_client.subscribe(device_id)

                self.devices = new_devices
                self._last_device_fetch = now

            # Apply device selection filter — only expose devices the user has selected.
            if self._selected_device_ids is not None:
                active_devices = {
                    k: v for k, v in self.devices.items() if k in self._selected_device_ids
                }
            else:
                active_devices = self.devices

            await self._async_refresh_realtime_keepalive()

            states_response = await self.client.async_get_device_states()
            states = states_response.get("DeviceStatesObj", {})

            return {
                "devices": active_devices,
                "states": states,
            }
        except MysaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except (MysaCannotConnect, MysaError) as err:
            raise UpdateFailed(f"Error communicating with Mysa API: {err}") from err

    async def _async_refresh_realtime_keepalive(self) -> None:
        """Keep Mysa device status publishing active (MsgType 11 renewal).

        The device stops publishing status updates if it does not receive a
        renewal within ``REALTIME_TIMEOUT_SECONDS``.  We send a renewal every
        ``REALTIME_KEEPALIVE_SECONDS`` (just before the timeout) to keep the
        device publishing continuously.
        """
        now = time.time()
        active_ids = (
            self._selected_device_ids if self._selected_device_ids is not None else set(self.devices)
        )
        due_device_ids = [
            device_id
            for device_id in active_ids
            if device_id in self.devices
            and now - self._last_realtime_keepalive.get(device_id, 0) >= REALTIME_KEEPALIVE_SECONDS
        ]

        if not due_device_ids:
            return

        results = await asyncio.gather(
            *(
                self.client.async_start_publishing_device_status(
                    device_id=device_id,
                    timeout_seconds=REALTIME_TIMEOUT_SECONDS,
                )
                for device_id in due_device_ids
            ),
            return_exceptions=True,
        )

        for device_id, result in zip(due_device_ids, results, strict=True):
            if isinstance(result, Exception):
                if isinstance(result, MysaAuthError):
                    raise result
                _LOGGER.debug("Realtime keepalive failed for %s: %s", device_id, result)
                continue

            self._last_realtime_keepalive[device_id] = now


# ------------------------------------------------------------------
# MQTT message → state dict patch helpers
# ------------------------------------------------------------------


def _mqtt_message_to_state_patch(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert an MQTT device message to a partial coordinator state dict.

    The coordinator stores state in the same format as the REST API:
    ``{"FieldName": {"v": value}}``.  This function maps the MQTT payload
    fields (which use different names and structure) to that format so the
    existing entity code requires no changes.

    Supported MQTT message types:
    - MsgType 0  / msg 0  : V1 device status (BB-V1)
    - MsgType 40 / msg 40 : V2 device status (BB-V2, AC-V1)
    - MsgType 44 / msg 44 : State-change acknowledgement (command response)
    - MsgType 1  / msg 1  : Setpoint-change notification
    """
    patch: dict[str, Any] = {}

    # Older messages use "MsgType"; newer envelope format uses "msg".
    msg_type = msg.get("MsgType") if "MsgType" in msg else msg.get("msg")

    if msg_type == 0:
        # V1 status: flat fields directly on the message object.
        _map_fields(
            msg,
            patch,
            {
                "MainTemp": "CorrectedTemp",
                "Humidity": "Humidity",
                "Current": "Current",
                "SetPoint": "SetPoint",
            },
        )

    elif msg_type == 40:
        # V2 status: fields nested under "body".
        body = msg.get("body", {})
        _map_fields(
            body,
            patch,
            {
                "ambTemp": "CorrectedTemp",
                "hum": "Humidity",
                "stpt": "SetPoint",
                "dtyCycle": "Duty",
            },
        )

    elif msg_type == 44:
        # State-change acknowledgement: device confirms a command was applied.
        body = msg.get("body", {})
        state = body.get("state", {})
        _map_fields(
            state,
            patch,
            {
                "md": "TstatMode",   # raw mode (1=off, 3=heat, 4=cool, …)
                "sp": "SetPoint",
                "fn": "FanSpeed",    # raw fan speed (AC-V1 only)
            },
        )

    elif msg_type == 1:
        # Setpoint-change notification: device reports a manual dial adjustment.
        if "Next" in msg and msg["Next"] is not None:
            patch["SetPoint"] = {"v": msg["Next"]}

    return patch


def _map_fields(
    src: dict[str, Any],
    dst: dict[str, Any],
    mapping: dict[str, str],
) -> None:
    """Copy fields from ``src`` to ``dst`` using the provided key mapping.

    Values are wrapped in the REST-compatible ``{"v": value}`` format.
    Fields missing from ``src`` or with a ``None`` value are skipped.
    """
    for src_key, dst_key in mapping.items():
        value = src.get(src_key)
        if value is not None:
            dst[dst_key] = {"v": value}
