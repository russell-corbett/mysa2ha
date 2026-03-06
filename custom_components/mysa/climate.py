"""Climate platform for Mysa integration."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MysaConfigEntry
from .const import RAW_TO_FAN, RAW_TO_MODE
from .entity import MysaEntity

HA_AC_MODES = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.DRY, HVACMode.FAN_ONLY, HVACMode.HEAT_COOL]
HA_HEAT_ONLY_MODES = [HVACMode.OFF, HVACMode.HEAT]

HVAC_TO_MYSA = {
    HVACMode.OFF: "off",
    HVACMode.HEAT: "heat",
    HVACMode.COOL: "cool",
    HVACMode.DRY: "dry",
    HVACMode.FAN_ONLY: "fan_only",
    HVACMode.HEAT_COOL: "auto",
    HVACMode.AUTO: "auto",
}

MYSA_TO_HVAC = {
    "off": HVACMode.OFF,
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "fan_only": HVACMode.FAN_ONLY,
    "auto": HVACMode.HEAT_COOL,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mysa climate entities."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        MysaClimateEntity(coordinator, device) for device in coordinator.data.get("devices", {}).values()
    )


class MysaClimateEntity(MysaEntity, ClimateEntity):
    """Representation of a Mysa thermostat."""

    def __init__(self, coordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)

        self._attr_unique_id = f"mysa_{self._device_id}_climate"
        self._attr_name = "Thermostat"
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_target_temperature_step = 0.5

        is_ac = str(device.get("Model", "")).startswith("AC")
        self._attr_hvac_modes = HA_AC_MODES if is_ac else HA_HEAT_ONLY_MODES

        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        if is_ac:
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = ["auto", "low", "medium", "high", "max"]

        min_setpoint = device.get("MinSetpoint")
        max_setpoint = device.get("MaxSetpoint")
        if min_setpoint is not None:
            self._attr_min_temp = float(min_setpoint)
        if max_setpoint is not None:
            self._attr_max_temp = float(max_setpoint)

        self._pending_target_temperature: float | None = None
        self._pending_hvac_mode: HVACMode | None = None
        self._pending_fan_mode: str | None = None

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        return _state_value(self.state_obj, "CorrectedTemp")

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        if self._pending_target_temperature is not None:
            return self._pending_target_temperature
        if self.hvac_mode == HVACMode.OFF:
            return None
        return _state_value(self.state_obj, "SetPoint")

    @property
    def current_humidity(self) -> float | None:
        """Return current humidity."""
        return _state_value(self.state_obj, "Humidity")

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current hvac mode."""
        if self._pending_hvac_mode is not None:
            return self._pending_hvac_mode
        mode_value = _state_value(self.state_obj, "TstatMode")
        if mode_value is None:
            return None
        mysa_mode = RAW_TO_MODE.get(int(mode_value))
        if mysa_mode is None:
            return None
        return MYSA_TO_HVAC.get(mysa_mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return running action."""
        mode = self.hvac_mode
        if mode == HVACMode.OFF:
            return HVACAction.OFF

        duty_value = _state_value(self.state_obj, "Duty")
        if duty_value is None:
            return None

        if mode == HVACMode.HEAT:
            return HVACAction.HEATING if duty_value > 0 else HVACAction.IDLE
        if mode == HVACMode.COOL:
            return HVACAction.COOLING if duty_value > 0 else HVACAction.IDLE
        if mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        return HVACAction.IDLE if duty_value <= 0 else None

    @property
    def fan_mode(self) -> str | None:
        """Return fan mode."""
        if self._pending_fan_mode is not None:
            return self._pending_fan_mode
        fan_value = _state_value(self.state_obj, "FanSpeed")
        return RAW_TO_FAN.get(int(fan_value)) if fan_value is not None else None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return

        setpoint = float(temperature)
        if self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            setpoint = (setpoint - 32.0) * 5.0 / 9.0

        # Mysa setpoints are in 0.5C increments.
        setpoint = round(setpoint * 2) / 2
        if self._attr_min_temp is not None:
            setpoint = max(setpoint, float(self._attr_min_temp))
        if self._attr_max_temp is not None:
            setpoint = min(setpoint, float(self._attr_max_temp))

        self._pending_target_temperature = setpoint
        self.async_write_ha_state()

        await self.coordinator.client.async_set_device_state(self._device, setpoint=setpoint)
        self.hass.async_create_task(self._async_delayed_refresh())

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        mysa_mode = HVAC_TO_MYSA.get(hvac_mode)
        if mysa_mode is None:
            return

        self._pending_hvac_mode = hvac_mode
        self.async_write_ha_state()

        await self.coordinator.client.async_set_device_state(self._device, mode=mysa_mode)
        self.hass.async_create_task(self._async_delayed_refresh())

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""
        self._pending_fan_mode = fan_mode
        self.async_write_ha_state()

        await self.coordinator.client.async_set_device_state(self._device, fan_speed=fan_mode)
        self.hass.async_create_task(self._async_delayed_refresh())

    async def _async_delayed_refresh(self) -> None:
        """Refresh after a short delay to allow cloud state propagation."""
        await asyncio.sleep(4)
        await self.coordinator.async_request_refresh()
        self._pending_target_temperature = None
        self._pending_hvac_mode = None
        self._pending_fan_mode = None
        self.async_write_ha_state()


def _state_value(state_obj: dict[str, Any], key: str) -> float | None:
    """Read Mysa state object value."""
    raw = state_obj.get(key)
    if not raw:
        return None
    value = raw.get("v")
    if value is None:
        return None
    return float(value)
