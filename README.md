# WLED Browser

A powerful command-line interface for managing multiple WLED devices on your network.

## Features

### Device Discovery
- **Automatic mDNS Discovery**: Scans your network for WLED devices using zeroconf
- **Configurable Scan Time**: Adjust scan duration to find all devices
- **Persistent Device Tracking**: Maintains device state across scans, preserving groups and power states

### Device Control
- **Power Management**: Turn devices on/off individually or in groups
- **Reboot Devices**: Restart devices remotely
- **Interactive Identification**: Cycle through devices with visual feedback to identify physical locations

### UDP Sync Management
- **Enable/Disable Sync**: Control UDP sync send and receive per device
- **Sync Group Configuration**: Assign devices to sync groups (1-8) for coordinated effects
- **Flexible Group Assignment**: Set send and receive groups independently

### Device Organization
- **Custom Groups**: Organize devices into logical groups (e.g., living_room, bedroom)
- **Persistent Grouping**: Groups survive network rescans
- **Group-based Commands**: Execute commands on entire groups at once

### Device Queries
- **Power State Query**: Refresh and display current power states
- **State Inspection**: View full JSON state with optional field filtering
- **Info Inspection**: Query device information (WiFi, version, etc.) with field filtering
- **Nested Field Access**: Use dot notation and array indexing (e.g., `seg[0].bri`, `wifi.rssi`)

### Flexible Range Syntax
Commands support multiple targeting methods:
- **Single Device**: `0`, `5`
- **Ranges**: `1-5`, `0-3`
- **Multiple Selections**: `0,2-4,7`
- **Group Names**: `living_room`, `bedroom`
- **All Devices**: `all`
- **Mixed Syntax**: `0,living_room,5-7`

### Reliability Features
- **Automatic Retries**: Configurable retry count for failed operations
- **Retry Command**: Re-execute failed operations on only the failed devices
- **Error Reporting**: Clear error messages with device indices for troubleshooting

### Browser Integration
- **Quick UI Access**: Launch device web interface directly from the CLI

## Installation

```bash
pip install zeroconf requests
```

## Usage

```bash
python wled_browser.py
```

### Common Commands

```
on <range>              Turn on devices
off <range>             Turn off devices
power <range>           Refresh power state display
reboot <range>          Reboot devices
id <range>              Identify devices interactively
sync <range> {on|off}   Enable/disable UDP sync
syncgroups <range> send <groups> recv <groups>
                        Configure sync group membership
state <range> [fields]  Query device state (optionally filtered)
info <range> [fields]   Query device info (optionally filtered)
group <range> <groupid> Assign devices to a group
ui <index>              Open device web UI in browser
scan [seconds]          Rescan network for devices
list                    Refresh device list display
retries <n>             Set retry count for operations
retry                   Retry last command on failed devices
help                    Show all commands
quit                    Exit
```

### Examples

```
# Turn on all living room lights
on living_room

# Turn off devices 0-5
off 0-5

# Query WiFi signal strength for all devices
info all wifi.rssi

# Set sync groups: devices 0-2 send on groups 1,3 and receive on group 2
syncgroups 0-2 send 1,3 recv 2

# View brightness of first segment for devices 3-5
state 3-5 seg[0].bri

# Group devices 0-3 as bedroom
group 0-3 bedroom

# Turn on bedroom, device 5, and devices 7-9
on bedroom,5,7-9
```

## Requirements

- Python 3.x
- zeroconf (for mDNS discovery)
- requests (for HTTP API calls)

## License

MIT
