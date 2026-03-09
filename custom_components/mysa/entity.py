"""Base entities for Mysa integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MysaDataUpdateCoordinator


class MysaEntity(CoordinatorEntity[MysaDataUpdateCoordinator]):
    """Base entity for Mysa devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MysaDataUpdateCoordinator,
        device: dict[str, Any],
    ) -> None:
        """Initialize base entity."""
        super().__init__(coordinator)
        self._device = device
        self._device_id = str(device["Id"])

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._device.get("Name") or f"Mysa {self._device_id}",
            manufacturer="Mysa",
            model=self._device.get("Model"),
            hw_version=self._device.get("Format"),
            suggested_area=self._device.get("Zone"),
            configuration_url="https://app-prod.mysa.cloud",
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not super().available:
            return False

        state = self.state_obj
        connected = state.get("Connected", {}).get("v")
        if connected is not None and not connected:
            return False

        return bool(state)

    @property
    def state_obj(self) -> dict[str, Any]:
        """Return latest state object for this device."""
        return self.coordinator.data.get("states", {}).get(self._device_id, {})
