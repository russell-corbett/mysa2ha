"""Data coordinator for Mysa integration."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MysaApiClient, MysaAuthError, MysaCannotConnect, MysaError
from .const import REALTIME_KEEPALIVE_SECONDS, REALTIME_TIMEOUT_SECONDS

DEVICE_REFRESH_SECONDS = 3600  # Re-fetch device list hourly to pick up newly added devices.


class MysaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate Mysa API polling."""

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Mysa."""
        try:
            now = time.time()
            if not self.devices or now - self._last_device_fetch >= DEVICE_REFRESH_SECONDS:
                devices_response = await self.client.async_get_devices()
                self.devices = devices_response.get("DevicesObj", {})
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
        """Keep Mysa device status publishing active for near-realtime updates."""
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
