# SPDX-License-Identifier: Apache-2.0
"""Metrics: parse raw command output into typed facts, snapshot, and diff.

Parsing is deterministic (PRODUCT.md §4: code computes facts). Every parser is tolerant and
falls back to leaving a fact absent rather than raising. ``collect_snapshot`` reduces a set of
command results to a flat, comparable value map; ``diff_snapshots`` turns two snapshots into a
human-readable ``changed_facts`` list for the diff panel and grounded explanations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from .adapter import CommandResult

_IP = r"\d+\.\d+\.\d+\.\d+"


class InterfaceStatus(BaseModel):
    name: str
    oper: str
    admin: str
    mtu: str = ""
    speed: str = ""


class BgpNeighbor(BaseModel):
    neighbor: str
    remote_as: str
    state: str
    pfx_rcd: str


class RouteEntry(BaseModel):
    prefix: str
    source: str
    next_hops: list[str] = []


class PingResult(BaseModel):
    dest: str
    size: int
    df: bool
    loss_pct: float
    transmitted: int = 0
    received: int = 0
    local_error: bool = False


class MacEntry(BaseModel):
    vlan: str
    mac: str
    port: str
    type: str


class LldpNeighbor(BaseModel):
    local_port: str
    remote_device: str
    remote_port: str


class VlanInfo(BaseModel):
    vlan_id: str
    ip: str
    members: list[str] = []


@dataclass
class Snapshot:
    """A comparable capture of the lab: flat values for diffing, structured facts for context."""

    values: dict[str, str] = field(default_factory=dict)
    structured: dict[str, object] = field(default_factory=dict)


def parse_interface_status(text: str) -> list[InterfaceStatus]:
    rows: list[InterfaceStatus] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(Ethernet\d+)\s+", line)
        if not match:
            continue
        cells = line.split()
        opers = [c for c in cells if c.lower() in ("up", "down")]
        mtu = next((c for c in cells if c.isdigit() and 500 <= int(c) <= 20000), "")
        rows.append(
            InterfaceStatus(
                name=match.group(1),
                oper=opers[0].lower() if opers else "",
                admin=opers[-1].lower() if opers else "",
                mtu=mtu,
            )
        )
    return rows


def parse_bgp_summary(text: str) -> list[BgpNeighbor]:
    rows: list[BgpNeighbor] = []
    for line in text.splitlines():
        match = re.match(rf"^\s*({_IP})\s+\d+\s+(\d+)\s+.*\s+(\S+)\s*$", line)
        if not match:
            continue
        last = match.group(3)
        state = "Established" if last.isdigit() else last
        pfx = last if last.isdigit() else "0"
        rows.append(
            BgpNeighbor(neighbor=match.group(1), remote_as=match.group(2), state=state, pfx_rcd=pfx)
        )
    return rows


def parse_ip_route(text: str) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    current: RouteEntry | None = None
    for line in text.splitlines():
        head = re.match(rf"^([KCSBOIL])[>\*\s]*\s+({_IP}/\d+)", line)
        if head:
            if current:
                entries.append(current)
            current = RouteEntry(prefix=head.group(2), source=head.group(1), next_hops=[])
            current.next_hops.extend(re.findall(rf"via ({_IP})", line))
        elif current and ("via" in line or line.strip().startswith("*")):
            current.next_hops.extend(re.findall(rf"via ({_IP})", line))
    if current:
        entries.append(current)
    return entries


def parse_ping(command: str, text: str) -> PingResult:
    dest_match = re.findall(_IP, command)
    dest = dest_match[-1] if dest_match else "?"
    size = int(m.group(1)) if (m := re.search(r"-s\s+(\d+)", command)) else 0
    df = "-M do" in command
    if re.search(r"Message too long|local error", text, re.IGNORECASE):
        return PingResult(dest=dest, size=size, df=df, loss_pct=100.0, local_error=True)
    stats = re.search(
        r"(\d+) packets transmitted, (\d+)(?: packets)? received, ([\d.]+)% packet loss", text
    )
    if not stats:
        return PingResult(dest=dest, size=size, df=df, loss_pct=100.0)
    tx, rx = int(stats.group(1)), int(stats.group(2))
    return PingResult(
        dest=dest, size=size, df=df, loss_pct=float(stats.group(3)), transmitted=tx, received=rx
    )


def parse_mac_table(text: str) -> tuple[list[MacEntry], int | None]:
    entries: list[MacEntry] = []
    count: int | None = None
    for line in text.splitlines():
        total = re.search(r"Total number of entries\s+(\d+)", line)
        if total:
            count = int(total.group(1))
            continue
        match = re.match(r"^\s*\d+\s+(\d+)\s+([0-9a-fA-F:]{17})\s+(\S+)\s+(\S+)", line)
        if match:
            entries.append(
                MacEntry(
                    vlan=match.group(1),
                    mac=match.group(2).lower(),
                    port=match.group(3),
                    type=match.group(4),
                )
            )
    if count is None and entries:
        count = len(entries)
    return entries, count


def parse_lldp_table(text: str) -> list[LldpNeighbor]:
    rows: list[LldpNeighbor] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(Ethernet\d+)\s+(\S+)\s+(\S+)", line)
        if match:
            rows.append(
                LldpNeighbor(
                    local_port=match.group(1),
                    remote_device=match.group(2),
                    remote_port=match.group(3),
                )
            )
    return rows


def parse_vlan_brief(text: str) -> list[VlanInfo]:
    rows: list[VlanInfo] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        members = [cells[2]] if cells[2] else []
        rows.append(VlanInfo(vlan_id=cells[0], ip=cells[1], members=members))
    return rows


def _redis_key(command: str) -> str:
    db = next((tok for tok in command.split() if tok.endswith("_DB")), "DB")
    if "dbsize" in command:
        return f"redis:{db}:dbsize"
    pattern = m.group(1) if (m := re.search(r'"([^"]+)"', command)) else "keys"
    slug = re.sub(r"[^A-Za-z0-9]+", "_", pattern).strip("_")
    return f"redis:{db}:{slug}"


def _redis_value(command: str, text: str) -> str:
    body = text.strip()
    if "dbsize" in command:
        digits = re.search(r"\d+", body)
        return digits.group(0) if digits else "0"
    if "hget " in command:
        return body.splitlines()[0].strip() if body else ""
    lines = [line for line in body.splitlines() if line.strip() and not line.startswith("(mock:")]
    return str(len(lines))


def collect_snapshot(results: list[CommandResult]) -> Snapshot:
    """Parse every recognised command output into a comparable snapshot.

    Every fact key is prefixed with the source target so probes of the same table on
    different nodes (e.g. interface status on both leafs) never collide.
    """
    snapshot = Snapshot()
    for result in results:
        command = result.command
        node = result.target
        if command.startswith("show interfaces status"):
            for iface in parse_interface_status(result.raw):
                snapshot.values[f"{node}:iface:{iface.name}:oper"] = iface.oper
                snapshot.values[f"{node}:iface:{iface.name}:admin"] = iface.admin
                if iface.mtu:
                    snapshot.values[f"{node}:iface:{iface.name}:mtu"] = iface.mtu
            snapshot.structured.setdefault(f"{node}:interfaces", []).extend(
                i.model_dump() for i in parse_interface_status(result.raw)
            )
        elif command.startswith("show bgp summary"):
            for nbr in parse_bgp_summary(result.raw):
                snapshot.values[f"{node}:bgp:{nbr.neighbor}:state"] = nbr.state
                snapshot.values[f"{node}:bgp:{nbr.neighbor}:pfx_rcd"] = nbr.pfx_rcd
        elif "show ip route" in command:
            routes = parse_ip_route(result.raw)
            for route in routes:
                snapshot.values[f"{node}:route:{route.prefix}:next_hops"] = ",".join(
                    sorted(route.next_hops)
                )
                snapshot.values[f"{node}:route:{route.prefix}:source"] = route.source
            if command.strip().endswith("show ip route"):
                snapshot.values[f"{node}:route_count"] = str(len(routes))
        elif command.startswith("ping"):
            ping = parse_ping(command, result.raw)
            key = f"{node}:ping:{ping.dest}:s{ping.size}:{'df' if ping.df else 'nodf'}"
            snapshot.values[key] = f"{ping.loss_pct:g}% loss"
        elif command == "show mac" or command.startswith("show mac -c"):
            entries, count = parse_mac_table(result.raw)
            if count is not None:
                snapshot.values[f"{node}:mac_count"] = str(count)
            for entry in entries:
                snapshot.values[f"{node}:mac:{entry.mac}:port"] = entry.port
                snapshot.values[f"{node}:mac:{entry.mac}:vlan"] = entry.vlan
        elif command.startswith("show lldp table"):
            neighbors = parse_lldp_table(result.raw)
            snapshot.values[f"{node}:lldp_count"] = str(len(neighbors))
            for nbr in neighbors:
                snapshot.values[f"{node}:lldp:{nbr.local_port}:remote"] = nbr.remote_device
        elif command.startswith("show vlan brief"):
            for vlan in parse_vlan_brief(result.raw):
                snapshot.values[f"{node}:vlan:{vlan.vlan_id}:members"] = ",".join(vlan.members)
                snapshot.values[f"{node}:vlan:{vlan.vlan_id}:ip"] = vlan.ip
        elif command.startswith("sonic-db-cli"):
            snapshot.values[f"{node}:{_redis_key(command)}"] = _redis_value(command, result.raw)
    return snapshot


def diff_snapshots(before: Snapshot, after: Snapshot) -> list[str]:
    """Return human-readable changed facts (before → after)."""
    changed: list[str] = []
    for key in sorted(set(before.values) | set(after.values)):
        old = before.values.get(key)
        new = after.values.get(key)
        if old == new:
            continue
        changed.append(
            f"{key}: {old if old is not None else '(absent)'} → "
            f"{new if new is not None else '(absent)'}"
        )
    return changed
