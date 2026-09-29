# SPDX-License-Identifier: Apache-2.0
"""Metrics tests: parsers on healthy + broken fixtures, and snapshot diffing."""

from __future__ import annotations

import pytest

from chaoslab.lab import metrics
from chaoslab.lab.adapter import MockAdapter

BGP_AFTER = [
    "leaf1: show bgp summary",
    "leaf1: show ip route 10.0.2.0/24",
    "leaf1: show interfaces status",
    'leaf1: sonic-db-cli APPL_DB keys "ROUTE_TABLE:10.0.2.0/24"',
    "h1: ping -c 10 10.0.2.10",
    "leaf2: show bgp summary",
]


@pytest.fixture
def adapter(repo_root):
    return MockAdapter(repo_root / "tests" / "fixtures" / "mock")


def test_parse_bgp_summary_healthy(adapter):
    neighbors = metrics.parse_bgp_summary(adapter.run("leaf1: show bgp summary").raw)
    assert {n.neighbor for n in neighbors} == {"10.0.12.1", "10.0.12.3"}
    assert all(n.state == "Established" and n.pfx_rcd == "1" for n in neighbors)


def test_parse_bgp_summary_broken(adapter):
    adapter.on_inject("c_shut_one_link")
    neighbors = {
        n.neighbor: n for n in metrics.parse_bgp_summary(adapter.run("leaf1: show bgp summary").raw)
    }
    assert neighbors["10.0.12.1"].state == "Active"
    assert neighbors["10.0.12.3"].state == "Established"


def test_parse_ip_route_next_hops(adapter):
    healthy = metrics.parse_ip_route(adapter.run("leaf1: show ip route 10.0.2.0/24").raw)
    assert healthy[0].prefix == "10.0.2.0/24"
    assert healthy[0].next_hops == ["10.0.12.1", "10.0.12.3"]
    adapter.on_inject("c_shut_one_link")
    broken = metrics.parse_ip_route(adapter.run("leaf1: show ip route 10.0.2.0/24").raw)
    assert broken[0].next_hops == ["10.0.12.3"]


def test_parse_interface_status_down(adapter):
    adapter.on_inject("c_shut_access")
    ifaces = {
        i.name: i
        for i in metrics.parse_interface_status(adapter.run("leaf1: show interfaces status").raw)
    }
    assert ifaces["Ethernet8"].oper == "down"
    assert ifaces["Ethernet0"].oper == "up"


def test_parse_ping_loss(adapter):
    healthy = metrics.parse_ping(
        "h1: ping -c 5 10.0.2.10", adapter.run("h1: ping -c 5 10.0.2.10").raw
    )
    assert healthy.loss_pct == 0.0
    adapter.on_inject("c_shut_access")
    broken = metrics.parse_ping(
        "h1: ping -c 5 10.0.2.10", adapter.run("h1: ping -c 5 10.0.2.10").raw
    )
    assert broken.loss_pct == 100.0


def test_parse_ping_local_error():
    text = "PING 10.0.2.10 (10.0.2.10): 8972 data bytes\nping: local error: Message too long, mtu=1500\n"
    result = metrics.parse_ping("h1: ping -M do -s 8972 -c 3 10.0.2.10", text)
    assert result.local_error and result.loss_pct == 100.0


def test_parse_mac_table(adapter):
    entries, count = metrics.parse_mac_table(adapter.run("leaf1: show mac").raw)
    assert count == 1
    assert entries[0].mac == "02:00:00:00:01:10"
    assert entries[0].port == "Ethernet8"


def test_snapshot_diff_detects_chaos(adapter):
    before = metrics.collect_snapshot(adapter.run_many(BGP_AFTER))
    adapter.on_inject("c_shut_one_link")
    after = metrics.collect_snapshot(adapter.run_many(BGP_AFTER))
    changed = metrics.diff_snapshots(before, after)
    joined = "\n".join(changed)
    assert "leaf1:bgp:10.0.12.1:state" in joined
    assert "route:10.0.2.0/24:next_hops" in joined
    assert "iface:Ethernet0:oper" in joined


def test_snapshot_no_change_is_empty(adapter):
    before = metrics.collect_snapshot(adapter.run_many(BGP_AFTER))
    after = metrics.collect_snapshot(adapter.run_many(BGP_AFTER))
    assert metrics.diff_snapshots(before, after) == []
