import socket
import time
import webbrowser
import requests
import os
import readline  # Enables command-line editing (arrow keys, history, etc.)
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

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
                'power_state': None,  # Cache for power state
                'sync_enabled': None, # Cache for UDP sync on/off
                'sync_send': None,    # Cache for sync send groups (bitmask)
                'sync_recv': None     # Cache for sync recv groups (bitmask)
            }

    # update_service is called when a service's info changes; treat same as add
    update_service = add_service 


def scan_wled_devices(discovery_time_seconds=10):
    """
    Scan for WLED devices on the network.
    
    Args:
        discovery_time_seconds: Time to scan for devices (default: 10)
    
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

    services_list = list(listener.services.values())
    # Sort by friendly_name (instance name) as default
    services_list.sort(key=lambda s: s['friendly_name'].lower())
    return services_list


def display_services(services_list):
    """
    Display the list of discovered WLED services with cached power state.
    """
    if not services_list:
        print("No WLED services found.")
        return
    
    print("\n--- Discovered WLED Hosts ---")
    for i, service in enumerate(services_list):
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


def set_sync_enabled(service, enabled):
    """
    Enable or disable UDP sync for a device.
    
    Args:
        service: The service dict containing host_ip and port
        enabled: True to enable, False to disable
    
    API Reference: https://kno.wled.ge/interfaces/json-api/
    udpn.send: Send WLED broadcast (UDP sync) packet on state change
    udpn.recv: Receive broadcast packets
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    try:
        response = requests.post(url, json={"udpn": {"send": enabled, "recv": enabled}}, timeout=2)
        if response.status_code == 200:
            service['sync_enabled'] = enabled  # Update cache
            status = "ON" if enabled else "OFF"
            print(f"  {service['friendly_name']}: sync {status}")
            return True
        else:
            print(f"  {service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False
    except Exception as e:
        print(f"  {service['friendly_name']}: Error - {e}")
        return False


def set_sync_groups(service, send_mask, recv_mask):
    """
    Set WLED sync groups for a device.
    
    Args:
        service: The service dict containing host_ip and port
        send_mask: Bitmask for send groups (0-255)
        recv_mask: Bitmask for recv groups (0-255)
    
    API Reference: https://kno.wled.ge/interfaces/json-api/
    udpn.sgrp: Bitfield for broadcast send groups 1-8 (0-255)
    udpn.rgrp: Bitfield for broadcast receive groups 1-8 (0-255)
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    try:
        response = requests.post(url, json={"udpn": {"sgrp": send_mask, "rgrp": recv_mask}}, timeout=2)
        if response.status_code == 200:
            service['sync_send'] = send_mask  # Update cache
            service['sync_recv'] = recv_mask  # Update cache
            print(f"  {service['friendly_name']}: send={send_mask}, recv={recv_mask}")
            return True
        else:
            print(f"  {service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False
    except Exception as e:
        print(f"  {service['friendly_name']}: Error - {e}")
        return False


def get_status(service):
    """
    Get the full JSON status from a WLED device.
    
    Args:
        service: The service dict containing host_ip and port
    
    Returns:
        JSON dict if successful, None otherwise
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    try:
        response = requests.get(url, timeout=2)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"  {service['friendly_name']}: Failed (HTTP {response.status_code})")
            return None
    except Exception as e:
        print(f"  {service['friendly_name']}: Error - {e}")
        return None


def get_nested_field(data, field_path):
    """
    Extract a nested field from a dictionary using dot notation.
    
    Args:
        data: The dictionary to extract from
        field_path: Dot-separated path (e.g., 'udpn.send')
    
    Returns:
        The value at the field path, or None if not found
    """
    parts = field_path.split('.')
    current = data
    
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    
    return current


def set_power(service, state):
    """
    Turn a WLED device on or off and update cached state.
    
    Args:
        service: The service dict containing host_ip and port
        state: True for on, False for off
    """
    url = f"http://{service['host_ip']}:{service['port']}/json/state"
    try:
        response = requests.post(url, json={"on": state}, timeout=2)
        if response.status_code == 200:
            service['power_state'] = state  # Update cache
            status = "ON" if state else "OFF"
            print(f"  {service['friendly_name']}: {status}")
            return True
        else:
            print(f"  {service['friendly_name']}: Failed (HTTP {response.status_code})")
            return False
    except Exception as e:
        print(f"  {service['friendly_name']}: Error - {e}")
        return False


def parse_range(range_str, max_index):
    """
    Parse a range string like '3', '1-5', '0,2-4,7', or 'all' into a list of indices.
    
    Args:
        range_str: String like '3', '1-5', '0,2-4,7', or 'all' (comma-separated ranges/singles)
        max_index: Maximum valid index (exclusive)
    
    Returns:
        List of valid indices (deduplicated and sorted), or None if invalid
    """
    # Handle 'all' keyword
    if range_str.strip().lower() == 'all':
        return list(range(max_index))
    
    try:
        indices = []
        
        # Split by commas to handle comma-separated ranges/singles
        parts = range_str.split(',')
        
        for part in parts:
            part = part.strip()
            if '-' in part:
                # Handle range like '1-5'
                range_parts = part.split('-')
                if len(range_parts) != 2:
                    return None
                start = int(range_parts[0])
                end = int(range_parts[1])
                if start < 0 or end >= max_index or start > end:
                    return None
                indices.extend(range(start, end + 1))
            else:
                # Handle single index like '3'
                index = int(part)
                if index < 0 or index >= max_index:
                    return None
                indices.append(index)
        
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
                print("  status <range>   : Refresh power state display")
                print("  info <range> [fields]")
                print("                   : Get and display JSON status")
                print("                     Optional fields: CSV list with dot notation")
                print("                     Example: info 0 on,bri,udpn.send")
                print("  ui <nn>          : Launch WLED UI in browser")
                print("  scan [seconds]   : Rescan network for WLED devices")
                print("                     Default: 10 seconds")
                print("  list             : Refresh device list display")
                print()
                print("General:")
                print("  help             : Show this help message")
                print("  quit / exit      : Exit the program")
                print()
                print("Range Syntax:")
                print("  <nn>             : Single device (e.g., 0)")
                print("  <nn>-<mm>        : Range of devices (e.g., 1-3)")
                print("  <nn>,<mm>,...    : Multiple devices/ranges (e.g., 0,2-4,7)")
                print("  all              : All devices")
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
                
                services_list = scan_wled_devices(scan_time)
                clear_screen()
                display_services(services_list)
            
            elif cmd == 'list':
                clear_screen()
                display_services(services_list)
            
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
                
                indices = parse_range(parts[1], len(services_list))
                if indices is None:
                    print(f"Invalid range. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                state = (cmd == 'on')
                for idx in indices:
                    set_power(services_list[idx], state)
                
                clear_screen()
                display_services(services_list)
            
            elif cmd == 'id':
                if len(parts) < 2:
                    print("Usage: id <nn>[-<mm>]")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                indices = parse_range(parts[1], len(services_list))
                if indices is None:
                    print(f"Invalid range. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                id_mode(services_list, indices)
                clear_screen()
                display_services(services_list)
            
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
                
                indices = parse_range(range_spec, len(services_list))
                if indices is None:
                    print(f"Invalid range. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                enabled = (on_off == 'on')
                for idx in indices:
                    set_sync_enabled(services_list[idx], enabled)
                
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
                
                indices = parse_range(range_spec, len(services_list))
                if indices is None:
                    print(f"Invalid range. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                send_mask = parse_sync_groups(send_groups)
                recv_mask = parse_sync_groups(recv_groups)
                
                if send_mask is None:
                    print(f"Invalid send groups: {send_groups}. Use format: 1,3,5 or none")
                    continue
                
                if recv_mask is None:
                    print(f"Invalid recv groups: {recv_groups}. Use format: 1,3,5 or none")
                    continue
                
                for idx in indices:
                    set_sync_groups(services_list[idx], send_mask, recv_mask)
                
                clear_screen()
                display_services(services_list)
            
            elif cmd == 'status':
                if len(parts) < 2:
                    print("Usage: status <nn>[-<mm>]")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                indices = parse_range(parts[1], len(services_list))
                if indices is None:
                    print(f"Invalid range. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                # Refresh power state from devices
                for idx in indices:
                    service = services_list[idx]
                    status = get_status(service)
                    if status:
                        service['power_state'] = status.get('on', None)
                
                clear_screen()
                display_services(services_list)
            
            elif cmd == 'info':
                if len(parts) < 2:
                    print("Usage: info <nn>[-<mm>] [fields]")
                    print("Example: info 0 on,bri,udpn.send")
                    continue
                
                if not services_list:
                    print("No devices found. Run 'scan' first.")
                    continue
                
                # Parse: info <range> [fields]
                info_parts = parts[1].split(maxsplit=1)
                range_spec = info_parts[0]
                fields_str = info_parts[1] if len(info_parts) > 1 else None
                
                indices = parse_range(range_spec, len(services_list))
                if indices is None:
                    print(f"Invalid range. Valid indices: 0-{len(services_list)-1}")
                    continue
                
                import json
                
                # Parse field list if provided
                fields = None
                if fields_str:
                    fields = [f.strip() for f in fields_str.split(',')]
                
                for idx in indices:
                    service = services_list[idx]
                    print(f"\n--- Info for {idx}. {service['friendly_name']} ---")
                    status = get_status(service)
                    if status:
                        if fields:
                            # Display only requested fields
                            for field in fields:
                                value = get_nested_field(status, field)
                                print(f"  {field}: {json.dumps(value)}")
                        else:
                            # Display full JSON
                            print(json.dumps(status, indent=2))
            
            elif cmd == 'ui':
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

