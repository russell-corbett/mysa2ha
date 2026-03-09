"""Sensor platform for Mysa integration."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from . import MysaConfigEntry
from .entity import MysaEntity


@dataclass(frozen=True, kw_only=True)
class MysaSensorDescription(SensorEntityDescription):
    """Describes Mysa sensor entity."""

    key_path: str


SENSORS: tuple[MysaSensorDescription, ...] = (
    MysaSensorDescription(
        key="temperature",
        name="Current temperature",
        key_path="CorrectedTemp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MysaSensorDescription(
        key="humidity",
        name="Current humidity",
        key_path="Humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MysaSensorDescription(
        key="power",
        name="Current power",
        key_path="Current",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mysa sensors."""
    coordinator = entry.runtime_data.coordinator

    entities: list[SensorEntity] = []
    for device in coordinator.data.get("devices", {}).values():
        for description in SENSORS:
            entities.append(MysaSensorEntity(coordinator, device, description))
        entities.append(MysaEnergySensorEntity(coordinator, device))

    async_add_entities(entities)


class MysaSensorEntity(MysaEntity, SensorEntity):
    """Representation of Mysa numeric sensor."""

    entity_description: MysaSensorDescription

    def __init__(
        self,
        coordinator,
        device: dict[str, Any],
        description: MysaSensorDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator, device)
        self.entity_description = description
        self._attr_unique_id = f"mysa_{self._device_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """Return sensor value."""
        raw = self.state_obj.get(self.entity_description.key_path)
        if raw is None:
            return None

        value = raw.get("v")
        if value is None:
            return None

        if self.entity_description.key == "power":
            mode_obj = self.state_obj.get("TstatMode")
            duty_obj = self.state_obj.get("Duty")
            mode_value = mode_obj.get("v") if mode_obj else None
            duty_value = duty_obj.get("v") if duty_obj else None

            # Mysa's polled Current can remain non-zero while idle; gate power on actual heating/cooling activity.
            if mode_value == 1 or (duty_value is not None and float(duty_value) <= 0):
                return 0.0

            voltage_obj = self.state_obj.get("Voltage")
            if voltage_obj and voltage_obj.get("v") is not None:
                return round(float(value) * float(voltage_obj["v"]), 2)

        return float(value)


class MysaEnergySensorEntity(MysaEntity, RestoreSensor):
    """Energy sensor that integrates power (W) over time into Wh."""

    _attr_name = "Total energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, device: dict[str, Any]) -> None:
        """Initialize energy sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"mysa_{self._device_id}_energy"
        self._total_energy_wh: float = 0.0
        self._last_power: float | None = None
        self._last_update: datetime.datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last known energy on startup."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._total_energy_wh = float(last.native_value or 0)

    def _get_power(self) -> float | None:
        """Return current gated power in watts (same logic as power sensor)."""
        state = self.state_obj
        current_obj = state.get("Current")
        if current_obj is None:
            return None
        value = current_obj.get("v")
        if value is None:
            return None

        mode_obj = state.get("TstatMode")
        duty_obj = state.get("Duty")
        mode_value = mode_obj.get("v") if mode_obj else None
        duty_value = duty_obj.get("v") if duty_obj else None

        if mode_value == 1 or (duty_value is not None and float(duty_value) <= 0):
            return 0.0

        voltage_obj = state.get("Voltage")
        if voltage_obj and voltage_obj.get("v") is not None:
            return float(value) * float(voltage_obj["v"])

        return float(value)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Integrate power into energy on each coordinator update."""
        now = dt_util.utcnow()
        power = self._get_power()

        if (
            power is not None
            and self._last_power is not None
            and self._last_update is not None
        ):
            dt_hours = (now - self._last_update).total_seconds() / 3600
            self._total_energy_wh += (self._last_power + power) / 2 * dt_hours

        if power is not None:
            self._last_power = power
            self._last_update = now

        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        """Return accumulated energy in Wh."""
        return round(self._total_energy_wh, 2)
