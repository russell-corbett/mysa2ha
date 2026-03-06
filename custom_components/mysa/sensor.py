"""Sensor platform for Mysa integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

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

    entities: list[MysaSensorEntity] = []
    for device in coordinator.data.get("devices", {}).values():
        for description in SENSORS:
            entities.append(MysaSensorEntity(coordinator, device, description))

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
