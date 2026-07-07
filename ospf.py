#!/usr/bin/env python3
"""
MikroTik Auto-Provisioning – COMPLETE (Working OSPF + All Verification)
"""

import time, json, re
from ipaddress import ip_network, ip_address
from routeros_api import RouterOsApiPool
from routeros_api.exceptions import RouterOsApiCommunicationError
import paramiko

# ---------- USER SETTINGS ----------
USERNAME = "admin"
PASSWORD = "admin"

MGMT_SUBNET = "192.168.101.0/24"   # your PC's LAN subnet

PTP_BASE = "10.10.20.0"            # /24 pool for PtP links
PTP_PREFIXLEN = 30

STATE_FILE = "network_state.json"
# -----------------------------------

def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except FileNotFoundError:
        return {"routers": [], "used_ptp": [], "ospf_area": "0.0.0.0"}

def save_state(s):
    with open(STATE_FILE, "w") as f: json.dump(s, f, indent=2)
    print("[DEBUG] State file updated.")

def api_connect(host, port=8728):
    pool = RouterOsApiPool(host, username=USERNAME, password=PASSWORD,
                           port=port, plaintext_login=True, use_ssl=False)
    try:
        api = pool.get_api()
        api.get_resource("/system/identity").get()
        return pool, api
    except Exception:
        pool.disconnect()
        return None, None

def next_ptp(state):
    base = ip_network(PTP_BASE + "/24", strict=False)
    step = 4
    for i in range(0, 256, step):
        net = ip_network(f"{base.network_address + i}/{PTP_PREFIXLEN}", strict=True)
        if str(net) not in state["used_ptp"]: return net
    raise RuntimeError("No free /30 subnet")

def next_identity_and_loopback(state):
    max_n = 0
    for r in state["routers"]:
        m = re.search(r'RT(\d+)', r["identity"], re.I)
        if m: max_n = max(max_n, int(m.group(1)))
    new_n = max_n + 1
    return f"RT{new_n}", f"{new_n}.{new_n}.{new_n}.{new_n}"

def router_macs(api):
    return {i.get("mac-address", "").upper()
            for i in api.get_resource("/interface").get() if i.get("mac-address")}

def is_valid_ptp_subnet(cidr_str):
    try: return ip_network(cidr_str, strict=True).prefixlen == PTP_PREFIXLEN
    except: return False

def prompt_user_config(state, parent_identity, iface):
    default_identity, default_loopback = next_identity_and_loopback(state)
    default_ptp = next_ptp(state)
    print(f"\n{'='*60}")
    print(f"  New neighbour on {parent_identity} interface {iface}")
    print(f"  Default identity  : {default_identity}")
    print(f"  Default loopback  : {default_loopback}")
    print(f"  Default PtP subnet: {default_ptp}")
    print(f"{'='*60}")
    while True:
        ident = input(f"  Enter identity (or press Enter for '{default_identity}'): ").strip()
        if not ident: ident = default_identity; break
        if re.match(r'^[A-Za-z0-9_-]+$', ident): break
        print("  Invalid identity.")
    while True:
        loop_raw = input(f"  Enter loopback IP (or press Enter for '{default_loopback}'): ").strip()
        if not loop_raw: loopback = default_loopback; break
        try:
            ip = ip_address(loop_raw)
            if ip.is_loopback or ip.is_multicast or ip.is_reserved or ip.is_unspecified: raise ValueError
            loopback = str(ip); break
        except: print(f"  Invalid IP.")
    while True:
        ptp_raw = input(f"  Enter PtP subnet in CIDR notation (or press Enter for '{default_ptp}'): ").strip()
        if not ptp_raw: ptp_net = default_ptp; break
        if is_valid_ptp_subnet(ptp_raw):
            ptp_net = ip_network(ptp_raw, strict=True)
            if str(ptp_net) in state["used_ptp"]: print(f"  Subnet already in use."); continue
            break
        else: print(f"  Invalid /30 subnet.")
    print(f"\n  Proposed: Identity={ident}, Loopback={loopback}/32, PtP={ptp_net}")
    confirm = input("  Confirm? [Y/n]: ").strip().lower()
    if confirm and confirm != 'y': return None, None, None
    return ident, loopback, ptp_net

def safe_add(api, resource_path, **kwargs):
    try: api.get_resource(resource_path).add(**kwargs)
    except RouterOsApiCommunicationError as e:
        if "already have" in str(e) or "already exist" in str(e):
            print(f"  [INFO] Object already exists.")
        else: raise

def ensure_ospf_area(api, area_id):
    areas = api.get_resource("/routing/ospf/area").get()
    for a in areas:
        if a.get("area-id") == area_id: return a.get("name")
    print(f"  [OSPF] Creating area {area_id} ...")
    instances = api.get_resource("/routing/ospf/instance").get()
    if not instances: api.get_resource("/routing/ospf/instance").add(name="default", router_id="0.0.0.0", disabled="no")
    inst_name = instances[0]["name"] if instances else "default"
    area_name = "backbone" if area_id == "0.0.0.0" else f"area_{area_id.replace('.', '_')}"
    safe_add(api, "/routing/ospf/area", name=area_name, area_id=area_id, instance=inst_name)
    return area_name

def detect_routeros_version(api):
    try:
        res = api.get_resource("/system/resource").get()
        version_full = res[0].get("version", "unknown")
        major = int(version_full.split(".")[0])
        print(f"  [INFO] Detected RouterOS version: {version_full} (v{major})")
        return major, version_full
    except:
        return 7, "unknown"

def manage_ospf_templates(api):
    resource = api.get_resource('/routing/ospf/interface-template')
    templates = resource.get()
    if not templates:
        print("  No existing OSPF templates found.")
        return
    print(f"\n  Current OSPF Interface Templates ({len(templates)}):")
    for idx, t in enumerate(templates):
        passive = " (passive)" if t.get("passive", "") == "yes" else ""
        print(f"    [{idx}] interfaces: {t.get('interfaces', '')}, area: {t.get('area', '')}{passive}")
    while True:
        choice = input("  Enter indices to delete (comma-separated), 'all', or press Enter to keep all: ").strip().lower()
        if not choice:
            print("  Keeping all existing templates.")
            return
        if choice == "all":
            for t in templates:
                try: resource.remove(id=t['id']); print(f"    Deleted {t['id']}")
                except Exception as e: print(f"    Failed: {e}")
            return
        try: indices = [int(x.strip()) for x in choice.split(",")]
        except ValueError: print("  Invalid input."); continue
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(templates):
                t = templates[idx]
                try: resource.remove(id=t['id']); print(f"    Deleted {t['id']}")
                except Exception as e: print(f"    Failed: {e}")
            else: print(f"    Index {idx} out of range.")
        return

def show_rt1_full_config(api):
    print("\n" + "="*60)
    print("  RT1 FULL CONFIGURATION")
    print("="*60)
    try:
        ident = api.get_resource("/system/identity").get()
        if ident: print(f"  Identity: {ident[0]['name']}")
    except: print("  Identity: (error)")
    print("\n  IP Addresses:")
    try:
        for addr in api.get_resource("/ip/address").get():
            print(f"    {addr.get('address', '?')} on {addr.get('interface', '?')}")
    except Exception as e: print(f"    Error: {e}")
    print("\n  Firewall Filter Rules:")
    try:
        for rule in api.get_resource("/ip/firewall/filter").get():
            print(f"    chain={rule.get('chain','')} action={rule.get('action','')} "
                  f"src-address={rule.get('src-address','')} dst-address={rule.get('dst-address','')} "
                  f"protocol={rule.get('protocol','')} dst-port={rule.get('dst-port','')}")
    except Exception as e: print(f"    Error: {e}")
    print("\n  NAT Rules:")
    try:
        for nat in api.get_resource("/ip/firewall/nat").get():
            print(f"    chain={nat.get('chain','')} action={nat.get('action','')} "
                  f"src-address={nat.get('src-address','')} dst-address={nat.get('dst-address','')} "
                  f"out-interface={nat.get('out-interface','')}")
    except Exception as e: print(f"    Error: {e}")
    print("\n  OSPF Instance:")
    try:
        inst = api.get_resource("/routing/ospf/instance").get()
        if inst: print(f"    {inst[0].get('name','')} router-id={inst[0].get('router-id','')} disabled={inst[0].get('disabled','')}")
    except: print("    (none)")
    print("  OSPF Area:")
    try:
        area = api.get_resource("/routing/ospf/area").get()
        if area: print(f"    {area[0].get('name','')} area-id={area[0].get('area-id','')}")
    except: print("    (none)")
    print("  OSPF Interface Templates:")
    try:
        templates = api.get_resource("/routing/ospf/interface-template").get()
        if templates:
            for t in templates:
                passive = " (passive)" if t.get("passive", "") == "yes" else ""
                print(f"    interfaces={t.get('interfaces','')} area={t.get('area','')}{passive}")
        else: print("    (none)")
    except Exception as e: print(f"    Error: {e}")
    print("="*60)

def configure_rt1_ospf(api, rt1_ip, rt1_ident, loopback_ip, area_id):
    print(f"  [RT1] Setting identity to '{rt1_ident}' ...")
    try:
        api.get_resource("/system/identity").set(name=rt1_ident)
        new_ident = api.get_resource("/system/identity").get()[0]["name"]
        if new_ident == rt1_ident: print(f"  [RT1] Identity set.")
        else: print(f"  [RT1] Identity still '{new_ident}'.")
    except Exception as e: print(f"  [RT1] Identity failed: {e}")

    manage_ospf_templates(api)

    if not api.get_resource("/interface").get(name="lo"):
        print("  [RT1 OSPF] Creating loopback bridge 'lo' ...")
        safe_add(api, "/interface/bridge", name="lo")
    else: print("  [RT1 OSPF] Loopback bridge already exists.")

    loopback_addr = f"{loopback_ip}/32"
    if not api.get_resource("/ip/address").get(address=loopback_addr):
        print(f"  [RT1 OSPF] Adding loopback IP {loopback_addr} ...")
        safe_add(api, "/ip/address", interface="lo", address=loopback_addr)
    else: print(f"  [RT1 OSPF] Loopback IP already exists.")

    instances = api.get_resource("/routing/ospf/instance").get()
    if not instances:
        print("  [RT1 OSPF] Creating OSPF instance ...")
        safe_add(api, "/routing/ospf/instance", name="default", router_id=loopback_ip, disabled="no")
    else:
        inst = instances[0]
        if inst.get("router-id") != loopback_ip:
            print(f"  [RT1 OSPF] Updating router-id to {loopback_ip} ...")
            api.get_resource("/routing/ospf/instance").set(id=inst['id'], **{"router-id": loopback_ip})
        else: print("  [RT1 OSPF] OSPF instance already configured.")

    area_name = ensure_ospf_area(api, area_id)

    lan_iface = None
    for addr in api.get_resource("/ip/address").get():
        if addr.get("address", "").startswith(rt1_ip): lan_iface = addr.get("interface"); break
    if lan_iface:
        templates = api.get_resource("/routing/ospf/interface-template").get()
        for t in templates:
            if lan_iface in t.get("interfaces", "").split(","):
                try: api.get_resource("/routing/ospf/interface-template").remove(id=t['id'])
                except: pass
        print(f"  [RT1 OSPF] Adding passive template for LAN ({lan_iface}) ...")
        safe_add(api, "/routing/ospf/interface-template", interfaces=lan_iface, area=area_name, passive="yes")
    else: print("  [RT1 OSPF] WARNING: Could not determine LAN interface.")

    templates = api.get_resource("/routing/ospf/interface-template").get()
    for t in templates:
        if "lo" in t.get("interfaces", "").split(","):
            try: api.get_resource("/routing/ospf/interface-template").remove(id=t['id'])
            except: pass
    print("  [RT1 OSPF] Adding passive template for loopback ...")
    safe_add(api, "/routing/ospf/interface-template", interfaces="lo", area=area_name, passive="yes")

    fw = api.get_resource("/ip/firewall/filter")
    if not fw.get(chain="input", protocol="ospf", action="accept"):
        print("  [RT1 OSPF] Adding input accept rule for OSPF ...")
        try: fw.add(chain="input", protocol="ospf", action="accept")
        except: pass

    return lan_iface if lan_iface else "unknown"

def enable_ospf_instance_ssh(parent_ip):
    print("  [PARENT] Enabling OSPF instance via SSH ...")
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(parent_ip, username=USERNAME, password=PASSWORD, look_for_keys=False, allow_agent=False, timeout=10)
        ssh.exec_command("/routing ospf instance set [find name=default] disabled=no\r")
        time.sleep(1)
        ssh.close()
    except Exception as e:
        print(f"  [PARENT] SSH OSPF enable failed (maybe already enabled): {e}")

def configure_parent_api(api, neighbor, ptp_net, child_identity, area_id):
    iface = neighbor["interface"]
    parent_ip = str(ptp_net[1]); ptp_str = str(ptp_net)

    # 1. Parent IP
    if not api.get_resource("/ip/address").get(interface=iface, address=f"{parent_ip}/{PTP_PREFIXLEN}"):
        safe_add(api, "/ip/address", interface=iface, address=f"{parent_ip}/{PTP_PREFIXLEN}",
                 network=str(ptp_net.network_address))
    else: print(f"  [PARENT] IP already exists on {iface}.")

    # 2. Forward firewall rules (safe placement)
    fw = api.get_resource("/ip/firewall/filter")
    comment = f"auto-{child_identity}"
    if not fw.get(comment=comment):
        try:
            if fw.get(chain="forward"): fw.add(chain="forward", src_address=MGMT_SUBNET, dst_address=ptp_str,
                                               action="accept", comment=comment, place_before="0")
            else: fw.add(chain="forward", src_address=MGMT_SUBNET, dst_address=ptp_str,
                         action="accept", comment=comment)
        except: pass
        try: fw.add(chain="forward", src_address=ptp_str, dst_address=MGMT_SUBNET,
                    action="accept", comment=comment, place_before="0")
        except: pass

    # 3. Masquerade for LAN -> PtP (makes API/Winbox always work)
    nat = api.get_resource("/ip/firewall/nat")
    if not nat.get(chain="srcnat", src_address=MGMT_SUBNET, dst_address=ptp_str, action="masquerade"):
        print("  [PARENT] Adding srcnat masquerade for LAN -> PtP ...")
        try:
            nat.add(chain="srcnat", src_address=MGMT_SUBNET, dst_address=ptp_str,
                    action="masquerade", out_interface=iface)
        except Exception as e:
            print(f"  [PARENT] Masquerade rule (maybe exists): {e}")

    # 4. OSPF input accept on parent
    if not fw.get(chain="input", protocol="ospf", action="accept"):
        print("  [PARENT] Adding input accept rule for OSPF ...")
        try: fw.add(chain="input", protocol="ospf", action="accept")
        except: pass

    # 5. OSPF active template on PtP interface
    area_name = ensure_ospf_area(api, area_id)
    templates = api.get_resource("/routing/ospf/interface-template").get()
    already = any(iface in t.get("interfaces", "").split(",") and t.get("area") == area_name for t in templates)
    if not already:
        safe_add(api, "/routing/ospf/interface-template", interfaces=iface, area=area_name)
        print(f"  [PARENT] Active OSPF template for {iface} added.")
    else: print(f"  [PARENT] Active OSPF template for {iface} already exists.")

    parent_mac = ""
    try:
        macs = api.get_resource("/interface").get(name=iface)
        if macs: parent_mac = macs[0].get("mac-address", "").upper()
    except: pass
    return parent_mac

def wait_and_capture(channel, targets, timeout=10.0):
    start = time.time(); buffer = ""
    while time.time() - start < timeout:
        time.sleep(0.1)
        if channel.recv_ready():
            chunk = channel.recv(4096).decode("utf-8", errors="ignore")
            buffer += chunk
            if any(t in buffer for t in targets): return True, buffer
    return False, buffer

def send_and_wait_for_prompt(channel, cmd, prompt_patterns=["> ", "] >"], wait=1.0):
    print(f"  [CMD] {cmd}")
    channel.send(cmd + "\r")
    time.sleep(wait)
    success, output = wait_and_capture(channel, prompt_patterns, timeout=10.0)
    if output.strip():
        for line in output.splitlines():
            if line.strip():
                print(f"    {line.strip()}")
    return output

def get_child_interface_by_parent_mac(channel, parent_mac):
    if not parent_mac: return None
    mac_clean = parent_mac.replace(":", "")
    for attempt in range(3):
        for fmt in (parent_mac, mac_clean):
            cmd = f':put [/ip neighbor get [find mac-address={fmt}] interface]'
            out = send_and_wait_for_prompt(channel, cmd, wait=3)
            for line in out.splitlines():
                line = line.strip()
                if line and re.match(r'^[a-zA-Z0-9_-]+$', line) and not line.startswith("["):
                    print(f"  [MAC-TELNET] Detected child interface: {line}")
                    return line
        time.sleep(5)
    return None

def get_active_child_interface(channel):
    time.sleep(3)
    cmd = (
        ':local iface [/interface ethernet find where running];'
        ':if ([:len $iface] > 0) do={'
        ':put [/interface ethernet get $iface->0 name]'
        '} else={'
        ':put "ether1"'
        '}'
    )
    out = send_and_wait_for_prompt(channel, cmd, wait=2)
    for line in out.splitlines():
        line = line.strip()
        if line and re.match(r'^[a-zA-Z0-9_-]+$', line) and not line.startswith("["):
            return line
    return "ether1"

def apply_firewall_rules(channel, mgmt_subnet, parent_ptp):
    rules = [
        f"/ip firewall filter add chain=input protocol=ospf action=accept place-before=0",
        f"/ip firewall filter add chain=input src-address={mgmt_subnet} protocol=icmp action=accept place-before=0",
        f"/ip firewall filter add chain=input src-address={mgmt_subnet} protocol=tcp dst-port=8728,8291 action=accept place-before=0",
        f"/ip firewall filter add chain=input src-address={parent_ptp} protocol=icmp action=accept place-before=0",
        f"/ip firewall filter add chain=input src-address={parent_ptp} protocol=tcp dst-port=8728,8291 action=accept place-before=0",
    ]
    print("  [FIREWALL] Adding input rules one by one...")
    for rule in rules:
        resp = send_and_wait_for_prompt(channel, rule, wait=0.5)
        if "no such item" in resp or "expected" in resp:
            fallback = rule.replace(" place-before=0", "")
            print(f"    Placement failed, retrying: {fallback}")
            send_and_wait_for_prompt(channel, fallback, wait=0.5)
    print("  [FIREWALL] Verification:")
    send_and_wait_for_prompt(channel, "/ip firewall filter print chain=input", wait=2)

def configure_child_via_mactelnet(parent_host, mac, parent_mac, child_identity, child_loopback,
                                  child_ip, ptp_prefixlen, mgmt_subnet, area_id):
    print(f"  [MAC-TELNET] Opening SSH shell to {parent_host} ...")
    ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(parent_host, username=USERNAME, password=PASSWORD,
                look_for_keys=False, allow_agent=False, timeout=10)
    channel = ssh.invoke_shell(term="vt100", width=120, height=24); time.sleep(2.0)
    wait_and_capture(channel, [">", "]"])
    print("  [MAC-TELNET] Cleared parent banner.")

    # MAC-Telnet – send without consuming output
    print(f"  [CMD] /tool mac-telnet {mac}")
    channel.send(f"/tool mac-telnet {mac}\r")

    success, output = wait_and_capture(channel, [
        "Do you want to see the software license?", "Login:", "User:"
    ], timeout=20)
    if not success:
        print("  [MAC-TELNET] Timeout waiting for child prompt.")
        channel.close(); ssh.close(); return False, None

    if "Do you want to see the software license?" in output:
        channel.send("n\r"); time.sleep(0.5)
        if not wait_and_capture(channel, ["new password>"], timeout=10)[0]:
            channel.close(); ssh.close(); return False, None
        channel.send(f"{PASSWORD}\r"); time.sleep(0.5)
        if not wait_and_capture(channel, ["repeat new password>"], timeout=10)[0]:
            channel.close(); ssh.close(); return False, None
        channel.send(f"{PASSWORD}\r"); time.sleep(1)
        if not wait_and_capture(channel, ["> "], timeout=10)[0]:
            channel.close(); ssh.close(); return False, None
    elif "Login:" in output or "User:" in output:
        time.sleep(0.5)
        channel.send(f"{USERNAME}\r")
        success, _ = wait_and_capture(channel, ["Password:"], timeout=5.0)
        if not success:
            print("  [MAC-TELNET] Missing Password prompt.")
            channel.close(); ssh.close(); return False, None
        time.sleep(0.5)
        channel.send(f"{PASSWORD}\r")
        time.sleep(1.0)
        temp_output = ""
        while channel.recv_ready():
            temp_output += channel.recv(4096).decode("utf-8", errors="ignore")
        if temp_output:
            print(f"  [LOGIN] Intermediate: {temp_output.strip()}")
        success, landing = wait_and_capture(channel, ["] >", "failed", "incorrect"], timeout=10.0)
        if "failed" in landing or "incorrect" in landing:
            print("  [MAC-TELNET] Authentication failed.")
            channel.close(); ssh.close(); return False, None
        if not success:
            print("  [MAC-TELNET] Did not reach prompt after login.")
            channel.close(); ssh.close(); return False, None
        time.sleep(2.0)
        channel.send("\r")
        time.sleep(1.0)
        print("  [MAC-TELNET] Logged in successfully.")
    else:
        channel.close(); ssh.close(); return False, None

    # Detect child interface
    active_iface = get_child_interface_by_parent_mac(channel, parent_mac)
    if not active_iface:
        print("  [MAC-TELNET] Parent-MAC detection failed, using fallback...")
        active_iface = get_active_child_interface(channel)
    print(f"  [MAC-TELNET] Using child interface: {active_iface}")

    parent_ptp = str(ip_network(f"{child_ip}/{ptp_prefixlen}", strict=False)[1])

    # ---- STATIC ROUTE FIRST ----
    route_cmd = f"/ip route add dst-address={mgmt_subnet} gateway={parent_ptp}"
    send_and_wait_for_prompt(channel, route_cmd, wait=1)

    # ---- BASIC CONFIGURATION ----
    basic_cmds = [
        f"/system identity set name={child_identity}",
        f"/ip address add interface={active_iface} address={child_ip}/{ptp_prefixlen}",
        ":if ([:len [/interface find name=lo]] = 0) do={/interface bridge add name=lo}",
        f"/ip address add interface=lo address={child_loopback}/32",
        ":if ([:len [/routing ospf instance find]] > 0) do={/routing ospf instance remove [find]}",
        f"/routing ospf instance add name=default router-id={child_loopback} disabled=no",
        f"/routing ospf area add name=backbone area-id={area_id} instance=default",
        f"/routing ospf interface-template add interfaces={active_iface} area=backbone",
        "/routing ospf interface-template add interfaces=lo area=backbone passive",
        "/ip service set api disabled=no",
        "/ip service set api-ssl disabled=no",
        "/ip service set winbox disabled=no",
    ]
    for cmd in basic_cmds:
        send_and_wait_for_prompt(channel, cmd, wait=0.5)

    # ---- FIREWALL RULES ----
    apply_firewall_rules(channel, mgmt_subnet, parent_ptp)

    # ---- FINAL STEPS ----
    send_and_wait_for_prompt(channel, '/log info "=== provisioning complete ==="')
    send_and_wait_for_prompt(channel, "/routing ospf instance set [find name=default] disabled=no", wait=1)

    # ---- FULL VERIFICATION DUMP ----
    print("\n  [VERIFICATION] Child router current configuration:")
    for check_cmd in ["/ip address print", "/ip route print", "/ip firewall filter print chain=input",
                      "/routing ospf instance print", "/routing ospf interface-template print"]:
        send_and_wait_for_prompt(channel, check_cmd, wait=2)

    channel.close(); ssh.close()
    return True, active_iface

def cleanup_rt1(api, child_ip, child_loopback):
    print("  [CLEANUP] Checking for stale IPs on RT1...")
    try:
        ips = api.get_resource("/ip/address").get()
        for ip in ips:
            addr = ip.get("address", "")
            if addr.startswith(child_ip) or addr == f"{child_loopback}/32":
                print(f"    Removing stale IP {addr} on {ip.get('interface','')}")
                try: api.get_resource("/ip/address").remove(id=ip['id'])
                except Exception as e: print(f"    Failed: {e}")
    except Exception as e:
        print(f"  [CLEANUP] Error: {e}")

def diagnose_child_via_mactelnet(parent_host, mac):
    """Separate diagnostic session – does NOT interfere with the child's state."""
    print(f"\n  [DIAGNOSTICS] Connecting to child {mac} via {parent_host} ...")
    try:
        ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(parent_host, username=USERNAME, password=PASSWORD,
                    look_for_keys=False, allow_agent=False, timeout=10)
        channel = ssh.invoke_shell(term="vt100", width=120, height=24); time.sleep(2.0)
        wait_and_capture(channel, [">", "]"])
        print(f"  [DIAG] Sending: /tool mac-telnet {mac}")
        channel.send(f"/tool mac-telnet {mac}\r")

        success, output = wait_and_capture(channel, [
            "Do you want to see the software license?", "Login:", "User:"
        ], timeout=20)
        if not success:
            print("  [DIAGNOSTICS] Could not reach child prompt."); channel.close(); ssh.close(); return
        if "Do you want to see the software license?" in output:
            print("  [DIAGNOSTICS] Child is factory‑fresh (unexpected)."); channel.close(); ssh.close(); return

        time.sleep(0.5)
        channel.send(f"{USERNAME}\r")
        success, _ = wait_and_capture(channel, ["Password:"], timeout=5.0)
        if not success:
            print("  [DIAGNOSTICS] Password prompt missing."); channel.close(); ssh.close(); return
        time.sleep(0.5)
        channel.send(f"{PASSWORD}\r")
        time.sleep(1.0)
        temp = ""
        while channel.recv_ready():
            temp += channel.recv(4096).decode("utf-8", errors="ignore")
        if temp: print(f"  [DIAG] Intermediate: {temp.strip()}")
        success, landing = wait_and_capture(channel, ["] >", "failed", "incorrect"], timeout=10.0)
        if "failed" in landing or "incorrect" in landing:
            print("  [DIAGNOSTICS] Authentication failed."); channel.close(); ssh.close(); return
        if not success:
            print("  [DIAGNOSTICS] Did not reach prompt."); channel.close(); ssh.close(); return
        time.sleep(2.0)
        channel.send("\r"); time.sleep(1.0)
        print("  [DIAGNOSTICS] Logged in successfully.")

        diag_cmds = [
            "/ip address print",
            "/ip route print",
            "/ip firewall filter print chain=input",
            "/ip service print",
            "/routing ospf instance print",
            "/routing ospf neighbor print",
            "/routing ospf interface-template print",
            f"/ping {parent_host} count=2",
        ]
        print("  [DIAGNOSTICS] --- Running diagnostics ---")
        for cmd in diag_cmds:
            send_and_wait_for_prompt(channel, cmd, wait=2)
        print("  [DIAGNOSTICS] --- End of diagnostics ---")
        channel.close(); ssh.close()
    except Exception as e:
        print(f"  [DIAGNOSTICS] Error: {e}")

def check_rt1_forwarding(api):
    print("\n  [RT1 VERIFY] Checking RT1's forward rules and NAT...")
    try:
        fw = api.get_resource("/ip/firewall/filter").get(chain="forward")
        print("  Forward rules:")
        for r in fw:
            print(f"    {r}")
    except Exception as e:
        print(f"    Error: {e}")
    try:
        nat = api.get_resource("/ip/firewall/nat").get()
        print("  NAT rules:")
        for n in nat:
            print(f"    {n}")
    except Exception as e:
        print(f"    Error: {e}")

def main():
    print("=== MikroTik Auto-Provisioning (COMPLETE) ===")
    state = load_state()
    if not state["routers"]:
        print("\nFirst run: Please enter RT1's information.\n")
        rt1_ip = input("  RT1 IP [192.168.101.23]: ").strip() or "192.168.101.23"
        while True:
            try: ip_address(rt1_ip); break
            except: rt1_ip = input("  Invalid IP, try again: ").strip()
        rt1_ident = input("  RT1 identity [RT1]: ").strip() or "RT1"
        while not re.match(r'^[A-Za-z0-9_-]+$', rt1_ident): rt1_ident = input("  Invalid identity: ").strip()
        rt1_loop = input("  RT1 loopback [1.1.1.1]: ").strip() or "1.1.1.1"
        while True:
            try: ip_address(rt1_loop); break
            except: rt1_loop = input("  Invalid IP: ").strip()
        area_id = input("  OSPF area [0.0.0.0]: ").strip() or "0.0.0.0"
        while not (re.match(r'^\d+\.\d+\.\d+\.\d+$', area_id) or area_id.isdigit()):
            area_id = input("  Invalid area ID: ").strip()
        pool, api = api_connect(rt1_ip)
        if not api: print("ERROR: Cannot connect to RT1."); return
        version_major, version_full = detect_routeros_version(api)
        lan_iface = configure_rt1_ospf(api, rt1_ip, rt1_ident, rt1_loop, area_id)
        enable_ospf_instance_ssh(rt1_ip)
        macs = list(router_macs(api))
        state["routers"].append({"host": rt1_ip, "port": 8728, "identity": rt1_ident, "loopback": rt1_loop, "macs": macs})
        state["ospf_area"] = area_id; state["version"] = version_major; state["version_full"] = version_full
        show_rt1_full_config(api)
        pool.disconnect(); save_state(state)
        print(f"\n  RouterOS Version: {version_full} (major {version_major})")
        print(f"  REMINDER: sudo ip route add 10.10.20.0/24 via {rt1_ip}\n")
    area_id = state.get("ospf_area", "0.0.0.0")
    print("\n[MAIN LOOP] Scanning...")
    while True:
        try:
            for router in list(state["routers"]):
                pool, api = api_connect(router["host"], router["port"])
                if not api: continue
                neighbors = api.get_resource("/ip/neighbor").get()
                for n in neighbors:
                    platform = n.get("platform", ""); mac = n.get("mac-address", "").upper()
                    if platform != "MikroTik" or not mac: continue
                    if any(mac in r.get("macs", []) for r in state["routers"]): continue
                    iface = n.get("interface", "unknown")
                    new_identity, new_loopback, ptp_net = prompt_user_config(state, router["identity"], iface)
                    if not new_identity: continue
                    if str(ptp_net) in state["used_ptp"]: continue
                    parent_mac = configure_parent_api(api, n, ptp_net, new_identity, area_id)
                    child_ip = str(ptp_net[2])
                    enable_ospf_instance_ssh(router["host"])
                    success, used_iface = configure_child_via_mactelnet(
                        router["host"], mac, parent_mac, new_identity, new_loopback,
                        child_ip, PTP_PREFIXLEN, MGMT_SUBNET, area_id)
                    cleanup_rt1(api, child_ip, new_loopback)
                    state["used_ptp"].append(str(ptp_net))
                    state["routers"].append({"host": child_ip, "port": 8728, "identity": new_identity, "loopback": new_loopback, "macs": [mac]})
                    save_state(state)
                    if not success:
                        print(f"✘ Configuration failed for {new_identity}. Skipping future attempts.")
                        continue
                    time.sleep(30)
                    cpool, capi = api_connect(child_ip)
                    if capi:
                        new_macs = list(router_macs(capi))
                        state["routers"][-1]["macs"] = new_macs
                        save_state(state)
                        print(f"✔ SUCCESS: {new_identity} online at {child_ip}")
                        cpool.disconnect()
                    else:
                        print("\n  [FAILURE] API verification failed. Running deep diagnostics...")
                        check_rt1_forwarding(api)
                        diagnose_child_via_mactelnet(router["host"], mac)
                        print(f"✘ API verification failed for {new_identity}. Marked as failed.")
                pool.disconnect()
            time.sleep(10)
        except KeyboardInterrupt: break
        except Exception as e: print(f"[ERROR] {e}"); time.sleep(10)

if __name__ == "__main__":
    main()
