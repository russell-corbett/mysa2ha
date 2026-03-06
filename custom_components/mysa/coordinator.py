"""Data coordinator for Mysa integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MysaApiClient, MysaAuthError, MysaCannotConnect, MysaError


class MysaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate Mysa API polling."""

    def __init__(
        self, hass: HomeAssistant, client: MysaApiClient, update_interval: timedelta
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            logger=client.logger,
            name="mysa",
            update_interval=update_interval,
        )
        self.client = client
        self.devices: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Mysa."""
        try:
            if not self.devices:
                devices_response = await self.client.async_get_devices()
                self.devices = devices_response.get("DevicesObj", {})

            states_response = await self.client.async_get_device_states()
            states = states_response.get("DeviceStatesObj", {})

            return {
                "devices": self.devices,
                "states": states,
            }
        except MysaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except (MysaCannotConnect, MysaError) as err:
            raise UpdateFailed(f"Error communicating with Mysa API: {err}") from err
