# mysa2ha

**Native Home Assistant integration for Mysa smart thermostats.**

[![GitHub Release](https://img.shields.io/github/v/release/russell-corbett/mysa2ha?style=for-the-badge&color=4B9CD3)](https://github.com/russell-corbett/mysa2ha/releases)
[![GitHub Downloads](https://img.shields.io/github/downloads/russell-corbett/mysa2ha/total?style=for-the-badge&label=Downloads&color=4B9CD3)](https://github.com/russell-corbett/mysa2ha/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge&logo=home-assistant)](https://hacs.xyz)
[![HA Min Version](https://img.shields.io/badge/Min%20HA%20Version-2024.1.0-blue?style=for-the-badge&logo=home-assistant)](https://www.home-assistant.io)
[![GitHub Issues](https://img.shields.io/github/issues/russell-corbett/mysa2ha?style=for-the-badge)](https://github.com/russell-corbett/mysa2ha/issues)
[![GitHub Stars](https://img.shields.io/github/stars/russell-corbett/mysa2ha?style=for-the-badge)](https://github.com/russell-corbett/mysa2ha/stargazers)

---

## Overview

mysa2ha connects your [Mysa](https://getmysa.com) smart thermostats to Home Assistant without a local broker or additional hardware. It authenticates directly with the Mysa cloud using your account credentials, then uses the AWS IoT data plane to send commands and a REST API to poll device state.

> **Note:** This integration uses undocumented Mysa cloud APIs. API compatibility may break if Mysa changes their backend.

---

## Features

| Feature | Details |
|---|---|
| **Devices** | Baseboard heaters (BB-V1, BB-V2, BB-V2-L) and mini-split ACs (AC-V1) |
| **Climate entity** | Set target temperature, HVAC mode, and fan speed |
| **Sensors** | Current temperature, humidity, live power (W), accumulated energy (Wh) |
| **Optimistic UI** | State updates instantly in the HA UI; confirmed against the device within ~12 s |
| **Auto-discovery** | All Mysa devices on your account are discovered automatically, re-checked hourly |
| **Realtime updates** | Devices are asked to publish frequent state updates; falls back to polling |
| **Poll interval** | Configurable from 5 s to 600 s (default 10 s) via the integration options |
| **Re-auth flow** | Password can be updated in HA without removing and re-adding the integration |

---

## Installation

### HACS (Recommended)

1. Open **HACS** in Home Assistant.
2. Go to **Integrations**.
3. Open the 3-dot menu and choose **Custom repositories**.
4. Add `https://github.com/russell-corbett/mysa2ha` and set the category to **Integration**.
5. Search for **Mysa** in HACS and install it.
6. Restart Home Assistant.
7. Go to **Settings → Devices & Services → Add Integration**.
8. Search for **Mysa** and enter your Mysa account credentials.

### Manual

1. Download the [latest release](https://github.com/russel-corbett/mysa2ha/releases/latest).
2. Copy the `custom_components/mysa` folder into your Home Assistant `config/custom_components/` directory.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration**.
5. Search for **Mysa** and enter your Mysa account credentials.

---

## Entities

Each thermostat creates the following entities in Home Assistant:

### Climate
| Entity | Description |
|---|---|
| `climate.{name}_thermostat` | Main thermostat control — mode, target temperature, fan speed |

### Sensors
| Entity | Unit | Description |
|---|---|---|
| `sensor.{name}_current_temperature` | °C | Room temperature as measured by the device |
| `sensor.{name}_current_humidity` | % | Relative humidity |
| `sensor.{name}_current_power` | W | Live power draw (gated to 0 W when idle or off) |
| `sensor.{name}_total_energy` | Wh | Accumulated energy since first setup, persisted across restarts |

---

## Configuration

After adding the integration, options can be changed via **Settings → Devices & Services → Mysa → Configure**:

| Option | Default | Range | Description |
|---|---|---|---|
| Poll interval | 10 s | 5 – 600 s | How often HA polls the Mysa API for device state |

---

## Supported Models

| Model | Type | Modes |
|---|---|---|
| BB-V1 | Baseboard heater | Heat, Off |
| BB-V2 / BB-V2-L | Baseboard heater | Heat, Off |
| AC-V1 | Mini-split AC | Heat, Cool, Heat/Cool (Auto), Dry, Fan Only, Off |

---

## Troubleshooting

**Thermostat becomes unavailable periodically**
Mysa devices report a `Connected` field. If the device loses its Wi-Fi connection, HA will mark it unavailable until it reconnects.

**State doesn't update after changing temperature**
The integration retries commands automatically. If the device still doesn't respond after ~12 seconds, a warning is logged. Check the device's Wi-Fi signal strength.

**Re-authentication required**
If your Mysa password changes, HA will prompt you to re-enter it via a notification. You can also trigger this manually from the integration page.

**Enabling debug logs**
Add the following to your `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.mysa: debug
```

---

## Technical Notes

- Authentication uses AWS Cognito SRP (Secure Remote Password) — credentials are never sent in plaintext.
- Commands are published to the device via the AWS IoT data plane using short-lived IAM credentials obtained from a Cognito identity pool.
- The energy sensor uses trapezoidal integration of the power sensor over time and persists its value across HA restarts.

---

## Acknowledgments

- [bourquep/mysa2mqtt](https://github.com/bourquep/mysa2mqtt) — original reverse engineering of the Mysa protocol
- [bourquep/mqtt2ha](https://github.com/bourquep/mqtt2ha) — earlier HA integration approach
- [bourquep/mysa-js-sdk](https://github.com/bourquep/mysa-js-sdk) — reference implementation of the Mysa command payload format
- [dlenski/mysotherm](https://github.com/dlenski/mysotherm) — additional protocol research and thermostat API documentation

