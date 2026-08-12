# MikroTik Auto-Provisioning System

A Python-based network automation framework for zero-touch provisioning and management of MikroTik RouterOS devices in OSPF-based lab topologies.

[![Python](https://img.shields.io/badge/python-3.7%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

##  Overview

This project automates the complete lifecycle of MikroTik router deployment in a lab environment:

- **Auto-detection** of new routers via neighbor discovery
- **Zero-touch provisioning** over MAC-Telnet/SSH
- **OSPF configuration** with dynamic area support
- **Firewall & NAT** rules for API/Winbox access
- **Verification** of all deployed configurations
- **Diagnostics** on provisioning failure
- **Interactive management** of any router in the topology

---

##  Architecture

PC (Management) ─── RT1 ─── RT2 ─── RT3
│
└── RT4 ...


All routers run OSPF in a backbone area. The script:
1. Configures RT1 (manually connected via API)
2. Discovers new neighbors on any managed router
3. MAC-Telnets into new routers and provisions them
4. Each new router becomes a management point for further discovery

---

##  Quick Start

### Prerequisites

- Python 3.7+
- MikroTik routers running RouterOS v6 or v7
- Network connectivity between your PC and RT1

### Installation

```
git clone https://github.com/yourusername/mikrotik-auto-provision.git
cd mikrotik-auto-provision
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
Usage
1. Configure RT1 manually:
Assign an IP on the LAN interface
Set admin password
2. Run the main script:

python3 ospf.py
Enter RT1's IP, identity, loopback, and OSPF area when prompted.
3. Connect new routers:
Plug factory-fresh MikroTik routers into any free interface of a managed router. The script will detect, prompt for configuration, and provision automatically.
4. Add static route on PC:
sudo ip route add 10.10.20.0/24 via <RT1-LAN-IP>
Configuration Manager
Query or edit any router in the topology:

# Direct API
python3 manage.py 192.168.101.8

# Via neighbor SSH
python3 manage.py 10.10.20.2 --via 192.168.101.8

# Via neighbor MAC-Telnet
python3 manage.py 10.10.20.6 --via 192.168.101.8 --mac 0C:2F:5D:8B:00:08


 Configuration
Edit the top of ospf.py:

Variable
Description
Default
USERNAME / PASSWORD
RouterOS credentials
admin / admin
MGMT_SUBNET
Your management LAN subnet
192.168.101.0/24
PTP_BASE
Pool for point-to-point links
10.10.20.0
PTP_PREFIXLEN
PtP subnet mask length
30


 Project Structure

├── ospf.py              # Main auto-provisioning script
├── manage.py            # Interactive configuration manager
├── requirements.txt     # Python dependencies
├── README.md            # This file
└── network_state.json   # Auto-generated state file (persists across runs)
```

##  Features
Main Script (ospf.py)
- Interactive & automatic modes – defaults provided, Enter to accept
- Duplicate prevention – state file tracks provisioned routers
- Comprehensive verification – firewall, OSPF, routes, services checked post-deployment
- Failure diagnostics – deep inspection of failed routers via MAC-Telnet
- Cleanup – removes stale IPs from parent routers
- Version detection – logs RouterOS version for compatibility tracking
- Management Tool (manage.py)
- Full config dump: identity, IPs, routes, firewall, NAT, OSPF, services
- Interactive edit mode: type RouterOS commands directly
- Works through neighbors: MAC-Telnet tunneling via API-reachable routers

##  Requirements

- routeros-api==0.17.0
- paramiko==3.4.0

##  Testing
Tested with:

- RouterOS v6.49.x
- RouterOS v7.6+
- Python 3.10 on Ubuntu 22.04


##  License

- MIT License – see LICENSE file.

##  Acknowledgments

- MikroTik for RouterOS and the API library
- Paramiko for SSH support
- Open-source community for inspiration and feedback
