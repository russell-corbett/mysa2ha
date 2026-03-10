"""The Mysa integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .api import MysaApiClient, MysaAuthError, MysaCannotConnect, MysaError
from .const import CONF_POLL_INTERVAL, CONF_SELECTED_DEVICES, DEFAULT_POLL_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import MysaDataUpdateCoordinator


@dataclass
class MysaRuntimeData:
    """Runtime data for a Mysa config entry."""

    client: MysaApiClient
    coordinator: MysaDataUpdateCoordinator


MysaConfigEntry = ConfigEntry[MysaRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: MysaConfigEntry) -> bool:
    """Set up Mysa from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    client = MysaApiClient(hass=hass, username=username, password=password)

    try:
        await client.async_login()
    except MysaAuthError as err:
        raise ConfigEntryAuthFailed("Mysa authentication failed") from err
    except MysaCannotConnect as err:
        raise ConfigEntryNotReady(f"Unable to connect to Mysa: {err}") from err

    poll_seconds = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    raw_selected = entry.options.get(CONF_SELECTED_DEVICES)
    selected_device_ids = set(raw_selected) if raw_selected is not None else None
    coordinator = MysaDataUpdateCoordinator(
        hass=hass,
        client=client,
        update_interval=timedelta(seconds=poll_seconds),
        selected_device_ids=selected_device_ids,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except MysaAuthError as err:
        raise ConfigEntryAuthFailed("Mysa authentication failed") from err
    except MysaCannotConnect as err:
        raise ConfigEntryNotReady(f"Unable to fetch initial Mysa data: {err}") from err
    except MysaError as err:
        raise ConfigEntryNotReady(f"Unexpected Mysa error: {err}") from err

    entry.runtime_data = MysaRuntimeData(client=client, coordinator=coordinator)

    # Start MQTT real-time subscriptions now that we have authenticated credentials
    # and an initial device list.  Cleanup is registered via async_on_unload so
    # it runs automatically when the entry is unloaded or reloaded.
    await coordinator.async_start_realtime()
    entry.async_on_unload(coordinator.async_stop_realtime)

    if selected_device_ids is not None:
        dev_reg = dr.async_get(hass)
        for device_entry in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            mysa_id = next(
                (ident[1] for ident in device_entry.identifiers if ident[0] == DOMAIN),
                None,
            )
            if mysa_id is not None and mysa_id not in selected_device_ids:
                dev_reg.async_remove_device(device_entry.id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MysaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: MysaConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
