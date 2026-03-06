"""Diagnostics support for Mysa."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import MysaConfigEntry

TO_REDACT = {
    "username",
    "password",
    "id_token",
    "access_token",
    "refresh_token",
    "session_token",
    "Authorization",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MysaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    _ = hass
    coordinator = entry.runtime_data.coordinator
    client = entry.runtime_data.client

    return {
        "entry": async_redact_data(dict(entry.as_dict()), TO_REDACT),
        "device_count": len(coordinator.data.get("devices", {})),
        "state_count": len(coordinator.data.get("states", {})),
        "sample_devices": async_redact_data(coordinator.data.get("devices", {}), TO_REDACT),
        "sample_states": async_redact_data(coordinator.data.get("states", {}), TO_REDACT),
        "token_loaded": client.has_tokens,
    }
