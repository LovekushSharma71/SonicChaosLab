# Lab topology (Containerlab)

Six devices — two `sonic-vs` leafs and four Alpine hosts — with two parallel `/31` eBGP links
(ECMP) between the leafs and two access hosts per leaf.

```
h1 ── leaf1 ══════ leaf2 ── h4
        |            |
        h2           h3

leaf1 AS 65001  (Ethernet0/4 = /31 uplinks)  leaf2 AS 65002
access: Ethernet8 = h1/h3, Ethernet12 = h2/h4
```

| Node  | Kind      | Key addressing |
|-------|-----------|----------------|
| leaf1 | sonic-vs  | AS 65001 · Ethernet0 `10.0.12.0/31` · Ethernet4 `10.0.12.2/31` · Vlan10 `10.0.1.1/24` · advertises `10.0.1.0/24` |
| leaf2 | sonic-vs  | AS 65002 · Ethernet0 `10.0.12.1/31` · Ethernet4 `10.0.12.3/31` · Vlan20 `10.0.2.1/24` · advertises `10.0.2.0/24` |
| h1    | alpine    | leaf1 Ethernet8 · `10.0.1.10/24` gw `10.0.1.1`, MAC `02:00:00:00:01:10`, MTU 9100 |
| h2    | alpine    | leaf1 Ethernet12 · `10.0.1.11/24` gw `10.0.1.1`, MAC `02:00:00:00:01:11`, MTU 9100 |
| h3    | alpine    | leaf2 Ethernet8 · `10.0.2.10/24` gw `10.0.2.1`, MAC `02:00:00:00:02:10`, MTU 9100 |
| h4    | alpine    | leaf2 Ethernet12 · `10.0.2.11/24` gw `10.0.2.1`, MAC `02:00:00:00:02:11`, MTU 9100 |

Containerlab endpoint `ethN` maps to SONiC `Ethernet(4·(N-1))`: `eth1→Ethernet0`, `eth2→Ethernet4`,
`eth3→Ethernet8`, `eth4→Ethernet12`. BGP timers are set DC-style (keepalive 3 s / hold 10 s) with
fast external failover for demo-friendly reconvergence.

## Host requirements (x86 only)

`docker-sonic-vs` images are **amd64-only** and each leaf needs ~2 GB RAM. Run the lab on an
**x86 Linux host** with Docker and ~8 GB free RAM. On macOS / Apple Silicon, run the lab on an
x86 Ubuntu cloud VM or GitHub Codespaces; the Python app can run anywhere and reach the lab (the
device adapter isolates shell access, so an SSH target can be added).

## Getting the sonic-vs image

The image is **not on any docker registry** — it is a build artifact of the official
`sonic-buildimage` vs pipeline. One-time setup (installs containerlab too):

```bash
make lab-bootstrap                     # pulls branch 202411 by default
SONIC_VS_BRANCH=master make lab-bootstrap    # or pin another branch
SONIC_VS_IMAGE_URL=<url> make lab-bootstrap  # or point at an exact artifact
```

The script downloads the branch's **latest successful** `target/docker-sonic-vs.gz` from
<https://sonic-build.azurewebsites.net> (artifact API), `docker load`s it as
`docker-sonic-vs:latest`, and also tags it `docker-sonic-vs:<branch>`. The resolved artifact URL is
printed for traceability; the download is cached in `~/.chaoslab/cache/`. Manual fallbacks: the
per-branch index at <https://sonic.software> or the Azure DevOps pipeline
*Azure.sonic-buildimage.official.vs* (see the containerlab sonic-vs docs for the click-path).

## Deploy / verify / destroy

```bash
# from the repo root
make lab-bootstrap  # one-time: containerlab + sonic-vs image (see above)
make lab-up       # containerlab deploy + poll until both leafs answer the SONiC CLI
make lab-status   # containerlab inspect + per-node readiness
make lab-reset    # re-apply baseline configs + startup ports (never redeploys)
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

Per leaf, two baseline files are bound to **side paths** — not to `/etc/sonic/config_db.json`,
because sonic's `start.sh` moves a merged config over that file at boot, which fails on a bind
mount:

| File | Bound at | Applied by | Contains |
|---|---|---|---|
| `configs/leaf<n>.json` | `/etc/sonic/baseline_config_db.json` | `config load -y` | ports at MTU 9100, access VLAN + gateway SVI, loopback, `BGP_NEIGHBOR` |
| `configs/frr_leaf<n>.conf` | `/etc/sonic/frr_baseline.conf` | `vtysh -f` | eBGP: neighbors, 3/10 timers, `network` origination, `maximum-paths 2` |

`docker-sonic-vs` ships **no bgpcfgd**, so CONFIG_DB BGP tables are never translated to FRR — the
FRR file is the BGP source of truth. `BGP_NEIGHBOR` stays in the JSON purely as a teaching aid
(the inside_sonic lesson inspects it in CONFIG_DB); it is inert.

The topology manager applies both baselines after the readiness poll on deploy, and
`make lab-reset` / `chaoslab reset` re-runs exactly the same sequence.

> Verify-on-lab: negotiated timers and fast-fallover behavior should still be confirmed on the
> running image (mirrors the `[VERIFY-ON-LAB]` markers in the lessons); BGP origination is pinned
> by the `network` statement in the FRR baseline. **Reset = re-apply baseline +
> `config interface startup`, never redeploy** (§7) — implemented as `config load` merge +
> `vtysh -f` + lesson-port startup. Note `config load` *merges*: non-baseline keys added during
> free experiments survive a reset until `lab-down`/`lab-up`.
