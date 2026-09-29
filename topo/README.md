# Lab topology (Containerlab)

Four devices — two `sonic-vs` leafs and two Alpine hosts — with two parallel `/31` eBGP links
(ECMP) between the leafs and one access host per leaf.

```
h1 ── leaf1 ══════ leaf2 ── h2
       Ethernet8   Ethernet0/4   Ethernet8
leaf1 AS 65001                    leaf2 AS 65002
```

| Node  | Kind      | Key addressing |
|-------|-----------|----------------|
| leaf1 | sonic-vs  | AS 65001 · Ethernet0 `10.0.12.0/31` · Ethernet4 `10.0.12.2/31` · Vlan10 `10.0.1.1/24` · advertises `10.0.1.0/24` |
| leaf2 | sonic-vs  | AS 65002 · Ethernet0 `10.0.12.1/31` · Ethernet4 `10.0.12.3/31` · Vlan20 `10.0.2.1/24` · advertises `10.0.2.0/24` |
| h1    | alpine    | `10.0.1.10/24` gw `10.0.1.1`, MAC `02:00:00:00:01:10`, MTU 9100 |
| h2    | alpine    | `10.0.2.10/24` gw `10.0.2.1`, MAC `02:00:00:00:02:10`, MTU 9100 |

Containerlab endpoint `ethN` maps to SONiC `Ethernet(4·(N-1))`: `eth1→Ethernet0`, `eth2→Ethernet4`,
`eth3→Ethernet8`. BGP timers are set DC-style (keepalive 3 s / hold 10 s) with fast external
failover for demo-friendly reconvergence.

## Host requirements (x86 only)

`docker-sonic-vs` images are **amd64-only** and each leaf needs ~2 GB RAM. Run the lab on an
**x86 Linux host** with Docker and ~8 GB free RAM. On macOS / Apple Silicon, run the lab on an
x86 Ubuntu cloud VM or GitHub Codespaces; the Python app can run anywhere and reach the lab (the
device adapter isolates shell access, so an SSH target can be added).

## Deploy / verify / destroy

```bash
# from the repo root
make lab-up       # containerlab deploy + poll until both leafs answer the SONiC CLI
make lab-status   # containerlab inspect + per-node readiness
make lab-down     # containerlab destroy --cleanup (idempotent)
```

Or directly:

```bash
cd topo
containerlab deploy -t chaoslab.clab.yml
containerlab inspect -t chaoslab.clab.yml
containerlab destroy -t chaoslab.clab.yml --cleanup
```

## Baseline configs

`configs/leaf1.json` and `configs/leaf2.json` are CONFIG_DB baselines bound to
`/etc/sonic/config_db.json` at boot: ports at MTU 9100, the access VLAN and gateway SVI, the two
eBGP neighbors with 3/10 timers, and the host-subnet network origination.

> Verify-on-lab: exact BGP origination (network statement vs redistribute-connected), negotiated
> timers, and fast-fallover behavior should be confirmed on the running `sonic-vs` image and
> adjusted here if the branch defaults differ (this mirrors the `[VERIFY-ON-LAB]` markers in the
> lessons). **Reset = re-apply baseline + `config interface startup`, never redeploy.**
