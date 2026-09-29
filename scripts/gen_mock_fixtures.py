#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate DEV mock-lab fixtures under tests/fixtures/mock/.

These are synthetic, plausible sonic-vs outputs used only by MockAdapter so the lesson loop
runs with no containerlab. They are NOT authored curriculum. Defaults are healthy-state; each
anchor chaos gets an override subfolder so the demo shows a real before/after diff. The script
asserts every observe command in every lesson has a default fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from chaoslab.lab.adapter import slugify_spec  # noqa: E402
from chaoslab.lessons import loader  # noqa: E402

MOCK_DIR = REPO_ROOT / "tests" / "fixtures" / "mock"


def ping(dest: str, count: int, size: int = 0, loss: float = 0.0, local_error: bool = False) -> str:
    data = size if size else 56
    received = 0 if local_error else round(count * (1 - loss / 100))
    lines = [f"PING {dest} ({dest}): {data} data bytes"]
    if local_error:
        lines += ["ping: local error: Message too long, mtu=1500" for _ in range(count)]
    else:
        lines += [
            f"{data + 8} bytes from {dest}: icmp_seq={i} ttl=63 time=0.1{i} ms"
            for i in range(received)
        ]
    lines += [
        "",
        f"--- {dest} ping statistics ---",
        f"{count} packets transmitted, {received} packets received, {loss:g}% packet loss",
    ]
    if received:
        lines.append("round-trip min/avg/max = 0.10/0.15/0.20 ms")
    return "\n".join(lines) + "\n"


IFACE_HEADER = (
    "  Interface            Lanes    Speed    MTU    FEC        Alias    Vlan    Oper    Admin\n"
    "-----------  ---------------  -------  -----  -----  -----------  ------  ------  -------\n"
)


def iface_rows(rows: list[tuple[str, str, str, str]]) -> str:
    body = "".join(
        f"  {name:<9}      0,1,2,3      40G   {mtu:>5}    N/A  {name:>11}  {vlan:>6}  {oper:>6}  {admin:>7}\n"
        for name, oper, admin, mtu, vlan in [(n, o, a, m, "routed") for n, o, a, m in rows]
    )
    return IFACE_HEADER + body


def bgp_summary(neighbors: list[tuple[str, str, str, str]], router_id: str, local_as: str) -> str:
    header = (
        f"BGP router identifier {router_id}, local AS number {local_as} vrf-id 0\n"
        "BGP table version 4\n\n"
        "Neighbor        V         AS   MsgRcvd   MsgSent   TblVer  InQ OutQ  Up/Down State/PfxRcd\n"
    )
    body = "".join(
        f"{nbr:<15} 4      {asn}       120       118        4    0    0 {updown:>8}   {state_pfx:>11}\n"
        for nbr, asn, updown, state_pfx in neighbors
    )
    return header + body + f"\nTotal number of neighbors {len(neighbors)}\n"


ROUTE_CODES = "Codes: K - kernel, C - connected, S - static, B - BGP, > - selected, * - FIB\n\n"


def route_2nh() -> str:
    return (
        ROUTE_CODES
        + "B>* 10.0.2.0/24 [20/0] via 10.0.12.1, Ethernet0, 00:10:23\n"
        + "  *                    via 10.0.12.3, Ethernet4, 00:10:23\n"
    )


def route_1nh() -> str:
    return ROUTE_CODES + "B>* 10.0.2.0/24 [20/0] via 10.0.12.3, Ethernet4, 00:00:12\n"


def full_route_table() -> str:
    return (
        ROUTE_CODES
        + "B>* 10.0.2.0/24 [20/0] via 10.0.12.1, Ethernet0, 00:10:23\n"
        + "  *                    via 10.0.12.3, Ethernet4, 00:10:23\n"
        + "C>* 10.0.1.0/24 is directly connected, Vlan10, 00:12:00\n"
        + "C>* 10.0.12.0/31 is directly connected, Ethernet0, 00:12:00\n"
        + "C>* 10.0.12.2/31 is directly connected, Ethernet4, 00:12:00\n"
    )


def keys(*names: str) -> str:
    return ("\n".join(names) + "\n") if names else "\n"


BASE_IFACES = [
    ("Ethernet0", "up", "up", "9100"),
    ("Ethernet4", "up", "up", "9100"),
    ("Ethernet8", "up", "up", "9100"),
]

DEFAULTS: dict[str, str] = {
    # --- host data-plane probes ---
    "h1: ping -c 3 10.0.1.1": ping("10.0.1.1", 3),
    "h1: ping -c 5 10.0.1.1": ping("10.0.1.1", 5),
    "h1: ping -c 10 10.0.1.1": ping("10.0.1.1", 10),
    "h1: ping -c 3 10.0.2.10": ping("10.0.2.10", 3),
    "h1: ping -c 5 10.0.2.10": ping("10.0.2.10", 5),
    "h1: ping -c 10 10.0.2.10": ping("10.0.2.10", 10),
    "h1: ping -M do -s 1472 -c 3 10.0.2.10": ping("10.0.2.10", 3, 1472),
    "h1: ping -M do -s 1472 -c 5 10.0.2.10": ping("10.0.2.10", 5, 1472),
    "h1: ping -M do -s 8972 -c 3 10.0.2.10": ping("10.0.2.10", 3, 8972),
    "h1: ping -M do -s 8972 -c 5 10.0.2.10": ping("10.0.2.10", 5, 8972),
    "h1: ping -s 8972 -c 3 10.0.2.10": ping("10.0.2.10", 3, 8972),
    "h1: ping -s 8972 -c 5 10.0.2.10": ping("10.0.2.10", 5, 8972),
    "h1: ip neigh show": "10.0.1.1 dev eth1 lladdr 02:00:00:00:0a:01 REACHABLE\n",
    # --- interfaces / L2 ---
    "leaf1: show interfaces status": iface_rows(BASE_IFACES),
    "leaf2: show interfaces status": iface_rows(BASE_IFACES),
    "leaf1: show interfaces counters": (
        "      IFACE    STATE    RX_OK    RX_ERR    RX_DRP    TX_OK    TX_ERR    TX_DRP\n"
        "  Ethernet8        U      1024         0         0     1024         0         0\n"
    ),
    "leaf1: show mac": (
        "  No.    Vlan    MacAddress          Port        Type\n"
        "-----  ------  -----------------  ----------  ---------\n"
        "    1      10  02:00:00:00:01:10   Ethernet8    Dynamic\n"
        "Total number of entries 1\n"
    ),
    "leaf1: show mac -c": "Total number of entries 1\n",
    "leaf1: show mac aging-time": "Aging time for switch is 600 seconds\n",
    "leaf1: show vlan brief": (
        "+-------+-------------+-----------+----------------+\n"
        "|  VLAN |  IP Address |     Ports |   Port Tagging |\n"
        "+-------+-------------+-----------+----------------+\n"
        "|    10 | 10.0.1.1/24 | Ethernet8 |       untagged |\n"
        "+-------+-------------+-----------+----------------+\n"
    ),
    "leaf1: show vlan config": (
        "Name      VID    Member       Mode\n"
        "------  -----  -----------  --------\n"
        "Vlan10     10    Ethernet8  untagged\n"
    ),
    "leaf1: show lldp table": (
        "LocalPort    RemoteDevice    RemotePortID    Capability\n"
        "-----------  --------------  --------------  ----------\n"
        "Ethernet0    leaf2           Ethernet0       BR\n"
        "Ethernet4    leaf2           Ethernet4       BR\n"
    ),
    "leaf1: show lldp neighbors Ethernet0": (
        "Interface:    Ethernet0\n"
        "  ChassisID:  mac 52:54:00:00:00:02\n"
        "  SysName:    leaf2\n"
        "  TTL:        120\n"
        "  PortID:     Ethernet0\n"
    ),
    "leaf1: show arp": (
        "Address      MacAddress         Iface     Vlan\n"
        "-----------  -----------------  --------  ----\n"
        "10.0.1.10    02:00:00:00:01:10  Vlan10      10\n"
    ),
    "leaf1: show ip interfaces": (
        "Interface    Master    IPv4 address/mask    Admin/Oper\n"
        "-----------  --------  -------------------  ----------\n"
        "Vlan10                 10.0.1.1/24          up/up\n"
        "Ethernet0              10.0.12.0/31         up/up\n"
        "Ethernet4              10.0.12.2/31         up/up\n"
    ),
    # --- routing / bgp ---
    "leaf1: show bgp summary": bgp_summary(
        [("10.0.12.1", "65002", "00:10:23", "1"), ("10.0.12.3", "65002", "00:10:23", "1")],
        "10.0.12.0",
        "65001",
    ),
    "leaf2: show bgp summary": bgp_summary(
        [("10.0.12.0", "65001", "00:10:23", "1"), ("10.0.12.2", "65001", "00:10:23", "1")],
        "10.0.12.1",
        "65002",
    ),
    "leaf1: show bgp neighbors 10.0.12.1": (
        "BGP neighbor is 10.0.12.1, remote AS 65002, local AS 65001, external link\n"
        "  BGP state = Established, up for 00:10:23\n"
        "  Last read 00:00:01, Last write 00:00:01\n"
        "  Hold time is 10, keepalive interval is 3 seconds\n"
        "  Message statistics:\n"
        "    Opens:          1          1\n"
        "    Notifications:  0          0\n"
        "    Updates:        3          2\n"
        "    Keepalives:   205        205\n"
    ),
    "leaf1: show bgp neighbors 10.0.12.1 advertised-routes": (
        "   Network          Next Hop\n*> 10.0.1.0/24      0.0.0.0\nTotal number of prefixes 1\n"
    ),
    "leaf1: show bgp neighbors 10.0.12.1 received-routes": (
        "   Network          Next Hop\n*> 10.0.2.0/24      10.0.12.1\nTotal number of prefixes 1\n"
    ),
    "leaf1: show bgp neighbors 10.0.12.3 received-routes": (
        "   Network          Next Hop\n*> 10.0.2.0/24      10.0.12.3\nTotal number of prefixes 1\n"
    ),
    "leaf1: show ip route": full_route_table(),
    "leaf1: show ip route 10.0.2.0/24": route_2nh(),
    'leaf1: vtysh -c "show ip bgp 10.0.2.0/24"': (
        "BGP routing table entry for 10.0.2.0/24\n"
        "Paths: (2 available, best #1, table default)\n"
        "  Advertised to non peer-group peers:\n"
        "  65002\n    10.0.12.1 from 10.0.12.1 (10.0.12.1)\n      Origin IGP, valid, external, multipath, best\n"
        "  65002\n    10.0.12.3 from 10.0.12.3 (10.0.12.3)\n      Origin IGP, valid, external, multipath\n"
    ),
    'leaf1: vtysh -c "show ip route 10.0.2.0/24"': route_2nh(),
    # --- architecture ---
    "leaf1: docker ps": (
        "CONTAINER ID   IMAGE                    STATUS         NAMES\n"
        "a1              docker-database:latest   Up 20 minutes  database\n"
        "a2              docker-swss:latest       Up 20 minutes  swss\n"
        "a3              docker-syncd:latest      Up 20 minutes  syncd\n"
        "a4              docker-fpm-frr:latest    Up 20 minutes  bgp\n"
        "a5              docker-lldp:latest       Up 20 minutes  lldp\n"
        "a6              docker-teamd:latest      Up 20 minutes  teamd\n"
    ),
    "leaf1: show feature status": (
        "Feature    State     AutoRestart\n"
        "---------  --------  -----------\n"
        "bgp        enabled   enabled\n"
        "lldp       enabled   enabled\n"
        "swss       enabled   enabled\n"
        "syncd      enabled   enabled\n"
    ),
    "leaf1: docker exec swss supervisorctl status": (
        "orchagent    RUNNING   pid 30, uptime 0:20:00\n"
        "portmgrd     RUNNING   pid 31, uptime 0:20:00\n"
        "vlanmgrd     RUNNING   pid 32, uptime 0:20:00\n"
        "neighsyncd   RUNNING   pid 33, uptime 0:20:00\n"
    ),
    "leaf1: docker exec bgp supervisorctl status": (
        "bgpd       RUNNING   pid 40, uptime 0:20:00\n"
        "zebra      RUNNING   pid 41, uptime 0:20:00\n"
        "fpmsyncd   RUNNING   pid 42, uptime 0:20:00\n"
    ),
    "leaf1: sonic-clear counters": "Cleared counters\n",
    "leaf1: sonic-clear fdb all": "FDB entries are cleared.\n",
    # --- redis reads ---
    'leaf1: sonic-db-cli CONFIG_DB keys "PORT|*"': keys(
        "PORT|Ethernet0", "PORT|Ethernet4", "PORT|Ethernet8"
    ),
    'leaf1: sonic-db-cli CONFIG_DB hgetall "PORT|Ethernet8"': "admin_status\nup\nmtu\n9100\nspeed\n40000\n",
    'leaf1: sonic-db-cli CONFIG_DB keys "VLAN*"': keys(
        "VLAN|Vlan10", "VLAN_MEMBER|Vlan10|Ethernet8"
    ),
    'leaf1: sonic-db-cli CONFIG_DB keys "BGP_NEIGHBOR*"': keys(
        "BGP_NEIGHBOR|10.0.12.1", "BGP_NEIGHBOR|10.0.12.3"
    ),
    'leaf1: sonic-db-cli CONFIG_DB keys "VLAN|Vlan30"': "\n",
    'leaf1: sonic-db-cli CONFIG_DB hget "PORT|Ethernet0" mtu': "9100\n",
    'leaf1: sonic-db-cli CONFIG_DB hget "PORT|Ethernet4" admin_status': "up\n",
    "leaf1: sonic-db-cli CONFIG_DB dbsize": "1234\n",
    'leaf1: sonic-db-cli APPL_DB keys "ROUTE_TABLE:10.0.2.0/24"': keys("ROUTE_TABLE:10.0.2.0/24"),
    'leaf1: sonic-db-cli APPL_DB keys "VLAN_TABLE:Vlan30"': "\n",
    'leaf1: sonic-db-cli APPL_DB keys "FDB_TABLE*"': "\n",
    'leaf1: sonic-db-cli APPL_DB keys "LLDP_ENTRY_TABLE*"': keys(
        "LLDP_ENTRY_TABLE:Ethernet0", "LLDP_ENTRY_TABLE:Ethernet4"
    ),
    'leaf1: sonic-db-cli APPL_DB hgetall "LLDP_ENTRY_TABLE:Ethernet0"': "remote_system_name\nleaf2\nremote_port_id\nEthernet0\n",
    'leaf1: sonic-db-cli APPL_DB hget "PORT_TABLE:Ethernet0" mtu': "9100\n",
    'leaf1: sonic-db-cli APPL_DB hget "PORT_TABLE:Ethernet4" admin_status': "up\n",
    "leaf1: sonic-db-cli APPL_DB dbsize": "2345\n",
    'leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_FDB_ENTRY*"': keys(
        'ASIC_STATE:SAI_OBJECT_TYPE_FDB_ENTRY:{"bvid":"oid:0x260000000005f4","mac":"02:00:00:00:01:10"}'
    ),
    'leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP_GROUP*"': keys(
        "ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP_GROUP:oid:0x50000000005ff"
    ),
    'leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_PORT*"': keys(
        "ASIC_STATE:SAI_OBJECT_TYPE_PORT:oid:0x10000000004a4",
        "ASIC_STATE:SAI_OBJECT_TYPE_PORT:oid:0x10000000004a5",
        "ASIC_STATE:SAI_OBJECT_TYPE_PORT:oid:0x10000000004a6",
    ),
    'leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_VLAN*"': keys(
        "ASIC_STATE:SAI_OBJECT_TYPE_VLAN:oid:0x260000000005f4"
    ),
    'leaf1: sonic-db-cli ASIC_DB hgetall "VIDTORID"': "oid:0x260000000005f4\noid:0x2600000000\n",
    "leaf1: sonic-db-cli ASIC_DB dbsize": "3456\n",
    'leaf1: sonic-db-cli STATE_DB keys "PORT_TABLE|Ethernet8"': keys("PORT_TABLE|Ethernet8"),
    'leaf1: sonic-db-cli STATE_DB hgetall "PORT_TABLE|Ethernet8"': "oper_status\nup\nspeed\n40000\n",
    'leaf1: sonic-db-cli STATE_DB hget "PORT_TABLE|Ethernet4" oper_status': "up\n",
    'leaf1: sonic-db-cli COUNTERS_DB keys "COUNTERS_PORT_NAME_MAP"': keys("COUNTERS_PORT_NAME_MAP"),
    'leaf1: sonic-db-cli COUNTERS_DB hgetall "COUNTERS_PORT_NAME_MAP"': "Ethernet0\noid:0x1000000000004\nEthernet8\noid:0x1000000000006\n",
    # --- mutating-lite observe commands ---
    "leaf1: sudo config interface shutdown Ethernet4": "",
    "leaf1: sudo config interface startup Ethernet4": "",
    "leaf1: sudo config vlan add 30": "",
    "leaf1: sudo config vlan del 30": "",
    # --- config get (chaoslab config get <node>) ---
    "leaf1: show runningconfiguration all": (
        '{\n  "DEVICE_METADATA": {"localhost": {"hostname": "leaf1", "bgp_asn": "65001"}},\n'
        '  "PORT": {"Ethernet0": {"admin_status": "up", "mtu": "9100"},\n'
        '           "Ethernet4": {"admin_status": "up", "mtu": "9100"},\n'
        '           "Ethernet8": {"admin_status": "up", "mtu": "9100"}},\n'
        '  "VLAN": {"Vlan10": {"vlanid": "10"}},\n'
        '  "VLAN_MEMBER": {"Vlan10|Ethernet8": {"tagging_mode": "untagged"}},\n'
        '  "BGP_NEIGHBOR": {"10.0.12.1": {"asn": "65002"}, "10.0.12.3": {"asn": "65002"}}\n}\n'
    ),
    "leaf2: show runningconfiguration all": (
        '{\n  "DEVICE_METADATA": {"localhost": {"hostname": "leaf2", "bgp_asn": "65002"}},\n'
        '  "PORT": {"Ethernet0": {"admin_status": "up", "mtu": "9100"},\n'
        '           "Ethernet4": {"admin_status": "up", "mtu": "9100"},\n'
        '           "Ethernet8": {"admin_status": "up", "mtu": "9100"}},\n'
        '  "VLAN": {"Vlan20": {"vlanid": "20"}},\n'
        '  "VLAN_MEMBER": {"Vlan20|Ethernet8": {"tagging_mode": "untagged"}},\n'
        '  "BGP_NEIGHBOR": {"10.0.12.0": {"asn": "65001"}, "10.0.12.2": {"asn": "65001"}}\n}\n'
    ),
}

SHUT8_IFACES = [
    ("Ethernet0", "up", "up", "9100"),
    ("Ethernet4", "up", "up", "9100"),
    ("Ethernet8", "down", "down", "9100"),
]
MTU1500_IFACES = [
    ("Ethernet0", "up", "up", "1500"),
    ("Ethernet4", "up", "up", "1500"),
    ("Ethernet8", "up", "up", "9100"),
]

OVERRIDES: dict[str, dict[str, str]] = {
    "c_shut_access": {
        "leaf1: show interfaces status": iface_rows(SHUT8_IFACES),
        "leaf1: show mac": "  No.    Vlan    MacAddress    Port    Type\nTotal number of entries 0\n",
        "leaf1: show mac -c": "Total number of entries 0\n",
        "h1: ping -c 5 10.0.1.1": ping("10.0.1.1", 5, loss=100.0),
        "h1: ping -c 5 10.0.2.10": ping("10.0.2.10", 5, loss=100.0),
        'leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_FDB_ENTRY*"': "\n",
    },
    "c_vlan_churn_50": {
        "leaf1: sonic-db-cli CONFIG_DB dbsize": "1284\n",
        "leaf1: sonic-db-cli APPL_DB dbsize": "2395\n",
        "leaf1: sonic-db-cli ASIC_DB dbsize": "3506\n",
        "leaf1: show vlan brief": (
            "+-------+-------------+-----------+----------------+\n"
            "|  VLAN |  IP Address |     Ports |   Port Tagging |\n"
            "+-------+-------------+-----------+----------------+\n"
            "|    10 | 10.0.1.1/24 | Ethernet8 |       untagged |\n"
            "|   100 |             |           |                |\n"
            "|   149 |             |           |                |\n"
            "+-------+-------------+-----------+----------------+\n"
        ),
    },
    "c_shut_one_link": {
        "leaf1: show bgp summary": bgp_summary(
            [("10.0.12.1", "65002", "00:00:12", "Active"), ("10.0.12.3", "65002", "00:09:50", "1")],
            "10.0.12.0",
            "65001",
        ),
        "leaf1: show ip route 10.0.2.0/24": route_1nh(),
        "leaf1: show interfaces status": iface_rows(
            [
                ("Ethernet0", "down", "down", "9100"),
                ("Ethernet4", "up", "up", "9100"),
                ("Ethernet8", "up", "up", "9100"),
            ]
        ),
        "leaf2: show bgp summary": bgp_summary(
            [("10.0.12.0", "65001", "00:00:12", "Active"), ("10.0.12.2", "65001", "00:09:50", "1")],
            "10.0.12.1",
            "65002",
        ),
    },
    "c_mtu1500_all_links": {
        "leaf1: show interfaces status": iface_rows(MTU1500_IFACES),
        "leaf2: show interfaces status": iface_rows(MTU1500_IFACES),
        "h1: ping -M do -s 8972 -c 5 10.0.2.10": ping("10.0.2.10", 5, 8972, loss=100.0),
    },
}


def main() -> int:
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    for spec, output in DEFAULTS.items():
        (MOCK_DIR / f"{slugify_spec(spec)}.txt").write_text(output)
    for chaos_id, mapping in OVERRIDES.items():
        chaos_dir = MOCK_DIR / chaos_id
        chaos_dir.mkdir(parents=True, exist_ok=True)
        for spec, output in mapping.items():
            (chaos_dir / f"{slugify_spec(spec)}.txt").write_text(output)

    missing: list[str] = []
    for lesson in loader.load_all(REPO_ROOT / "lessons"):
        commands = lesson.lesson.commands
        for spec in [
            *commands.baseline,
            *commands.after_chaos,
            *(c for v in commands.per_step.values() for c in v),
        ]:
            if spec not in DEFAULTS:
                missing.append(spec)
    if missing:
        print("MISSING DEFAULT FIXTURES:", file=sys.stderr)
        for spec in sorted(set(missing)):
            print(f"  {spec}", file=sys.stderr)
        return 1
    print(f"wrote {len(DEFAULTS)} default fixtures and {len(OVERRIDES)} chaos override sets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
