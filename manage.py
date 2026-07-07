#!/usr/bin/env python3
"""
MikroTik Configuration Manager – Interactive Menu
==================================================
- Establish connection via direct API or SSH/MAC‑Telnet (same logic as main script).
- Interactive menu to view or edit any section:
  Identity, IP Addresses, Routes, Firewall, NAT, OSPF, Services, Tools.
- Edit mode sends commands directly to the router with real‑time responses.
"""

import sys
import time
import re
import paramiko
from routeros_api import RouterOsApiPool

# ---------- USER SETTINGS ----------
USERNAME = "admin"
PASSWORD = "admin"
API_PORT = 8728
# -----------------------------------

def api_connect(host, port=API_PORT):
    pool = RouterOsApiPool(host, username=USERNAME, password=PASSWORD,
                           port=port, plaintext_login=True, use_ssl=False)
    try:
        api = pool.get_api()
        api.get_resource("/system/identity").get()
        return pool, api
    except Exception:
        pool.disconnect()
        return None, None

def wait_and_capture(channel, targets, timeout=10.0):
    start = time.time(); buffer = ""
    while time.time() - start < timeout:
        time.sleep(0.1)
        if channel.recv_ready():
            chunk = channel.recv(4096).decode("utf-8", errors="ignore")
            buffer += chunk
            if any(t in buffer for t in targets): return True, buffer
    return False, buffer

def send_and_wait(channel, cmd, wait=1.0):
    """Send command, wait for prompt, return output."""
    channel.send(cmd + "\r")
    time.sleep(wait)
    output = ""
    while channel.recv_ready():
        output += channel.recv(4096).decode("utf-8", errors="ignore")
    success, more = wait_and_capture(channel, ["> ", "] >"], timeout=10.0)
    output += more
    # Clean up prompt from output for display
    lines = output.splitlines()
    clean_lines = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("[admin@") and not line.startswith("["):
            clean_lines.append(line)
    return "\n".join(clean_lines)

def ssh_connect(host, mac=None):
    """Connect via SSH, optionally MAC-Telnet to target."""
    print(f"\n  Connecting to {host} via SSH ...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, username=USERNAME, password=PASSWORD,
                    look_for_keys=False, allow_agent=False, timeout=10)
    except Exception as e:
        print(f"  SSH connection failed: {e}")
        return None, None

    channel = ssh.invoke_shell(term="vt100", width=120, height=24)
    time.sleep(2.0)
    wait_and_capture(channel, [">", "]"])
    print("  SSH session established.")

    if mac:
        print(f"  MAC-Telnet to {mac} ...")
        channel.send(f"/tool mac-telnet {mac}\r")
        success, output = wait_and_capture(channel, [
            "Login:", "User:", "Do you want to see the software license?"
        ], timeout=20)
        if not success:
            print("  Failed to reach target.")
            channel.close(); ssh.close()
            return None, None

        if "Do you want to see the software license?" in output:
            channel.send("n\r"); time.sleep(0.5)
            wait_and_capture(channel, ["new password>"], timeout=10)
            channel.send(f"{PASSWORD}\r"); time.sleep(0.5)
            wait_and_capture(channel, ["repeat new password>"], timeout=10)
            channel.send(f"{PASSWORD}\r"); time.sleep(1)
            wait_and_capture(channel, ["> "], timeout=10)
        elif "Login:" in output or "User:" in output:
            time.sleep(0.5)
            channel.send(f"{USERNAME}\r")
            wait_and_capture(channel, ["Password:"], timeout=5)
            time.sleep(0.5)
            channel.send(f"{PASSWORD}\r")
            time.sleep(1)
            success, landing = wait_and_capture(channel, ["] >", "failed", "incorrect"], timeout=15)
            if "failed" in landing or "incorrect" in landing:
                print("  Login failed.")
                channel.close(); ssh.close()
                return None, None
            if not success:
                print("  Login timed out.")
                channel.close(); ssh.close()
                return None, None
        time.sleep(2.0)
        channel.send("\r"); time.sleep(1.0)
        print("  Connected to target.")

    return channel, ssh

# ================================================================
# VIEW FUNCTIONS
# ================================================================

def view_identity(api=None, channel=None):
    if api:
        try:
            ident = api.get_resource("/system/identity").get()
            print(f"\n  Identity: {ident[0]['name']}")
        except: print("  (error)")
    elif channel:
        out = send_and_wait(channel, "/system identity print", wait=2)
        print(f"\n{out}")

def view_ip_addresses(api=None, channel=None):
    if api:
        print("\n  IP Addresses:")
        try:
            for addr in api.get_resource("/ip/address").get():
                print(f"    {addr.get('address','?')} on {addr.get('interface','?')} "
                      f"(network: {addr.get('network','?')})")
        except Exception as e: print(f"    Error: {e}")
    elif channel:
        out = send_and_wait(channel, "/ip address print", wait=2)
        print(f"\n{out}")

def view_routes(api=None, channel=None):
    if api:
        print("\n  Routes:")
        try:
            for route in api.get_resource("/ip/route").get():
                print(f"    dst={route.get('dst-address','?')} gateway={route.get('gateway','?')} "
                      f"distance={route.get('distance','?')} "
                      f"{'static' if route.get('static','') else 'dynamic'}")
        except Exception as e: print(f"    Error: {e}")
    elif channel:
        out = send_and_wait(channel, "/ip route print", wait=2)
        print(f"\n{out}")

def view_firewall(api=None, channel=None):
    if api:
        print("\n  Firewall Filter (INPUT):")
        try:
            for rule in api.get_resource("/ip/firewall/filter").get(chain="input"):
                print(f"    [{rule.get('.id','?')}] action={rule.get('action','')} "
                      f"src={rule.get('src-address','')} dst={rule.get('dst-address','')} "
                      f"proto={rule.get('protocol','')} port={rule.get('dst-port','')} "
                      f"in-if={rule.get('in-interface','')}")
        except Exception as e: print(f"    Error: {e}")
        print("\n  Firewall Filter (FORWARD):")
        try:
            for rule in api.get_resource("/ip/firewall/filter").get(chain="forward"):
                print(f"    [{rule.get('.id','?')}] action={rule.get('action','')} "
                      f"src={rule.get('src-address','')} dst={rule.get('dst-address','')} "
                      f"proto={rule.get('protocol','')} port={rule.get('dst-port','')}")
        except Exception as e: print(f"    Error: {e}")
    elif channel:
        print("\n  Firewall Filter (INPUT):")
        out = send_and_wait(channel, "/ip firewall filter print chain=input", wait=2)
        print(f"\n{out}")
        print("\n  Firewall Filter (FORWARD):")
        out = send_and_wait(channel, "/ip firewall filter print chain=forward", wait=2)
        print(f"\n{out}")

def view_nat(api=None, channel=None):
    if api:
        print("\n  NAT Rules:")
        try:
            for nat in api.get_resource("/ip/firewall/nat").get():
                print(f"    [{nat.get('.id','?')}] chain={nat.get('chain','')} action={nat.get('action','')} "
                      f"src={nat.get('src-address','')} dst={nat.get('dst-address','')} "
                      f"out-if={nat.get('out-interface','')}")
        except Exception as e: print(f"    Error: {e}")
    elif channel:
        out = send_and_wait(channel, "/ip firewall nat print", wait=2)
        print(f"\n{out}")

def view_ospf(api=None, channel=None):
    if api:
        print("\n  OSPF Instance:")
        try:
            inst = api.get_resource("/routing/ospf/instance").get()
            if inst: print(f"    {inst[0].get('name','')} router-id={inst[0].get('router-id','')} "
                           f"disabled={inst[0].get('disabled','')}")
        except: print("    (none)")
        print("  OSPF Area:")
        try:
            area = api.get_resource("/routing/ospf/area").get()
            if area: print(f"    {area[0].get('name','')} area-id={area[0].get('area-id','')}")
        except: print("    (none)")
        print("  OSPF Neighbors:")
        try:
            neighbors = api.get_resource("/routing/ospf/neighbor").get()
            if neighbors:
                for n in neighbors:
                    print(f"    router-id={n.get('router-id','?')} state={n.get('state','?')} "
                          f"address={n.get('address','?')} interface={n.get('interface','?')}")
            else: print("    (none)")
        except: pass
        print("  OSPF Interface Templates:")
        try:
            templates = api.get_resource("/routing/ospf/interface-template").get()
            if templates:
                for t in templates:
                    passive = " (passive)" if t.get("passive", "") == "yes" else ""
                    print(f"    interfaces={t.get('interfaces','')} area={t.get('area','')}{passive}")
            else: print("    (none)")
        except: pass
    elif channel:
        for cmd in ["/routing ospf instance print", "/routing ospf area print",
                     "/routing ospf neighbor print", "/routing ospf interface-template print"]:
            out = send_and_wait(channel, cmd, wait=2)
            print(f"\n{out}")

def view_services(api=None, channel=None):
    if api:
        print("\n  IP Services:")
        try:
            for svc in api.get_resource("/ip/service").get():
                print(f"    {svc.get('name','?')} port={svc.get('port','?')} "
                      f"disabled={svc.get('disabled','?')} address={svc.get('address','?')}")
        except Exception as e: print(f"    Error: {e}")
    elif channel:
        out = send_and_wait(channel, "/ip service print", wait=2)
        print(f"\n{out}")

def view_full_config(api=None, channel=None):
    """Display full configuration."""
    view_identity(api, channel)
    view_ip_addresses(api, channel)
    view_routes(api, channel)
    view_firewall(api, channel)
    view_nat(api, channel)
    view_ospf(api, channel)
    view_services(api, channel)

# ================================================================
# EDIT FUNCTION
# ================================================================

def edit_mode(api=None, channel=None):
    """Interactive edit mode."""
    if api:
        print("\n  Entering API edit mode. Type 'quit' to exit.")
        print("  Commands are RouterOS CLI commands (without leading /).\n")
        while True:
            cmd = input("  ROUTER> ").strip()
            if cmd.lower() == "quit":
                break
            if not cmd:
                continue
            # Prepend / if not present
            if not cmd.startswith("/"):
                cmd = "/" + cmd
            try:
                result = api.get_resource('/').call(cmd, {})
                for item in result:
                    print(f"    {item}")
            except Exception as e:
                print(f"    Error: {e}")

    elif channel:
        print("\n  Entering interactive edit mode. Type 'quit' to exit.")
        print("  Commands are sent directly to the router.\n")
        while True:
            cmd = input("  ROUTER> ").strip()
            if cmd.lower() == "quit":
                break
            if not cmd:
                continue
            out = send_and_wait(channel, cmd, wait=1)
            if out.strip():
                print(f"\n{out}")

# ================================================================
# TOOLS
# ================================================================

def tool_ping(api=None, channel=None):
    target = input("  Target IP to ping: ").strip()
    if not target:
        return
    cmd = f"/ping {target} count=4"
    if api:
        print(f"  Pinging {target} ...")
        try:
            result = api.get_resource('/').call(cmd, {})
            for item in result:
                print(f"    {item}")
        except Exception as e:
            print(f"    Error: {e}")
    elif channel:
        out = send_and_wait(channel, cmd, wait=6)
        print(f"\n{out}")

def tool_traceroute(api=None, channel=None):
    target = input("  Target IP to trace: ").strip()
    if not target:
        return
    cmd = f"/tool traceroute {target} count=1"
    if api:
        print(f"  Tracing {target} ...")
        try:
            result = api.get_resource('/').call(cmd, {})
            for item in result:
                print(f"    {item}")
        except Exception as e:
            print(f"    Error: {e}")
    elif channel:
        out = send_and_wait(channel, cmd, wait=10)
        print(f"\n{out}")

def tool_arp(api=None, channel=None):
    cmd = "/ip arp print"
    if api:
        try:
            for entry in api.get_resource("/ip/arp").get():
                print(f"    {entry.get('address','?')} -> {entry.get('mac-address','?')} "
                      f"on {entry.get('interface','?')}")
        except Exception as e: print(f"    Error: {e}")
    elif channel:
        out = send_and_wait(channel, cmd, wait=2)
        print(f"\n{out}")

def tool_neighbors(api=None, channel=None):
    cmd = "/ip neighbor print"
    if api:
        try:
            for n in api.get_resource("/ip/neighbor").get():
                print(f"    {n.get('identity','?')} ({n.get('platform','?')}) "
                      f"mac={n.get('mac-address','?')} on {n.get('interface','?')}")
        except Exception as e: print(f"    Error: {e}")
    elif channel:
        out = send_and_wait(channel, cmd, wait=2)
        print(f"\n{out}")

# ================================================================
# MAIN MENU
# ================================================================

def interactive_menu(api=None, channel=None):
    """Main interactive menu."""
    while True:
        print("\n" + "="*60)
        print("  MIKROTIK CONFIGURATION MANAGER")
        print("="*60)
        print("  VIEW:")
        print("    1. Full configuration")
        print("    2. Identity")
        print("    3. IP Addresses")
        print("    4. Routes")
        print("    5. Firewall (Input + Forward)")
        print("    6. NAT Rules")
        print("    7. OSPF (Instance, Area, Neighbors, Templates)")
        print("    8. Services (API, Winbox, etc.)")
        print("\n  EDIT:")
        print("    9. Interactive Edit Mode")
        print("\n  TOOLS:")
        print("    10. Ping")
        print("    11. Traceroute")
        print("    12. ARP Table")
        print("    13. Neighbors")
        print("\n    0. Exit")
        print("="*60)

        choice = input("  Select option: ").strip()

        if choice == "1":
            view_full_config(api, channel)
        elif choice == "2":
            view_identity(api, channel)
        elif choice == "3":
            view_ip_addresses(api, channel)
        elif choice == "4":
            view_routes(api, channel)
        elif choice == "5":
            view_firewall(api, channel)
        elif choice == "6":
            view_nat(api, channel)
        elif choice == "7":
            view_ospf(api, channel)
        elif choice == "8":
            view_services(api, channel)
        elif choice == "9":
            edit_mode(api, channel)
        elif choice == "10":
            tool_ping(api, channel)
        elif choice == "11":
            tool_traceroute(api, channel)
        elif choice == "12":
            tool_arp(api, channel)
        elif choice == "13":
            tool_neighbors(api, channel)
        elif choice == "0":
            print("  Exiting.")
            break
        else:
            print("  Invalid option.")

        input("\n  Press Enter to continue...")

# ================================================================
# ENTRY POINT
# ================================================================

def show_help():
    print("""
MikroTik Configuration Manager
================================
Usage:
  python3 manage.py <target-ip>                         # Direct API
  python3 manage.py <target-ip> --via <neighbor-ip>     # SSH to neighbor
  python3 manage.py <target-ip> --via <neighbor-ip> --mac <mac>  # MAC-Telnet via neighbor

Examples:
  python3 manage.py 192.168.101.8                       # RT1 via API
  python3 manage.py 10.10.20.2                          # RT2 via API
  python3 manage.py 10.10.20.6 --via 192.168.101.8 --mac 0C:2F:5D:8B:00:08  # RT3 via RT1 + MAC-Telnet
  python3 manage.py 10.10.20.2 --via 192.168.101.8      # SSH to RT2 from RT1
""")

def main():
    if len(sys.argv) < 2:
        show_help()
        return

    target_ip = sys.argv[1]
    neighbor_ip = None
    target_mac = None

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--via" and i + 1 < len(sys.argv):
            neighbor_ip = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--mac" and i + 1 < len(sys.argv):
            target_mac = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    api = None
    channel = None
    pool = None
    ssh = None

    # Try direct API first if no --via
    if not neighbor_ip:
        print(f"  Trying direct API to {target_ip} ...")
        pool, api = api_connect(target_ip)
        if api:
            print(f"  Connected via API.")
        else:
            print(f"  Direct API failed. Use --via <neighbor-ip> to connect via SSH.")
            return
    else:
        # SSH via neighbor (with optional MAC-Telnet)
        channel, ssh = ssh_connect(neighbor_ip, target_mac)
        if not channel:
            print("  Connection failed.")
            return

    try:
        interactive_menu(api=api, channel=channel)
    finally:
        if channel:
            channel.close()
        if ssh:
            ssh.close()
        if pool:
            pool.disconnect()

if __name__ == "__main__":
    main()