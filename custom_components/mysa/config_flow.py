"""Config flow for Mysa integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_POLL_INTERVAL,
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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle first step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip().lower()
            password = user_input[CONF_PASSWORD]

            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()

            try:
                from .api import MysaApiClient, MysaAuthError, MysaCannotConnect

                client = MysaApiClient(self.hass, username=username, password=password)
                await client.async_login()
            except MysaAuthError:
                errors["base"] = "invalid_auth"
            except MysaCannotConnect:
                _LOGGER.exception("Cannot connect to Mysa during initial config")
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Mysa initial config")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                    options={
                        CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                    },
                )

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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start reauth flow."""
        _ = entry_data
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
                from .api import MysaApiClient, MysaAuthError, MysaCannotConnect

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


class MysaOptionsFlow(config_entries.OptionsFlow):
    """Handle options for a Mysa config entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=self.config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL))
                }
            ),
        )
