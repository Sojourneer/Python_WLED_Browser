import socket
import time
import webbrowser
import requests
import os
import readline  # Enables command-line editing (arrow keys, history, etc.)
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

# Global retry count for device commands
retry_count = 0

# Track last command for retry functionality
last_command = None  # Full command string
last_failed_indices = []  # List of indices that failed

class WLEDListener(ServiceListener):
    """
    A listener class to collect discovered WLED services.
    """
    def __init__(self):
        self.services = {}

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        if name in self.services:
            del self.services[name]

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info:
            # Get IP address
            address = socket.inet_ntoa(info.addresses[0]) if info.addresses else 'N/A'
            
            # Use the mDNS instance name as the friendly name, stripping the service type suffix.
            # Example: "MyRoomWLED._wled._tcp.local." becomes "MyRoomWLED"
            friendly_name = name.removesuffix(type_).removesuffix('.')

            # DEBUG: Print all raw properties to the console for troubleshooting
            #print(f"DEBUG: Properties for {friendly_name}: {info.properties}\n")

            self.services[name] = {
                'name_long': name,
                'host_ip': address,
                'port': info.port,
                'friendly_name': friendly_name,
                'group': '_default',  # Group identifier
                'power_state': None,  # Cache for power state
                'sync_enabled': None, # Cache for UDP sync on/off
                'sync_send': None,    # Cache for sync send groups (bitmask)
                'sync_recv': None     # Cache for sync recv groups (bitmask)
            }

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        # Preserve group and cached state when service info changes
        existing = self.services.get(name)
        self.add_service(zc, type_, name)
        if existing and name in self.services:
            # Restore preserved values
            self.services[name]['group'] = existing.get('group', '_default')
            self.services[name]['power_state'] = existing.get('power_state', None) 


def scan_wled_devices(discovery_time_seconds=10, service_db=None):
    """
    Scan for WLED devices on the network.
    
    Args:
        discovery_time_seconds: Time to scan for devices (default: 10)
        service_db: Service database to preserve group assignments
    
    Returns:
        List of discovered services.
    """
    service_type = "_wled._tcp.local."

    zeroconf = Zeroconf()
    listener = WLEDListener()
    browser = ServiceBrowser(zeroconf, service_type, listener)
    
    print(f"Scanning for {service_type} services for {discovery_time_seconds} seconds...")
    
    try:
        time.sleep(discovery_time_seconds) 
    except KeyboardInterrupt:
        pass
    finally:
        zeroconf.close()

    # Treat service_db as the working database
    # Use new scan results to update/add devices
    # Key: host_ip (invariant during script lifetime)
    if service_db:
        # Build lookup by host_ip
        existing_by_ip = {s['host_ip']: s for s in service_db}
        
        # Process new scan results
        for new_service in listener.services.values():
            ip = new_service['host_ip']
            if ip in existing_by_ip:
                # Known device - update mDNS info but preserve group/state
                existing_by_ip[ip]['name_long'] = new_service['name_long']
                existing_by_ip[ip]['port'] = new_service['port']
                existing_by_ip[ip]['friendly_name'] = new_service['friendly_name']
                # group and power_state are preserved
            else:
                # New device - add it to the database
                existing_by_ip[ip] = new_service
        
        services_list = list(existing_by_ip.values())
    else:
        # First scan - use results as-is
        services_list = list(listener.services.values())
    
    # Sort by group (with _default first), then by friendly_name
    services_list.sort(key=lambda s: (s['group'] != '_default', s['group'].lower(), s['friendly_name'].lower()))
    return services_list


def display_services(services_list):
    """
    Display the list of discovered WLED services with cached power state, grouped by group.
    """
    if not services_list:
        print("No WLED services found.")
        return
    
    # Count devices per group
    from collections import Counter
    group_counts = Counter(s['group'] for s in services_list)
    
    print("\n--- Discovered WLED Hosts ---")
    current_group = None
    for i, service in enumerate(services_list):
        # Display group header when group changes
        if service['group'] != current_group:
            current_group = service['group']
            count = group_counts[current_group]
            print(f"\n--- Group: {current_group} ({count}) ---")
        
        state_indicator = ""
        if service['power_state'] is True:
            state_indicator = "[ON] "
        elif service['power_state'] is False:
            state_indicator = "[OFF]"
        else:
            state_indicator = "[???]"
        print(f"{i}. {state_indicator} {service['friendly_name']} ({service['host_ip']}:{service['port']})")
    print("-----------------------------")


def parse_sync_groups(groups_str):
    """
    Parse sync group specification like '1,3,5' into a bitmask.
    
    Args:
        groups_str: String like '1,3' or empty string/'none' for no groups
    
    Returns:
        Integer bitmask (0-255), or None if invalid
    """
    if not groups_str or groups_str.strip() == '' or groups_str.lower() == 'none':
        return 0
    
    try:
        bitmask = 0
        parts = groups_str.split(',')
        for part in parts:
            part = part.strip()
            if not part:
                continue
            group_num = int(part)
            if group_num < 1 or group_num > 8:
                return None
            bitmask |= (1 << (group_num - 1))
        return bitmask
    except ValueError:
        return None


def retry_request(func):
    """
    Decorator to retry a function that makes HTTP requests to WLED devices.
    Uses the global retry_count variable.
    
    Args:
        func: Function that returns (success: bool, result: any)
    
    Returns:
        The result from the function after retries
    """
    def wrapper(*args, **kwargs):
        global retry_count
        attempts = retry_count + 1  # Total attempts = retries + 1 initial attempt
        
        for attempt in range(attempts):
            success, result = func(*args, **kwargs)
            if success:
                return success, result
            
            # If not successful and more attempts remain, wait briefly before retry
            if attempt < attempts - 1:
                time.sleep(0.1)  # Brief delay between retries
        
        # Return the last result after all attempts
        return success, result
    
    return wrapper


@retry_request
def set_sync_enabled(service, enabled, idx=None):
    """
    Enable or disable UDP sync for a device.
    
    Args:
        service: The service dict containing host_ip and port
        enabled: True to enable, False to disable
        idx: Optional device index for error reporting
    
    API Reference: https://kno.wled.ge/interfaces/json-api/
    udpn.send: Send WLED broadcast (UDP sync) packet on state change
    udpn.recv: Receive broadcast packets
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    prefix = f"{idx}. " if idx is not None else "  "
    try:
        response = requests.post(url, json={"udpn": {"send": enabled, "recv": enabled}}, timeout=2)
        if response.status_code == 200:
            service['sync_enabled'] = enabled  # Update cache
            status = "ON" if enabled else "OFF"
            print(f"{prefix}{service['friendly_name']}: sync {status}")
            return True, True
        else:
            print(f"{prefix}{service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False, False
    except Exception as e:
        print(f"{prefix}{service['friendly_name']}: Error - {e}")
        return False, False


@retry_request
def set_sync_groups(service, send_mask, recv_mask, idx=None):
    """
    Set WLED sync groups for a device.
    
    Args:
        service: The service dict containing host_ip and port
        send_mask: Bitmask for send groups (0-255)
        recv_mask: Bitmask for recv groups (0-255)
        idx: Optional device index for error reporting
    
    API Reference: https://kno.wled.ge/interfaces/json-api/
    udpn.sgrp: Bitfield for broadcast send groups 1-8 (0-255)
    udpn.rgrp: Bitfield for broadcast receive groups 1-8 (0-255)
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    prefix = f"{idx}. " if idx is not None else "  "
    try:
        response = requests.post(url, json={"udpn": {"sgrp": send_mask, "rgrp": recv_mask}}, timeout=2)
        if response.status_code == 200:
            service['sync_send'] = send_mask  # Update cache
            service['sync_recv'] = recv_mask  # Update cache
            print(f"{prefix}{service['friendly_name']}: send={send_mask}, recv={recv_mask}")
            return True, True
        else:
            print(f"{prefix}{service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False, False
    except Exception as e:
        print(f"{prefix}{service['friendly_name']}: Error - {e}")
        return False, False


@retry_request
def get_status(service, idx=None):
    """
    Get the full JSON status from a WLED device.
    
    Args:
        service: The service dict containing host_ip and port
        idx: Optional device index for error reporting
    
    Returns:
        Tuple of (success, JSON dict or None)
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    prefix = f"{idx}. " if idx is not None else "  "
    try:
        response = requests.get(url, timeout=2)
        if response.status_code == 200:
            return True, response.json()
        else:
            print(f"{prefix}{service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False, None
    except Exception as e:
        print(f"{prefix}{service['friendly_name']}: Error - {e}")
        return False, None


@retry_request
def get_info(service, idx=None):
    """
    Get the full JSON info from a WLED device (includes WiFi status, etc.).
    
    Args:
        service: The service dict containing host_ip and port
        idx: Optional device index for error reporting
    
    Returns:
        Tuple of (success, JSON dict or None)
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/info"
    prefix = f"{idx}. " if idx is not None else "  "
    try:
        response = requests.get(url, timeout=2)
        if response.status_code == 200:
            return True, response.json()
        else:
            print(f"{prefix}{service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False, None
    except Exception as e:
        print(f"{prefix}{service['friendly_name']}: Error - {e}")
        return False, None


def get_nested_field(data, field_path):
    """
    Extract a nested field from a dictionary/list using dot notation and bracket indexing.
    
    Args:
        data: The dictionary/list to extract from
        field_path: Path with dots and brackets (e.g., 'seg[0].bri', 'udpn.send')
    
    Returns:
        The value at the field path, or None if not found
    """
    import re
    
    # Split by dots, but keep bracket notation intact
    parts = field_path.split('.')
    current = data
    
    for part in parts:
        # Check if this part has bracket notation (e.g., 'seg[0]')
        match = re.match(r'^([^\[]+)\[(\d+)\]$', part)
        
        if match:
            # Handle array indexing: 'seg[0]'
            key = match.group(1)
            index = int(match.group(2))
            
            if isinstance(current, dict) and key in current:
                current = current[key]
                if isinstance(current, list) and 0 <= index < len(current):
                    current = current[index]
                else:
                    return None
            else:
                return None
        else:
            # Handle regular dictionary access
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
    
    return current


def display_json_data(idx, service, data, fields_str):
    """
    Display JSON data with optional field filtering and compact formatting.
    
    Args:
        idx: Device index
        service: Service dictionary
        data: JSON data to display
        fields_str: Comma-separated field list or None for full JSON
    """
    import json
    
    # Parse field list if provided
    fields = None
    if fields_str:
        fields = [f.strip() for f in fields_str.split(',')]
    
    if fields:
        # Display only requested fields
        # Check if we can fit everything on one line
        field_values = []
        for field in fields:
            value = get_nested_field(data, field)
            field_values.append((field, value))
        
        # If single field or all simple values, use compact format
        all_simple = all(not isinstance(v, (dict, list)) for _, v in field_values)
        if all_simple:
            # Single line format: idx. name: field1=value1, field2=value2
            value_strs = [f"{field}={json.dumps(value)}" for field, value in field_values]
            print(f"{idx}. {service['friendly_name']}: {', '.join(value_strs)}")
        else:
            # Multi-line format for complex values
            print(f"{idx}. {service['friendly_name']}:")
            for field, value in field_values:
                print(f"  {field}: {json.dumps(value)}")
    else:
        # Display full JSON with header
        print(f"{idx}. {service['friendly_name']}:")
        print(json.dumps(data, indent=2))


@retry_request
def set_power(service, state, idx=None):
    """
    Turn a WLED device on or off and update cached state.
    
    Args:
        service: The service dict containing host_ip and port
        state: True for on, False for off
        idx: Optional device index for error reporting
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    prefix = f"{idx}. " if idx is not None else "  "
    try:
        response = requests.post(url, json={"on": state}, timeout=2)
        if response.status_code == 200:
            service['power_state'] = state  # Update cache
            status = "ON" if state else "OFF"
            print(f"{prefix}{service['friendly_name']}: {status}")
            return True, True
        else:
            print(f"{prefix}{service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False, False
    except Exception as e:
        print(f"{prefix}{service['friendly_name']}: Error - {e}")
        return False, False


@retry_request
def reboot_device(service, idx=None):
    """
    Reboot a WLED device.
    
    Args:
        service: The service dict containing host_ip and port
        idx: Optional device index for error reporting
    
    API Reference: https://kno.wled.ge/interfaces/json-api/
    rb: Reboot the device
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    prefix = f"{idx}. " if idx is not None else "  "
    try:
        response = requests.post(url, json={"rb": True}, timeout=2)
        if response.status_code == 200:
            print(f"{prefix}{service['friendly_name']}: Rebooting")
            return True, True
        else:
            print(f"{prefix}{service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False, False
    except Exception as e:
        print(f"{prefix}{service['friendly_name']}: Error - {e}")
        return False, False


def parse_range(range_str, max_index, services_list=None):
    """
    Parse a range string like '3', '1-5', '0,2-4,7', 'all', or group names into a list of indices.
    
    Args:
        range_str: String like '3', '1-5', '0,2-4,7', 'all', or group name (comma-separated, can mix)
        max_index: Maximum valid index (exclusive)
        services_list: Optional list of services for group name resolution
    
    Returns:
        List of valid indices (deduplicated and sorted), or None if invalid
    """
    # Handle 'all' keyword
    if range_str.strip().lower() == 'all':
        return list(range(max_index))
    
    try:
        indices = []
        
        # Split by commas to handle comma-separated ranges/singles/groups
        parts = range_str.split(',')
        
        for part in parts:
            part = part.strip()
            if '-' in part and part[0].isdigit():
                # Handle numeric range like '1-5'
                range_parts = part.split('-')
                if len(range_parts) != 2:
                    return None
                start = int(range_parts[0])
                end = int(range_parts[1])
                if start < 0 or end >= max_index or start > end:
                    return None
                indices.extend(range(start, end + 1))
            elif part.isdigit():
                # Handle single numeric index like '3'
                index = int(part)
                if index < 0 or index >= max_index:
                    return None
                indices.append(index)
            elif services_list is not None and part.replace('_', '').isalnum():
                # Handle group name (alphanumeric with underscores)
                group_name = part.lower()
                group_indices = [i for i, s in enumerate(services_list) if s['group'].lower() == group_name]
                if not group_indices:
                    # Group doesn't exist - this is an error
                    return None
                indices.extend(group_indices)
            else:
                # Invalid format
                return None
        
        # Remove duplicates and sort
        return sorted(set(indices))
    except ValueError:
        return None


def clear_screen():
    """Clear the terminal screen."""
    os.system('clear' if os.name != 'nt' else 'cls')


def id_mode(services_list, indices):
    """
    Enter identification mode: turn off all specified devices, then cycle through them
    one by one with n(ext), p(rev), e(xit) commands.
    
    Args:
        services_list: List of all services
        indices: List of indices to cycle through
    """
    if not indices:
        return
    
    # Turn off all devices in the range
    print("Turning off all devices in range...")
    for idx in indices:
        set_power(services_list[idx], False)
    
    # Start enumeration
    current_pos = 0
    current_idx = indices[current_pos]
    set_power(services_list[current_idx], True)
    
    print("\n--- ID Mode ---")
    print("Commands: n(ext), p(rev), e(xit)")
    
    while True:
        service = services_list[current_idx]
        print(f"\nCurrent: {current_idx}. {service['friendly_name']}")
        
        cmd = input("[id]> ").strip().lower()
        
        if cmd == 'e' or cmd == 'exit':
            # Turn off current device before exiting
            set_power(services_list[current_idx], False)
            print("Exiting ID mode.")
            break
        
        elif cmd == 'n' or cmd == 'next':
            # Turn off current
            set_power(services_list[current_idx], False)
            # Move to next (wrap around)
            current_pos = (current_pos + 1) % len(indices)
            current_idx = indices[current_pos]
            # Turn on next
            set_power(services_list[current_idx], True)
        
        elif cmd == 'p' or cmd == 'prev':
            # Turn off current
            set_power(services_list[current_idx], False)
            # Move to previous (wrap around)
            current_pos = (current_pos - 1) % len(indices)
            current_idx = indices[current_pos]
            # Turn on previous
            set_power(services_list[current_idx], True)
        
        else:
            print("Unknown command. Use n(ext), p(rev), or e(xit).")


def command_loop():
    """
    Main command loop for interacting with WLED devices.
    """
    global last_command, last_failed_indices

    services_list = []
    
    print("WLED Browser - Command Interface")
    print("Type 'help' for available commands\n")
    
    # Initial scan
    services_list = scan_wled_devices()
    clear_screen()
    display_services(services_list)
    
    while True:
        try:
            command = input("\n> ").strip()
            
            if not command:
                clear_screen()
                display_services(services_list)
                continue
            
            parts = command.split(maxsplit=1)
            cmd = parts[0].lower()
            
            if cmd == 'help':
                print("\nWLED Browser - Command Reference")
                print("=" * 50)
                print("\nPower Control:")
                print("  on <range>       : Turn on device(s)")
                print("  off <range>      : Turn off device(s)")
                print("  reboot <range>   : Reboot device(s)")
                print()
                print("Sync Control:")
                print("  sync <range> {on|off}")
                print("                   : Enable/disable UDP sync (send & recv)")
                print("  syncgroups <range> send <groups> recv <groups>")
                print("                   : Set sync group membership")
                print("                     Example: syncgroups 0-2 send 1,3 recv 2")
                print()
                print("Device Management:")
                print("  id <range>       : Identify devices one-by-one")
                print("                     (n=next, p=prev, e=exit)")
                print("  power <range>    : Refresh power state display")
                print("  state <range> [fields]")
                print("                   : Get and display device state (JSON)")
                print("                     Optional fields: CSV list with dot/bracket notation")
                print("                     Example: state 0 on,bri,seg[0].bri,udpn.send")
                print("  info <range> [fields]")
                print("                   : Get and display device info (JSON)")
                print("                     Includes WiFi status, version, etc.")
                print("                     Optional fields: CSV list with dot/bracket notation")
                print("                     Example: info 0 wifi.rssi,ver,name")
                print("  group <range> <groupid>")
                print("                   : Assign devices to a group")
                print("                     Devices not in range with same groupid -> _default")
                print("                     Exception: _default is additive only")
                print("  ui <nn>          : Launch WLED UI in browser")
                print("  scan [seconds]   : Rescan network for WLED devices")
                print("                     Default: 10 seconds")
                print("  list             : Refresh device list display")
                print()
                print("General:")
                print("  retries <n>      : Set number of retries for device commands")
                print("                     Default: 0 (no retries)")
                print("  retry            : Retry previous command on failed devices only")
                print("  help             : Show this help message")
                print("  quit / exit      : Exit the program")
                print()
                print("Range Syntax:")
                print("  <nn>             : Single device (e.g., 0)")
                print("  <nn>-<mm>        : Range of devices (e.g., 1-3)")
                print("  <nn>,<mm>,...    : Multiple devices/ranges (e.g., 0,2-4,7)")
                print("  <groupid>        : Group name (e.g., living_room)")
                print("  all              : All devices")
                print("                     Note: Can mix syntax (e.g., 0,living_room,5)")
                print()
                print("Sync Groups:")
                print("  1-8              : Group numbers (e.g., 1,3,5)")
                print("  none or blank    : No groups")
                print("=" * 50)
            
            elif cmd == 'scan':
                scan_time = 10  # Default
                if len(parts) > 1:
                    try:
                        scan_time = int(parts[1])
                        if scan_time < 1:
                            print("Scan time must be at least 1 second.")
                            continue
                    except ValueError:
                        print("Invalid scan time. Using default of 10 seconds.")
                        scan_time = 10
                
                # Clear retry state after scan (indices change)
                global last_command, last_failed_indices
                
                services_list = scan_wled_devices(scan_time, services_list)
                clear_screen()
                display_services(services_list)
                
                last_command = None
                last_failed_indices = []
            
            elif cmd == 'list':
                # Clear retry state (not a device command)
                clear_screen()
                display_services(services_list)
                
                last_command = None
                last_failed_indices = []
            
            elif cmd in ['quit', 'exit', 'q']:
                print("Exiting.")
                break
            
            elif cmd == 'on' or cmd == 'off':
                if len(parts) < 2:
                    print(f"Usage: {cmd} <nn>[-<mm>]")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                indices = parse_range(parts[1], len(services_list), services_list)
                if indices is None:
                    print(f"Invalid range or group. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                last_command = command
                last_failed_indices = []
                
                state = (cmd == 'on')
                for idx in indices:
                    success, _ = set_power(services_list[idx], state, idx)
                    if not success:
                        last_failed_indices.append(idx)
                
                # If no failures, clear retry state
                if not last_failed_indices:
                    last_command = None
                
                clear_screen()
                display_services(services_list)
            
            elif cmd == 'reboot':
                if len(parts) < 2:
                    print("Usage: reboot <nn>[-<mm>]")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                indices = parse_range(parts[1], len(services_list), services_list)
                if indices is None:
                    print(f"Invalid range or group. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                last_command = command
                last_failed_indices = []
                
                print("Rebooting devices...")
                for idx in indices:
                    success, _ = reboot_device(services_list[idx], idx)
                    if not success:
                        last_failed_indices.append(idx)
                
                # If no failures, clear retry state
                if not last_failed_indices:
                    last_command = None
                
                print("Note: Devices will be offline for ~10 seconds during reboot.")
            
            elif cmd == 'id':
                if len(parts) < 2:
                    print("Usage: id <nn>[-<mm>]")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                indices = parse_range(parts[1], len(services_list), services_list)
                if indices is None:
                    print(f"Invalid range or group. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                # Clear retry state (not a retryable command)
                id_mode(services_list, indices)
                clear_screen()
                display_services(services_list)
                
                last_command = None
                last_failed_indices = []
            
            elif cmd == 'sync':
                if len(parts) < 2:
                    print("Usage: sync <nn>[-<mm>] {on|off}")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                # Parse: sync <range> {on|off}
                sync_parts = parts[1].split()
                if len(sync_parts) < 2:
                    print("Usage: sync <nn>[-<mm>] {on|off}")
                    continue
                
                range_spec = sync_parts[0]
                on_off = sync_parts[1].lower()
                
                if on_off not in ['on', 'off']:
                    print("Usage: sync <nn>[-<mm>] {on|off}")
                    continue
                
                indices = parse_range(range_spec, len(services_list), services_list)
                if indices is None:
                    print(f"Invalid range or group. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                last_command = command
                last_failed_indices = []
                
                enabled = (on_off == 'on')
                for idx in indices:
                    success, _ = set_sync_enabled(services_list[idx], enabled, idx)
                    if not success:
                        last_failed_indices.append(idx)
                
                # If no failures, clear retry state
                if not last_failed_indices:
                    last_command = None
                
                clear_screen()
                display_services(services_list)
            
            elif cmd == 'syncgroups':
                if len(parts) < 2:
                    print("Usage: syncgroups <nn>[-<mm>] send <groups> recv <groups>")
                    print("Example: syncgroups 0-2 send 1,3 recv 2")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                # Parse: syncgroups <range> send <groups> recv <groups>
                sync_parts = parts[1].split()
                if len(sync_parts) < 4 or sync_parts[1].lower() != 'send' or sync_parts[3].lower() != 'recv':
                    print("Usage: syncgroups <nn>[-<mm>] send <groups> recv <groups>")
                    print("Example: syncgroups 0-2 send 1,3 recv 2")
                    continue
                
                range_spec = sync_parts[0]
                send_groups = sync_parts[2]
                recv_groups = sync_parts[4] if len(sync_parts) > 4 else ''
                
                indices = parse_range(range_spec, len(services_list), services_list)
                if indices is None:
                    print(f"Invalid range or group. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                send_mask = parse_sync_groups(send_groups)
                recv_mask = parse_sync_groups(recv_groups)
                
                if send_mask is None:
                    print(f"Invalid send groups: {send_groups}. Use format: 1,3,5 or none")
                    continue
                
                if recv_mask is None:
                    print(f"Invalid recv groups: {recv_groups}. Use format: 1,3,5 or none")
                    continue
                
                last_command = command
                last_failed_indices = []
                
                for idx in indices:
                    success, _ = set_sync_groups(services_list[idx], send_mask, recv_mask, idx)
                    if not success:
                        last_failed_indices.append(idx)
                
                # If no failures, clear retry state
                if not last_failed_indices:
                    last_command = None
                
                clear_screen()
                display_services(services_list)
            
            elif cmd == 'power':
                if len(parts) < 2:
                    print("Usage: power <nn>[-<mm>]")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                indices = parse_range(parts[1], len(services_list), services_list)
                if indices is None:
                    print(f"Invalid range or group. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                last_command = command
                last_failed_indices = []
                
                # Refresh power state from devices
                for idx in indices:
                    service = services_list[idx]
                    success, status = get_status(service, idx)
                    if success and status:
                        service['power_state'] = status.get('on', None)
                    elif not success:
                        last_failed_indices.append(idx)
                
                # If no failures, clear retry state
                if not last_failed_indices:
                    last_command = None
                
                clear_screen()
                display_services(services_list)
            
            elif cmd == 'state':
                if len(parts) < 2:
                    print("Usage: state <nn>[-<mm>] [fields]")
                    print("Example: state 0 on,bri,udpn.send")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                # Parse: state <range> [fields]
                info_parts = parts[1].split(maxsplit=1)
                range_spec = info_parts[0]
                fields_str = info_parts[1] if len(info_parts) > 1 else None
                
                indices = parse_range(range_spec, len(services_list), services_list)
                if indices is None:
                    print(f"Invalid range or group. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                last_command = command
                last_failed_indices = []
                
                for idx in indices:
                    service = services_list[idx]
                    success, status = get_status(service, idx)
                    if success and status:
                        display_json_data(idx, service, status, fields_str)
                    elif not success:
                        last_failed_indices.append(idx)
                
                # If no failures, clear retry state
                if not last_failed_indices:
                    last_command = None
            
            elif cmd == 'info':
                if len(parts) < 2:
                    print("Usage: info <nn>[-<mm>] [fields]")
                    print("Example: info 0 wifi.rssi,ver,name")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                # Parse: info <range> [fields]
                info_parts = parts[1].split(maxsplit=1)
                range_spec = info_parts[0]
                fields_str = info_parts[1] if len(info_parts) > 1 else None
                
                indices = parse_range(range_spec, len(services_list), services_list)
                if indices is None:
                    print(f"Invalid range or group. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                last_command = command
                last_failed_indices = []
                
                for idx in indices:
                    service = services_list[idx]
                    success, info_data = get_info(service, idx)
                    if success and info_data:
                        display_json_data(idx, service, info_data, fields_str)
                    elif not success:
                        last_failed_indices.append(idx)
                
                # If no failures, clear retry state
                if not last_failed_indices:
                    last_command = None
            
            elif cmd == 'group':
                if len(parts) < 2:
                    print("Usage: group <range> <groupid>")
                    print("Example: group 0-2 living_room")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                # Parse: group <range> <groupid>
                group_parts = parts[1].split(maxsplit=1)
                if len(group_parts) < 2:
                    print("Usage: group <range> <groupid>")
                    print("Example: group 0-2 living_room")
                    continue
                
                range_spec = group_parts[0]
                group_id = group_parts[1].strip()
                
                # Validate group_id is alphanumeric (with underscores)
                if not group_id.replace('_', '').isalnum():
                    print("Group ID must be alphanumeric (underscores allowed).")
                    continue
                
                # Parse range - DON'T pass services_list since we can't use group names in group command
                indices = parse_range(range_spec, len(services_list), None)
                if indices is None:
                    print(f"Invalid range. Valid indices: 0-{len(services_list)-1}")
                    print("Note: Group names cannot be used in the group command.")
                    continue
                
                group_id_lower = group_id.lower()
                
                # Clear retry state (not a retryable command)
                # Apply grouping logic:
                # (a) Set group for devices in range
                for idx in indices:
                    services_list[idx]['group'] = group_id
                
                # (b) Reset devices NOT in range but with same group_id to _default
                #     EXCEPT when group_id is '_default' (additive mode)
                if group_id_lower != '_default':
                    for idx, service in enumerate(services_list):
                        if idx not in indices and service['group'].lower() == group_id_lower:
                            service['group'] = '_default'
                
                # Re-sort the list by group and name
                services_list.sort(key=lambda s: (s['group'] != '_default', s['group'].lower(), s['friendly_name'].lower()))
                
                clear_screen()
                display_services(services_list)
                
                last_command = None
                last_failed_indices = []
            
            elif cmd == 'retries':
                global retry_count
                
                if len(parts) < 2:
                    print(f"Current retry count: {retry_count}")
                    print("Usage: retries <n>")
                    print("Example: retries 3")
                    continue
                
                try:
                    new_count = int(parts[1])
                    if new_count < 0:
                        print("Retry count must be non-negative.")
                        continue
                    retry_count = new_count
                    print(f"Retry count set to {retry_count}")
                    
                    # Clear retry state (not a retryable command)
                    last_command = None
                    last_failed_indices = []
                except ValueError:
                    print("Invalid retry count. Please enter a number.")
            
            elif cmd == 'retry':
                if not last_command:
                    print("No previous command to retry.")
                    continue
                
                if not last_failed_indices:
                    print("No failures in previous command.")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                # Build a new command with failed indices
                indices_str = ','.join(str(idx) for idx in sorted(last_failed_indices))
                
                # Parse original command to extract base command
                orig_parts = last_command.split(maxsplit=1)
                orig_cmd = orig_parts[0].lower()
                
                # Reconstruct command with failed indices only
                if orig_cmd in ['on', 'off', 'reboot', 'power']:
                    new_command = f"{orig_cmd} {indices_str}"
                elif orig_cmd == 'sync':
                    # Extract on/off parameter
                    sync_parts = orig_parts[1].split()
                    if len(sync_parts) >= 2:
                        new_command = f"sync {indices_str} {sync_parts[1]}"
                    else:
                        print("Cannot reconstruct sync command.")
                        continue
                elif orig_cmd == 'syncgroups':
                    # Extract send/recv parameters
                    sync_parts = orig_parts[1].split()
                    if len(sync_parts) >= 4:
                        # Format: syncgroups <range> send <groups> recv <groups>
                        send_idx = sync_parts.index('send') if 'send' in sync_parts else -1
                        recv_idx = sync_parts.index('recv') if 'recv' in sync_parts else -1
                        if send_idx > 0 and recv_idx > send_idx:
                            send_groups = sync_parts[send_idx + 1]
                            recv_groups = sync_parts[recv_idx + 1] if recv_idx + 1 < len(sync_parts) else ''
                            new_command = f"syncgroups {indices_str} send {send_groups} recv {recv_groups}"
                        else:
                            print("Cannot reconstruct syncgroups command.")
                            continue
                    else:
                        print("Cannot reconstruct syncgroups command.")
                        continue
                elif orig_cmd == 'state':
                    # Extract optional fields parameter
                    state_parts = orig_parts[1].split(maxsplit=1)
                    if len(state_parts) > 1:
                        new_command = f"state {indices_str} {state_parts[1]}"
                    else:
                        new_command = f"state {indices_str}"
                elif orig_cmd == 'info':
                    # Extract optional fields parameter
                    info_parts = orig_parts[1].split(maxsplit=1)
                    if len(info_parts) > 1:
                        new_command = f"info {indices_str} {info_parts[1]}"
                    else:
                        new_command = f"info {indices_str}"
                else:
                    print(f"Command '{orig_cmd}' cannot be retried.")
                    continue
                
                print(f"Retrying: {new_command}")
                # Re-inject the command by setting 'command' variable
                # and jumping back to command processing
                command = new_command
                parts = command.split(maxsplit=1)
                cmd = parts[0].lower()
                
                # Now process it - this is a bit hacky but works
                # We need to continue processing from the top of the command handling
                # For now, let's just recursively call the handler based on cmd
                if cmd == 'on' or cmd == 'off':
                    if len(parts) < 2:
                        continue
                    indices = parse_range(parts[1], len(services_list), services_list)
                    if indices is None:
                        continue
                    last_command = command
                    last_failed_indices = []
                    state = (cmd == 'on')
                    for idx in indices:
                        success, _ = set_power(services_list[idx], state, idx)
                        if not success:
                            last_failed_indices.append(idx)
                    clear_screen()
                    display_services(services_list)
                elif cmd == 'reboot':
                    if len(parts) < 2:
                        continue
                    indices = parse_range(parts[1], len(services_list), services_list)
                    if indices is None:
                        continue
                    last_command = command
                    last_failed_indices = []
                    print("Rebooting devices...")
                    for idx in indices:
                        success, _ = reboot_device(services_list[idx], idx)
                        if not success:
                            last_failed_indices.append(idx)
                    print("Note: Devices will be offline for ~10 seconds during reboot.")
                elif cmd == 'sync':
                    sync_parts = parts[1].split()
                    if len(sync_parts) < 2:
                        continue
                    range_spec = sync_parts[0]
                    on_off = sync_parts[1].lower()
                    if on_off not in ['on', 'off']:
                        continue
                    indices = parse_range(range_spec, len(services_list), services_list)
                    if indices is None:
                        continue
                    last_command = command
                    last_failed_indices = []
                    enabled = (on_off == 'on')
                    for idx in indices:
                        success, _ = set_sync_enabled(services_list[idx], enabled, idx)
                        if not success:
                            last_failed_indices.append(idx)
                    clear_screen()
                    display_services(services_list)
                elif cmd == 'syncgroups':
                    sync_parts = parts[1].split()
                    if len(sync_parts) < 4:
                        continue
                    range_spec = sync_parts[0]
                    send_groups = sync_parts[2]
                    recv_groups = sync_parts[4] if len(sync_parts) > 4 else ''
                    indices = parse_range(range_spec, len(services_list), services_list)
                    if indices is None:
                        continue
                    send_mask = parse_sync_groups(send_groups)
                    recv_mask = parse_sync_groups(recv_groups)
                    if send_mask is None or recv_mask is None:
                        continue
                    last_command = command
                    last_failed_indices = []
                    for idx in indices:
                        success, _ = set_sync_groups(services_list[idx], send_mask, recv_mask, idx)
                        if not success:
                            last_failed_indices.append(idx)
                    clear_screen()
                    display_services(services_list)
                elif cmd == 'power':
                    if len(parts) < 2:
                        continue
                    indices = parse_range(parts[1], len(services_list), services_list)
                    if indices is None:
                        continue
                    last_command = command
                    last_failed_indices = []
                    for idx in indices:
                        service = services_list[idx]
                        success, status = get_status(service, idx)
                        if success and status:
                            service['power_state'] = status.get('on', None)
                        elif not success:
                            last_failed_indices.append(idx)
                    clear_screen()
                    display_services(services_list)
                elif cmd == 'state':
                    info_parts = parts[1].split(maxsplit=1)
                    range_spec = info_parts[0]
                    fields_str = info_parts[1] if len(info_parts) > 1 else None
                    indices = parse_range(range_spec, len(services_list), services_list)
                    if indices is None:
                        continue
                    last_command = command
                    last_failed_indices = []
                    for idx in indices:
                        service = services_list[idx]
                        success, status = get_status(service, idx)
                        if success and status:
                            display_json_data(idx, service, status, fields_str)
                        elif not success:
                            last_failed_indices.append(idx)
                elif cmd == 'info':
                    info_parts = parts[1].split(maxsplit=1)
                    range_spec = info_parts[0]
                    fields_str = info_parts[1] if len(info_parts) > 1 else None
                    indices = parse_range(range_spec, len(services_list), services_list)
                    if indices is None:
                        continue
                    last_command = command
                    last_failed_indices = []
                    for idx in indices:
                        service = services_list[idx]
                        success, info_data = get_info(service, idx)
                        if success and info_data:
                            display_json_data(idx, service, info_data, fields_str)
                        elif not success:
                            last_failed_indices.append(idx)
            
            elif cmd == 'ui':
                # Clear retry state (not a retryable command)
                if len(parts) < 2:
                    print("Usage: ui <nn>")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                try:
                    index = int(parts[1])
                    if index < 0 or index >= len(services_list):
                        print(f"Invalid index. Valid indices: 0-{len(services_list)-1}")
                        continue
                    
                    service = services_list[index]
                    url = f"http://{service['host_ip']}:{service['port']}"
                    print(f"Launching {url} in browser...")
                    webbrowser.open_new_tab(url)
                    
                    last_command = None
                    last_failed_indices = []
                except ValueError:
                    print("Invalid index. Please enter a number.")
            
            else:
                print(f"Unknown command: {cmd}. Type 'help' for available commands.")
        
        except KeyboardInterrupt:
            print("\nUse 'quit' or 'exit' to leave the program.")
        except EOFError:
            print("\nExiting.")
            break


if __name__ == "__main__":
    command_loop()

