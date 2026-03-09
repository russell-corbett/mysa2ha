"""Config flow for Mysa integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import MysaConfigEntry
from .api import MysaApiClient, MysaAuthError, MysaCannotConnect
from .const import (
    CONF_POLL_INTERVAL,
    CONF_SELECTED_DEVICES,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class MysaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mysa."""

    VERSION = 1
    _reauth_entry: config_entries.ConfigEntry | None = None

    def __init__(self) -> None:
        """Initialize flow state."""
        super().__init__()
        self._username: str = ""
        self._password: str = ""
        self._discovered_devices: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle credentials step — validate login and discover devices."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip().lower()
            password = user_input[CONF_PASSWORD]

            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()

            try:
                client = MysaApiClient(self.hass, username=username, password=password)
                await client.async_login()
                devices_response = await client.async_get_devices()
                devices = devices_response.get("DevicesObj", {})
            except MysaAuthError:
                errors["base"] = "invalid_auth"
            except MysaCannotConnect:
                _LOGGER.exception("Cannot connect to Mysa during initial config")
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Mysa initial config")
                errors["base"] = "unknown"
            else:
                self._username = username
                self._password = password
                self._discovered_devices = devices
                return await self.async_step_select_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle device selection step — choose which thermostats to add."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_DEVICES, [])
            if not selected:
                errors[CONF_SELECTED_DEVICES] = "no_devices_selected"
            else:
                return self.async_create_entry(
                    title=self._username,
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                    },
                    options={
                        CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                        CONF_SELECTED_DEVICES: selected,
                    },
                )

        device_options = [
            SelectOptionDict(
                value=device_id,
                label=device.get("Name") or f"Mysa {device_id}",
            )
            for device_id, device in self._discovered_devices.items()
        ]

        # Pre-select all devices by default.
        all_ids = list(self._discovered_devices.keys())

        return self.async_show_form(
            step_id="select_devices",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SELECTED_DEVICES, default=all_ids): SelectSelector(
                        SelectSelectorConfig(
                            options=device_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> FlowResult:
        """Start reauth flow."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle reauth."""
        errors: dict[str, str] = {}

        if user_input is not None:
            assert self._reauth_entry is not None
            username = self._reauth_entry.data[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                client = MysaApiClient(self.hass, username=username, password=password)
                await client.async_login()
            except MysaAuthError:
                errors["base"] = "invalid_auth"
            except MysaCannotConnect:
                _LOGGER.exception("Cannot connect to Mysa during reauth")
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Mysa reauth")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: password,
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow handler."""
        return MysaOptionsFlow(config_entry)


class MysaOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Handle options for a Mysa config entry."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage options — poll interval and active device selection."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get the current device list from the running coordinator.
        entry: MysaConfigEntry = self.config_entry  # type: ignore[assignment]
        all_devices: dict[str, Any] = entry.runtime_data.coordinator.devices

        device_options = [
            SelectOptionDict(
                value=device_id,
                label=device.get("Name") or f"Mysa {device_id}",
            )
            for device_id, device in all_devices.items()
        ]

        current_selected: list[str] = self.config_entry.options.get(
            CONF_SELECTED_DEVICES,
            list(all_devices.keys()),  # default: all devices selected
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=self.config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)),
                    vol.Required(
                        CONF_SELECTED_DEVICES,
                        default=current_selected,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=device_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )
