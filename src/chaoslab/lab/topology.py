# SPDX-License-Identifier: Apache-2.0
"""Topology manager: containerlab deploy / verify / destroy, plus baseline reset.

Shell access is confined to this module and ``adapter.py`` (PRODUCT.md §8.1). Every operation
degrades gracefully with a clear message when docker or containerlab is unavailable, so the CLI
never hangs and ``make lab-*`` is safe to run anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .adapter import DeviceAdapter, DockerAdapter

CLAB = "containerlab"
LAB_NAME = "chaoslab"
LEAVES = ("leaf1", "leaf2")
HOSTS = ("h1", "h2", "h3", "h4")
LESSON_PORTS = ("Ethernet0", "Ethernet4", "Ethernet8", "Ethernet12")
BASELINE_DB = "/etc/sonic/baseline_config_db.json"
BASELINE_FRR = "/etc/sonic/frr_baseline.conf"
_READY_TIMEOUT_S = 420  # first boot to a working SONiC CLI takes ~4 min on a vCPU host
_POLL_INTERVAL_S = 5

# The image ships no sudo but lessons author `sudo config ...` verbatim (true on real switches);
# docker exec is already root, so a pass-through shim keeps lesson commands working. Two mappings:
# `sudo docker` is swallowed (show CLI shells it; no nested docker) and `sudo rvtysh` execs vtysh
# (sonic-utilities' restricted vtysh wrapper is absent on vs — it breaks `show ip route`).
_SUDO_SHIM = (
    "if ! [ -x /usr/local/bin/sudo ] || ! grep -q rvtysh /usr/local/bin/sudo; then "
    'printf \'#!/bin/sh\\n[ "$1" = docker ] && exit 0\\n'
    '[ "$1" = rvtysh ] && { shift; exec vtysh "$@"; }\\nexec "$@"\\n\' >/usr/local/bin/sudo '
    "&& chmod 755 /usr/local/bin/sudo; fi"
)


def topo_file() -> Path:
    return Path(os.environ.get("CHAOSLAB_TOPO", "topo/chaoslab.clab.yml"))


def sonic_image() -> str:
    return os.environ.get("CHAOSLAB_SONIC_IMAGE", "docker-sonic-vs:latest")


def image_present() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", sonic_image()],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def describe() -> dict:
    """Static topology description (nodes, links, IPs/ASNs) for the API and UI diagram (§7)."""
    return {
        "nodes": [
            {
                "name": "leaf1",
                "kind": "sonic-vs",
                "asn": 65001,
                "svi": "Vlan10 10.0.1.1/24",
                "loopback": "10.0.0.1/32",
            },
            {
                "name": "leaf2",
                "kind": "sonic-vs",
                "asn": 65002,
                "svi": "Vlan20 10.0.2.1/24",
                "loopback": "10.0.0.2/32",
            },
            {
                "name": "h1",
                "kind": "linux",
                "ip": "10.0.1.10/24",
                "vlan": "Vlan10",
                "gw": "10.0.1.1",
                "mac": "02:00:00:00:01:10",
            },
            {
                "name": "h2",
                "kind": "linux",
                "ip": "10.0.1.11/24",
                "vlan": "Vlan10",
                "gw": "10.0.1.1",
                "mac": "02:00:00:00:01:11",
            },
            {
                "name": "h3",
                "kind": "linux",
                "ip": "10.0.2.10/24",
                "vlan": "Vlan20",
                "gw": "10.0.2.1",
                "mac": "02:00:00:00:02:10",
            },
            {
                "name": "h4",
                "kind": "linux",
                "ip": "10.0.2.11/24",
                "vlan": "Vlan20",
                "gw": "10.0.2.1",
                "mac": "02:00:00:00:02:11",
            },
        ],
        "links": [
            {
                "a": "leaf1:Ethernet0",
                "b": "leaf2:Ethernet0",
                "subnet": "10.0.12.0/31",
                "role": "ebgp",
            },
            {
                "a": "leaf1:Ethernet4",
                "b": "leaf2:Ethernet4",
                "subnet": "10.0.12.2/31",
                "role": "ebgp",
            },
            {"a": "leaf1:Ethernet8", "b": "h1:eth1", "role": "access"},
            {"a": "leaf1:Ethernet12", "b": "h2:eth1", "role": "access"},
            {"a": "leaf2:Ethernet8", "b": "h3:eth1", "role": "access"},
            {"a": "leaf2:Ethernet12", "b": "h4:eth1", "role": "access"},
        ],
        "mtu": 9100,
    }


def mgmt_ips() -> dict[str, str]:
    """Live management IPs from docker inspect; empty when docker/lab is absent."""
    ips: dict[str, str] = {}
    template = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
    for node in [item["name"] for item in describe()["nodes"]]:
        try:
            proc = subprocess.run(
                ["docker", "inspect", "-f", template, f"clab-{LAB_NAME}-{node}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        if proc.returncode == 0 and proc.stdout.strip():
            ips[node] = proc.stdout.strip()
    return ips


def link_states(adapter: DeviceAdapter) -> dict[str, str]:
    """Per-link carrier state via kernel LOWER_UP on both endpoints.

    The vs image's `show interfaces status` Oper column reads down even on forwarding
    links, so the kernel carrier flag is the truthful source.
    """
    states: dict[str, str] = {}
    for link in describe()["links"]:
        up = True
        for endpoint in (link["a"], link["b"]):
            node, device = endpoint.split(":", 1)
            result = adapter.run(f"{node}: ip link show {device}")
            if not (result.ok and "LOWER_UP" in result.raw):
                up = False
        states[f"{link['a']} ~ {link['b']}"] = "up" if up else "down"
    return states


def preflight() -> tuple[bool, str]:
    """Check that docker, containerlab, the topology file, and the sonic-vs image are present."""
    if shutil.which("docker") is None:
        return False, "docker not found (install Docker to run the lab)"
    if shutil.which(CLAB) is None:
        return False, "containerlab not found (run 'make lab-bootstrap'; the lab is x86-only)"
    if not topo_file().exists():
        return False, f"topology file not found: {topo_file()}"
    if not image_present():
        return False, f"SONiC image {sonic_image()!r} not found (run 'make lab-bootstrap')"
    return True, ""


def _run(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, check=False).returncode
    except FileNotFoundError as exc:
        print(f"command not available: {exc}")
        return 1


def deploy() -> int:
    """Deploy the topology, poll until both leafs answer, then apply the baselines."""
    ok, message = preflight()
    if not ok:
        print(f"Cannot deploy: {message}")
        return 1
    code = _run([CLAB, "deploy", "-t", str(topo_file())])
    if code != 0:
        return code
    if not wait_ready():
        return 1
    if not apply_baseline(DockerAdapter()):
        return 1
    print("Baselines applied; lab is ready.")
    return 0


def destroy() -> int:
    """Destroy the topology if containerlab is present (idempotent)."""
    if shutil.which(CLAB) is None:
        print("containerlab not found; nothing to destroy")
        return 0
    if not topo_file().exists():
        print("topology file not found; nothing to destroy")
        return 0
    return _run([CLAB, "destroy", "-t", str(topo_file()), "--cleanup"])


def node_ready(node: str) -> bool:
    """True once the port stack finished initializing AND the CLI answers.

    The CLI/redis answer seconds after container start, long before portsyncd finishes — probing
    the CLI alone lets the baseline apply mid-boot, and APPL_DB then shows default port config
    until init completes. ``PORT_TABLE:PortInitDone`` is SONiC's own port-stack-ready marker.
    """
    container = f"clab-{LAB_NAME}-{node}"
    probe = (
        'test "$(sonic-db-cli APPL_DB EXISTS PORT_TABLE:PortInitDone 2>/dev/null)" = "1" '
        "&& show interfaces status >/dev/null 2>&1"
    )
    try:
        proc = subprocess.run(
            ["docker", "exec", container, "bash", "-lc", probe],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def wait_ready(timeout: int = _READY_TIMEOUT_S) -> bool:
    """Poll until both leafs respond to the SONiC CLI, or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(node_ready(node) for node in LEAVES):
            print("Both leafs are answering the SONiC CLI.")
            return True
        print("Waiting for leafs to come up (first boot takes ~4 min)...")
        time.sleep(_POLL_INTERVAL_S)
    print("Timed out waiting for leafs.")
    return False


def _baseline_ip_adds(node: str) -> list[str]:
    """Interface-IP re-adds from the node's baseline JSON.

    ``config load`` races netdev creation on first boot — intfmgrd silently drops IPs for
    not-yet-existing Vlan devices — so every IP is re-added explicitly (idempotent).
    """
    path = topo_file().parent / "configs" / f"{node}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    commands: list[str] = []
    for table in ("VLAN_INTERFACE", "INTERFACE", "LOOPBACK_INTERFACE"):
        for key in data.get(table, {}):
            if "|" in key:
                name, ip = key.split("|", 1)
                commands.append(f"config interface ip add {name} {ip}")
    return commands


def apply_baseline(adapter: DeviceAdapter) -> bool:
    """Apply the bound CONFIG_DB + FRR baselines on a running lab (deploy AND §7 reset path).

    docker-sonic-vs has no bgpcfgd and no usable ``config reload`` (no systemd/sudo in the
    container), so the baseline is merged live: ``config load`` for CONFIG_DB, ``vtysh -f``
    for BGP, then explicit lesson-port startup. Idempotent; runs through the adapter so
    ssh lab mode works.
    """
    trim_logs = "sh -c 'truncate -s 0 /var/log/swss/*.rec* /var/log/syslog* 2>/dev/null || true'"
    # sonic-vs writes ~1.9 GB/leaf of recordings within hours — enough to fill a small host disk
    # and wedge the data plane; a tiny in-container loop keeps them truncated between resets.
    trim_script = (
        "sh -c 'printf \"#!/bin/sh\\nwhile :; do truncate -s 0 /var/log/swss/*.rec* "
        "/var/log/syslog* 2>/dev/null; sleep 300; done\\n\" >/usr/local/bin/chaoslab-rec-trim "
        "&& chmod 755 /usr/local/bin/chaoslab-rec-trim'"
    )
    trim_daemon = (
        "sh -c 'pgrep -f chaoslab-rec-trim >/dev/null || "
        "nohup /usr/local/bin/chaoslab-rec-trim >/dev/null 2>&1 &'"
    )
    steps_ok = True
    for node in LEAVES:
        steps: list[tuple[str, int, bool]] = [
            (f"{node}: {_SUDO_SHIM}", 30, True),
            # Default arp_ignore=0 lets the raw clab veths (ethN) answer ARP for the SVI
            # and uplink IPs with their own MAC, poisoning peer caches so traffic bypasses
            # the tap dataplane and flaps between paths. arp_ignore=1 keeps replies on the
            # interface that owns the address.
            (
                f"{node}: sysctl -w net.ipv4.conf.all.arp_ignore=1 "
                "net.ipv4.conf.default.arp_ignore=1",
                30,
                False,
            ),
            (f"{node}: {trim_logs}", 30, False),
            (f"{node}: {trim_script}", 30, False),
            (f"{node}: {trim_daemon}", 30, False),
            (f"{node}: supervisorctl start bgpd", 30, False),  # rc!=0 when already running
            (f"{node}: vtysh -f {BASELINE_FRR}", 60, True),
            (f"{node}: supervisorctl start arp_update", 30, False),
        ]
        for spec, step_timeout, required in steps:
            result = adapter.run(spec, timeout=step_timeout)
            if required and not result.ok:
                print(f"baseline step failed — {spec}: {result.raw.strip()[:200]}")
                steps_ok = False
        converged = _assert_until_consumed(adapter, node)
        if not converged:
            print(f"{node}: baseline never reflected in APPL_DB — check the lab")
        print(f"{node}: baseline {'applied' if steps_ok and converged else 'INCOMPLETE'}")
        steps_ok = steps_ok and converged
    for host in HOSTS:
        adapter.run(f"{host}: ip neigh flush dev eth1")  # drop pre-arp_ignore poisoned entries
    dataplane_ok = verify_dataplane(adapter)
    if steps_ok and not _wait_bgp_established(adapter):
        print("note: BGP sessions not Established yet (connect-retry can take ~2 min)")
    if not dataplane_ok:
        # config-load churn wedges vs pumps and re-arms can land late — recheck after
        # the BGP wait gave the port stack time to settle.
        dataplane_ok = verify_dataplane(adapter)
    return steps_ok and dataplane_ok


_ASSERT_ROUNDS = 30
_ASSERT_INTERVAL_S = 8


# Adjacency probes and the raw veths to bounce when one is dead. Two verified vs
# failure modes: (1) orchagent HOSTIF churn (repeated config loads / syncd restarts)
# kills the tap↔veth pumps permanently — only a redeploy fixes that; (2) transient
# pump stalls after admin flaps — a veth bounce re-arms those. arp_ignore=1 (baseline)
# removes the ARP-race masking that used to make these look intermittent.
_DATAPLANE_CHECKS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("h1: ping -c 2 -W 1 10.0.1.1", (("leaf1", "eth3"),)),
    ("h2: ping -c 2 -W 1 10.0.1.1", (("leaf1", "eth4"),)),
    ("h3: ping -c 2 -W 1 10.0.2.1", (("leaf2", "eth3"),)),
    ("h4: ping -c 2 -W 1 10.0.2.1", (("leaf2", "eth4"),)),
    ("leaf1: ping -c 2 -W 1 10.0.12.1", (("leaf1", "eth1"), ("leaf2", "eth1"))),
    ("leaf1: ping -c 2 -W 1 10.0.12.3", (("leaf1", "eth2"), ("leaf2", "eth2"))),
)


def _ping_ok(adapter: DeviceAdapter, spec: str) -> bool:
    result = adapter.run(spec, timeout=30)
    return result.ok and " 0% packet loss" in result.raw


# `bridge fdb flush dev Bridge` (no qualifier) deletes the bridge's own *local* entry —
# the row that delivers gateway-addressed frames up to the SVI — silently blackholing
# every host→gateway path while learning still works. Verified on-lab; re-adding the
# entry heals instantly. Idempotent re-assert for every Vlan SVI on the leaf.
_LOCAL_FDB_FIX = (
    "sh -c 'mac=$(cat /sys/class/net/Bridge/address); "
    "for v in /sys/class/net/Vlan*; do vid=${v##*/Vlan}; "
    'bridge fdb replace "$mac" dev Bridge self local vlan "$vid"; done 2>/dev/null; true\''
)


def verify_dataplane(adapter: DeviceAdapter) -> bool:
    """Ping every adjacency; bounce the raw veth under any dead path (see _DATAPLANE_CHECKS).

    Bounces are conditional — an uplink veth bounce drops the BGP session, so only dead
    paths are touched and the caller's BGP wait covers reconvergence. The pump re-arm can
    take well over 10 s when the port stack was just churned, so bounce+probe is retried.
    """
    for node in LEAVES:
        adapter.run(f"{node}: {_LOCAL_FDB_FIX}")
    healthy = True
    for probe, veths in _DATAPLANE_CHECKS:
        if _ping_ok(adapter, probe):
            continue
        healed = False
        for _attempt in range(3):
            for node, veth in veths:
                adapter.run(
                    f"{node}: sh -c 'ip link set {veth} down; sleep 1; ip link set {veth} up'",
                    timeout=30,
                )
            deadline = time.monotonic() + 15
            while not healed and time.monotonic() < deadline:
                time.sleep(4)
                healed = _ping_ok(adapter, probe)
            if healed:
                break
        if healed:
            print(f"lab health: path ok after virtual-cable bounce — {probe}")
        else:
            print(
                f"lab health: path still down — {probe} — if this persists, "
                "run 'make lab-reset' to rebuild the lab"
            )
            healthy = False
    return healthy


def _assert_until_consumed(adapter: DeviceAdapter, node: str) -> bool:
    """Assert port/IP baseline config until APPL_DB proves it was consumed.

    Config written while the port stack is still initializing is silently lost (no replay on
    this image). The full ``config load`` runs rarely — verified on-lab: repeated loads make
    orchagent re-create HOSTIFs, which fails (SAI_STATUS_FAILURE) and permanently kills the
    tap↔veth pumps. Light idempotent asserts (startup + ip add) run every round instead.
    """
    check_spec = f'{node}: sonic-db-cli APPL_DB hget "PORT_TABLE:Ethernet0" admin_status'
    for round_no in range(_ASSERT_ROUNDS):
        if round_no % 10 == 0:
            adapter.run(f"{node}: config load {BASELINE_DB} -y", timeout=120)
        for port in LESSON_PORTS:
            adapter.run(f"{node}: config interface startup {port}")
        for command in _baseline_ip_adds(node):
            adapter.run(f"{node}: {command}")
        result = adapter.run(check_spec)
        if result.ok and "up" in result.raw:
            return True
        if round_no == 0:
            print(f"{node}: waiting for the port stack to consume the baseline...")
        time.sleep(_ASSERT_INTERVAL_S)
    return False


_BGP_WAIT_S = 180


def _wait_bgp_established(adapter: DeviceAdapter, timeout: int = _BGP_WAIT_S) -> bool:
    """Poll until both eBGP sessions are Established (connect-retry needs up to ~2 min)."""
    spec = 'leaf1: vtysh -c "show bgp summary"'
    deadline = time.monotonic() + timeout
    announced = False
    while time.monotonic() < deadline:
        result = adapter.run(spec)
        if result.ok and result.raw.count(" 65002 ") >= 2 and "Active" not in result.raw:
            established = all(
                any(ch.isdigit() for ch in line.split()[-3])
                for line in result.raw.splitlines()
                if line.strip().startswith(("10.0.12.1", "10.0.12.3"))
            )
            if established:
                print("BGP sessions Established on both uplinks.")
                return True
        if not announced:
            print("Waiting for BGP sessions to establish (up to ~2 min)...")
            announced = True
        time.sleep(_POLL_INTERVAL_S)
    return False


def status() -> int:
    """Print containerlab inspect output and per-node readiness."""
    ok, message = preflight()
    if not ok:
        print(f"Lab not available: {message}")
        return 1
    _run([CLAB, "inspect", "-t", str(topo_file())])
    for node in LEAVES:
        print(f"  {node}: {'ready' if node_ready(node) else 'not ready'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    action = args[0] if args else "status"
    return {"up": deploy, "down": destroy, "status": status}.get(action, status)()


if __name__ == "__main__":
    raise SystemExit(main())
