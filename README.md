# mysa2ha

Native Home Assistant integration for Mysa thermostats.

## HACS Installation

1. Open HACS in Home Assistant.
2. Go to **Integrations**.
3. Open the 3-dot menu, choose **Custom repositories**.
4. Add this repository URL and set category to **Integration**.
5. Search for **Mysa** in HACS and install it.
6. Restart Home Assistant.
7. Go to **Settings -> Devices & Services -> Add Integration**.
8. Search for **Mysa** and enter your Mysa credentials.

## Manual Installation

1. Copy `custom_components/mysa` into your Home Assistant config directory.
2. Restart Home Assistant.
3. Go to **Settings -> Devices & Services -> Add Integration**.
4. Search for **Mysa** and enter your Mysa credentials.

## Features

- Discovers Mysa devices from your account
- Creates one `climate` entity per thermostat
- Creates `temperature`, `humidity`, and `power` sensors per thermostat
- Supports mode changes, target temperature, and AC fan mode

## Notes

- This integration uses undocumented Mysa cloud APIs and AWS IoT commands.
- API compatibility may break if Mysa changes backend behavior.

## Acknowledgments

- [bourquep/mysa2mqtt](https://github.com/bourquep/mysa2mqtt)
- [bourquep/mysa2ha](https://github.com/bourquep/mqtt2ha)
