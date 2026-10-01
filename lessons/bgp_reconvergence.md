# Lesson: bgp_reconvergence — BGP in SONiC: peering & reconvergence

## Meta

- **id:** `bgp_reconvergence`
- **difficulty:** core (flagship)
- **requires:** `switches_explained`, `inside_sonic` (assumes: L2/gateway boundary, the config pipeline, containers, APPL_DB/ASIC_DB, STATE_DB)
- **target image:** docker-sonic-vs, branch **202405**, FRR routing stack (Containerlab: leaf1/leaf2 SONiC, h1–h4 hosts)
- **devices used in this lesson:** `leaf1` primarily; `leaf2` for a few peer-side observations; `h1` for the end-to-end data-plane probe
- **lab prerequisites assumed by this lesson (bake into topology):**
  - eBGP: **leaf1 AS 65001 ↔ leaf2 AS 65002** over TWO parallel /31 links:
    - Ethernet0: 10.0.12.0/31 (leaf1) ↔ 10.0.12.1/31 (leaf2)
    - Ethernet4: 10.0.12.2/31 (leaf1) ↔ 10.0.12.3/31 (leaf2)
  - leaf1's two BGP neighbors: **10.0.12.1** (via Ethernet0) and **10.0.12.3** (via Ethernet4); both remote-as 65002
  - each leaf originates its host subnet: leaf1 advertises **10.0.1.0/24**, leaf2 advertises **10.0.2.0/24**; leaf1 therefore learns 10.0.2.0/24 over BOTH sessions → **ECMP** with two next-hops
  - h1 = 10.0.1.10/24 (Vlan10, gw 10.0.1.1); h3 = 10.0.2.10/24 (Vlan20, gw 10.0.2.1)
  - **BGP timers set DC-style: keepalive 3 s / hold 10 s** (fast, demo-friendly; the whole lesson's timing language assumes this)
  - **BGP fast-fallover enabled** (session drops immediately on link carrier loss) — SONiC/FRR default `[VERIFY-ON-LAB]`
  - **soft-reconfiguration inbound enabled** on leaf1 for both neighbors — FRR refuses `received-routes` (used in S2) without it `[VERIFY-ON-LAB: present in lab FRR config, else switch o_advertise to `routes`]`
- **command execution targets:** `leaf1:`/`leaf2:` = docker exec into SONiC vs; `h1:` = host container. `vtysh -c "…"` runs FRR commands inside the bgp container.
- **source URLs relied on:**
  - SONiC CLI Reference (202405) — BGP (`show bgp summary`, `show bgp neighbors [advertised-routes|received-routes|routes]`, `show ip bgp network`, `config bgp shutdown/startup neighbor|all`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#bgp>
  - SONiC CLI Reference (202405) — IP/IPv6 (`show ip route`, `show ip interfaces`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#ip--ipv6>
  - SONiC CLI Reference (202405) — Interfaces (`config interface shutdown/startup`, comma lists): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#interfaces>
  - SONiC Architecture wiki (bgp container: bgpd/zebra/fpmsyncd; fpmsyncd → APPL_DB ROUTE_TABLE; route pipeline to ASIC): <https://github.com/sonic-net/SONiC/wiki/Architecture>
  - FRR BGP documentation (FSM, timers, best-path, multipath/ECMP, graceful behaviors): <https://docs.frrouting.org/en/latest/bgp.html>
  - RFC 4271 (BGP-4: FSM, KEEPALIVE/HOLD, UPDATE/withdraw), RFC 4724 (graceful restart) — concept grounding
  - `vtysh`, `sonic-db-cli` — standard tooling shipped in the image

## Coverage Notes

- **TEACHES:** IP routing and longest-prefix match; connected vs protocol (BGP) routes; eBGP peering over TCP/179 and AS numbers; the BGP finite state machine (Idle→…→Established) with keepalive/hold timers; advertise/withdraw; best-path selection and ECMP; fast-fallover (link-down) vs hold-timer expiry (silent peer); control plane vs data plane; the FRR route path bgpd→zebra→fpmsyncd→APPL_DB→ASIC.
- **REINFORCES:** the config pipeline (now for a route, with fpmsyncd as the new actor); the L2/L3 boundary from Lesson 1 (h1→gateway→route); STATE_DB/ASIC_DB reading; container roles (the bgp container).
- **ASSUMES:** Lessons 1–2; software literacy (state machine, heartbeat/timeout, equal-cost load balancing, hashing).
- **PREVIEWS (named, not taught):** MTU's effect on an *up* BGP session and on ECMP data paths (Lesson 4 uses this topology directly).
- **DELIBERATELY OUT OF SCOPE:** iBGP, route reflectors, BGP policy/route-maps beyond a single withdraw demo, communities, confederations, IPv6, BFD internals, graceful-restart deep mechanics.

## Step Plan

| # | step id | kind | title | core-or-optional |
|---|---------|------|-------|------------------|
| 1 | t_peering | teach | S1: Two routers agree to talk — eBGP over TCP | core |
| 2 | o_peering | observe | S1: Session state, ASNs, prefix counts, uptime | core |
| 3 | q_peering | qna | S1: Questions — peering & the FSM | core |
| 4 | t_advertise | teach | S2: What I tell you vs what you tell me | optional |
| 5 | o_advertise | observe | S2: Advertised-routes vs received-routes per neighbor | optional |
| 6 | q_advertise | qna | S2: Questions — advertisement | optional |
| 7 | t_routes | teach | S3: The routing table — sources, selection, longest-prefix match | core |
| 8 | o_routes | observe | S3: Connected vs BGP routes; the 10.0.2.0/24 entry dissected | core |
| 9 | q_routes | qna | S3: Questions — the route table | core |
| 10 | t_ecmp | teach | S4: Two roads, same cost — ECMP | core |
| 11 | o_ecmp | observe | S4: One prefix, two next-hops | core |
| 12 | q_ecmp | qna | S4: Questions — ECMP | core |
| 13 | t_journey | teach | S5: A route's journey — bgpd to zebra to fpmsyncd to APPL_DB to ASIC | optional |
| 14 | o_journey | observe | S5: The same route in FRR, APPL_DB, and ASIC_DB | optional |
| 15 | q_journey | qna | S5: Questions — the route pipeline | optional |
| 16 | t_timers | teach | S6: Heartbeats and deadlines — keepalive, hold, fast-fallover | optional |
| 17 | o_timers | observe | S6: Negotiated timers on the live session | optional |
| 18 | q_timers | qna | S6: Questions — timers | optional |
| 19 | t_messages | teach | S7: The four BGP message types, counted | optional |
| 20 | o_messages | observe | S7: Opens, Updates, Keepalives, Notifications | optional |
| 21 | q_messages | qna | S7: Questions — messages | optional |
| 22 | t_dataplane | teach | S8: Control plane decides, data plane carries | optional |
| 23 | o_dataplane | observe | S8: h1 reaches h3 while both links carry traffic | optional |
| 24 | q_dataplane | qna | S8: Questions — control vs data plane | optional |
| 25 | q_baseline | qna | Questions — interrogate the healthy baseline | core |
| 26 | chaos | chaos_select | Pick one failure to inject (9 options) | core |
| 27 | q_impact | qna | Questions — interrogate the failure you injected | core |
| 28 | restore | restore | Heal the lab, verify reconvergence | core |

## Teach Sections

### SECTION: t_peering — S1: Two routers agree to talk — eBGP over TCP

**Essence.** Routers do not magically know each other's networks — they *tell* each other, over an explicit, negotiated conversation called a **BGP session**. This scenario is that handshake: two switches in different **autonomous systems** open a TCP connection, agree to be peers, and begin exchanging reachability. Everything else in this lesson rides on this session being alive.

**Mechanism.** A few load-bearing ideas:

- An **AS (autonomous system)** is a network under one administrative control, identified by a number. leaf1 is AS 65001, leaf2 is AS 65002. A session *between different* ASes is **eBGP** (external BGP).
- BGP runs over **TCP port 179** — it is an ordinary reliable connection, which matters later (a session can survive things that break bulk data, and can be blackholed by dropping just those packets).
- The session climbs a **finite state machine (FSM)**: Idle → Connect → Active → OpenSent → OpenConfirm → **Established**. Only in Established do peers exchange routes. Anything less means "not talking yet."
- Once Established, peers prove they're still alive with periodic **keepalive** messages. If a peer hears nothing for the **hold time**, it declares the session dead and tears it down. In this lab keepalive is **3 s** and hold is **10 s** — chosen so failures show up in seconds, not minutes.

**On SONiC.** FRR is the routing engine, living in the **bgp** container (Lesson 2). `show bgp summary` is the session dashboard: your router id and local AS, then one row per neighbor — its address, its AS, message counters, uptime (Up/Down), and the crucial **State/PfxRcd** column. A number there (prefixes received) means **Established** and healthy; a word like `Idle`, `Active`, or `Connect` means the session is *not* up. leaf1 has two neighbors (10.0.12.1 and 10.0.12.3, both AS 65002) — expect both Established, each having received leaf2's host prefix.

**Boundaries.** This scenario establishes *that* peers talk; *what* they say (advertise vs receive) is S2, and *what the routes become* is S3. The FSM's intermediate states are named here but you'll mostly see the endpoints — Established (healthy) and Idle/Active (broken) — which the chaos options produce on demand.

### SECTION: t_advertise — S2: What I tell you vs what you tell me

**Essence.** A BGP session is two independent one-way streams of reachability. leaf1 *advertises* the networks it wants leaf2 to reach it for, and separately *receives* the networks leaf2 advertises. Confusing "what I send" with "what I get" is the most common BGP mistake; this scenario makes the two directions concrete.

**Mechanism.** Reachability travels in **UPDATE** messages. Each carries **NLRI** — the prefixes being announced (e.g. "10.0.1.0/24 is reachable via me") — and, when a network goes away, a **withdraw** (an explicit "forget 10.0.1.0/24 via me"). Advertisement is policy-driven: leaf1 originates 10.0.1.0/24 (its host subnet) because it is configured to, and passes it to every eBGP peer. What leaf1 *accepts* from a peer is likewise subject to policy, but in this simple lab both sides accept each other's host prefix. The asymmetry is the point: I control what I advertise; my peer controls what I receive.

**On SONiC.** `show bgp neighbors 10.0.12.1 advertised-routes` shows what leaf1 is *sending* that neighbor — expect 10.0.1.0/24. `show bgp neighbors 10.0.12.1 received-routes` shows what that neighbor is *sending leaf1* — expect 10.0.2.0/24. Running both, for both neighbors, draws the full picture: leaf1 announces its own subnet out both links and hears leaf2's subnet back over both links (which is precisely why ECMP will exist).

**Boundaries.** `received-routes` shows what arrived *before* local acceptance policy; `routes` shows what was accepted and is a candidate for the table. In this lab they match, but the distinction matters the moment policy enters — and one chaos option withdraws a prefix by policy while the session stays up.

### SECTION: t_routes — S3: The routing table — sources, selection, longest-prefix match

**Essence.** BGP is a *source of routes*, not the router's forwarding table itself. All sources — directly connected subnets, static routes, BGP — feed a single **routing table**, and one rule decides which entry actually forwards a given packet: **longest-prefix match**. This scenario reads that table and dissects the entry that carries h1→h3.

**Mechanism.** Every route has a **prefix** (a network + mask, e.g. 10.0.2.0/24), a **next-hop** (where to send matching packets), and a **source code** telling you how it was learned: `C` connected (a subnet on a local interface — no protocol needed), `S` static, `B` BGP. When a packet arrives, the router finds *all* routes whose prefix contains the destination IP and picks the one with the **longest mask** (most specific). A `/32` beats a `/24` beats a `/0`. For leaf1 forwarding to h3 (10.0.2.10): 10.0.1.0/24 is `C` (connected, h1's side), and 10.0.2.0/24 is `B` (learned from leaf2) — the packet matches the BGP route and is sent toward leaf2. The route's existence is *why* the L2 boundary of Lesson 1 could be crossed.

**On SONiC.** `show ip route` prints the whole table with a legend of source codes; `show ip route 10.0.2.0/24` isolates the entry that matters. Read three things: the source (`B`, BGP), the prefix (10.0.2.0/24), and the next-hop(s) — and note there are *two* next-hops via the two inter-switch links. That plurality is ECMP, the next scenario. Compare with the connected routes for 10.0.12.0/31 and 10.0.12.2/31 (`C`) to feel the difference between "I'm directly on this wire" and "someone told me how to get there."

**Boundaries.** Best-path *selection within BGP* (which of several BGP announcements wins) is richer than we need — here each prefix has one clear origin. We care about the cross-source rule (longest-prefix match) and the BGP-vs-connected distinction. Static routes exist but the lab uses none.

### SECTION: t_ecmp — S4: Two roads, same cost — ECMP

**Essence.** leaf1 has two equally good ways to reach 10.0.2.0/24 — one over each parallel link — and instead of wasting one, it uses **both at once**. This is **ECMP** (equal-cost multi-path): when multiple paths tie, install them all and spread traffic across them. It doubles capacity and provides instant backup.

**Mechanism.** Because leaf2 advertises 10.0.2.0/24 over *both* sessions with equal attributes, BGP considers the two paths equal-cost and installs a **multipath** route with two next-hops (10.0.12.1 and 10.0.12.3). Forwarding then **hashes** each flow — a function of the packet's addresses/ports — to one of the next-hops, keeping every individual flow on a single consistent path (so packets don't reorder) while different flows use different links. The redundancy is automatic: if one link dies, the route simply drops to the surviving next-hop. Remember this shape — Lesson 4 shows how ECMP + a per-link MTU fault produces the maddening "some flows work, some don't."

**On SONiC.** `show ip route 10.0.2.0/24` shows the two next-hops stacked under one prefix. The FRR view `vtysh -c "show ip bgp 10.0.2.0/24"` labels the paths and marks them multipath, so you can see BGP's own reasoning. Two roads, one destination, both in use.

**Boundaries.** The exact hash inputs and per-flow placement are platform-dependent and not something you configure here; we only need "equal paths → both installed → per-flow hashing." Weighted/unequal-cost balancing is a different feature, out of scope.

### SECTION: t_journey — S5: A route's journey — bgpd to zebra to fpmsyncd to APPL_DB to ASIC

**Essence.** In Lesson 2 you watched a VLAN fall through the databases. A *route* takes a longer staircase with an extra actor, because it is born in FRR, not in a SONiC manager. This scenario traces one prefix from the routing protocol all the way to the chip.

**Mechanism.** The route's path:

1. **bgpd** (in the bgp container) receives leaf2's UPDATE and selects the best/multipath route.
2. It hands the winner to **zebra**, FRR's RIB manager, which merges routes from all protocols into one table.
3. **fpmsyncd** — the bridge process — listens to zebra and writes the chosen route into **APPL_DB** as a `ROUTE_TABLE` entry. *This* is where FRR's world joins the SONiC pipeline you already know.
4. From there it's the familiar staircase: **orchagent** consumes APPL_DB, programs **ASIC_DB** (SAI route + next-hop-group objects), **syncd** pushes it to the (virtual) chip.

So a BGP route reuses the entire Lesson-2 pipeline from APPL_DB down; fpmsyncd is simply the on-ramp from FRR. The two next-hops of ECMP appear as a SAI **next-hop group** at the bottom.

**On SONiC.** The observe step reads the same route three ways: `vtysh -c "show ip route 10.0.2.0/24"` (FRR/zebra's view), `sonic-db-cli APPL_DB keys "ROUTE_TABLE:10.0.2.0/24"` (the fpmsyncd hand-off), and the ASIC_DB route/next-hop-group objects (the compiled bottom). One prefix, three altitudes of the same fall.

**Boundaries.** Exact APPL_DB `ROUTE_TABLE` key formatting and the ASIC next-hop-group representation are what the observe step confirms `[VERIFY-ON-LAB]`. zebra's multi-protocol merge is trivial here (only BGP), so we don't dwell on it.

### SECTION: t_timers — S6: Heartbeats and deadlines — keepalive, hold, fast-fallover

**Essence.** A BGP session's liveness rests on two clocks and one shortcut. The **keepalive** clock says how often to prove you're alive; the **hold** clock says how long to wait before giving up on a silent peer; **fast-fallover** is a shortcut that skips the wait entirely when the physical link dies. Understanding these three explains *why some failures are instant and others take seconds*.

**Mechanism.** Peers negotiate to the **lower** of their configured hold times; keepalives are sent at roughly a third of the hold. In this lab: keepalive 3 s, hold 10 s. Two very different failure timings result:

- **Link carrier loss** (cable/interface down): with **fast-fallover**, BGP is told the interface dropped and tears the session down *immediately* — no waiting for the hold timer. Reconvergence is sub-second-ish.
- **Silent peer** (link stays up, but BGP packets stop arriving — a crash, a firewall drop): there is no carrier signal, so the session lingers until the **hold timer expires** (~10 s here) and only *then* collapses. During that window the route still points at a dead peer, and traffic is black-holed.

This gap — instant on link-down, ~hold-time on silence — is the single most important operational nuance in BGP, and two chaos options are designed to produce each case so you can time them.

**On SONiC.** `show bgp neighbors 10.0.12.1` prints the negotiated hold time and keepalive interval for the live session (expect 10 and 3). The `show bgp summary` uptime column resets to zero when a session re-establishes, giving you a crude stopwatch on reconvergence.

**Boundaries.** We keep timers short for teaching; production values are often 60/180 or tuned per design. BFD (a faster sub-second liveness protocol) is the "real" answer to fast detection but is out of scope here — fast-fallover on carrier is enough to contrast against hold-timer expiry.

### SECTION: t_messages — S7: The four BGP message types, counted

**Essence.** Everything BGP does is four message types, and FRR keeps running counts of each. Learning them turns the session from a mystery into a legible conversation you can watch tick.

**Mechanism.** The four:

- **OPEN** — the introduction at session start: "I am AS X, router-id Y, my hold time is Z." Sent once per session bring-up; agreeing on OPEN is how the FSM reaches Established.
- **UPDATE** — the payload: advertise new prefixes and/or withdraw old ones. Every route you saw in S2/S3 arrived in an UPDATE.
- **KEEPALIVE** — the heartbeat: a tiny message on the keepalive clock proving liveness, resetting the peer's hold timer.
- **NOTIFICATION** — the goodbye/error: sent to tear a session down with a reason (bad AS, hold-timer expired, admin shutdown). One NOTIFICATION and the session drops.

Watching the counters tells the story: KEEPALIVEs tick up steadily on a healthy session; a spike in UPDATEs means routes changed; a NOTIFICATION marks a teardown.

**On SONiC.** `show bgp neighbors 10.0.12.1` includes a message-statistics block with sent/received counts for each type. On a quiet, healthy session you'll see KEEPALIVEs climbing and OPEN at 1 (or a small number if it flapped). After you inject a chaos option and restore, re-reading these counters shows the OPEN/NOTIFICATION bump of the teardown-and-reconnect.

**Boundaries.** ROUTE-REFRESH (a fifth, optional message) exists but we don't need it. Message *contents* beyond type/count are deeper than this scenario goes.

### SECTION: t_dataplane — S8: Control plane decides, data plane carries

**Essence.** BGP is **control plane** — it decides routes but forwards no user packet. The **data plane** — the chip programmed via the pipeline — is what actually carries h1's traffic to h3. They are separate systems, and separating them explains SONiC's most counter-intuitive failures. This scenario proves the split with a live ping while inspecting both planes.

**Mechanism.** The control plane (bgpd/zebra) computes the best/multipath route and hands it down; the data plane (ASIC_DB → syncd → chip) executes it at line rate with no per-packet involvement from BGP. Consequences you will exploit: (1) you can *stop the BGP process* and existing forwarding often persists, because the chip keeps its programmed routes (control gone, data alive); (2) you can keep the *session perfectly Established* yet break forwarding by withdrawing the route or corrupting the data path (control healthy, data dead). "Is BGP up?" and "does the ping work?" are genuinely different questions.

**On SONiC.** The observe step pings h1→h3 (data plane working), then shows `show bgp summary` (control plane Established) and `show ip route 10.0.2.0/24` (the decision the control plane handed the data plane). Seeing all three green together sets the baseline; the chaos options then break exactly one plane at a time so you can watch them diverge.

**Boundaries.** How long the data plane survives without the control plane, and under which failures, is `[VERIFY-ON-LAB]` on the virtual switch — the *principle* is firm; the exact persistence window is lab-measured.

## Commands

Convention: `<target>: <command>`. `vtysh -c "…"` runs FRR commands in the bgp container.

### Per-scenario observe commands

#### o_peering
- `leaf1: vtysh -c "show bgp summary"`
- `leaf1: show ip interfaces`

#### o_advertise
- `leaf1: vtysh -c "show bgp neighbors 10.0.12.1 advertised-routes"`
- `leaf1: vtysh -c "show bgp neighbors 10.0.12.1 received-routes"`
- `leaf1: vtysh -c "show bgp neighbors 10.0.12.3 received-routes"`

#### o_routes
- `leaf1: show ip route`
- `leaf1: show ip route 10.0.2.0/24`

#### o_ecmp
- `leaf1: show ip route 10.0.2.0/24`
- `leaf1: vtysh -c "show ip bgp 10.0.2.0/24"`

#### o_journey
- `leaf1: vtysh -c "show ip route 10.0.2.0/24"`
- `leaf1: sonic-db-cli APPL_DB keys "ROUTE_TABLE:10.0.2.0/24"`
- `leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP_GROUP*"`

#### o_timers
- `leaf1: vtysh -c "show bgp neighbors 10.0.12.1"`

#### o_messages
- `leaf1: vtysh -c "show bgp neighbors 10.0.12.1"`

#### o_dataplane
- `h1: ping -c 5 10.0.2.10`
- `leaf1: vtysh -c "show bgp summary"`
- `leaf1: show ip route 10.0.2.0/24`

### After-chaos commands

Run after any injection (the engine diffs these against baseline facts):

- `leaf1: vtysh -c "show bgp summary"`
- `leaf1: show ip route 10.0.2.0/24`
- `leaf1: show interfaces status`
- `leaf1: sonic-db-cli APPL_DB keys "ROUTE_TABLE:10.0.2.0/24"`
- `h1: ping -c 10 10.0.2.10`
- `leaf2: vtysh -c "show bgp summary"`

### Vocabulary

```
vtysh -c "show bgp*
show ip bgp*
show ip route*
show ip interfaces*
show interfaces status*
show interfaces counters*
vtysh*
config bgp shutdown*
config bgp startup*
config interface shutdown*
config interface startup*
sonic-db-cli APPL_DB *
sonic-db-cli ASIC_DB *
sonic-db-cli STATE_DB *
sonic-db-cli CONFIG_DB *
redis-cli *
ping*
ip route*
ip neigh*
```

## Chaos Options

All options are restorable to baseline. "Injection-failed tell" = how to detect the injection itself did not take on sonic-vs. Timing language assumes keepalive 3 s / hold 10 s.

---

**1. id: `c_shut_one_link` — "Cut one of two roads: shut a single inter-switch link" (ANCHOR A)**
- **type:** link · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: sudo config interface shutdown Ethernet0`
- **restore:**
  - `leaf1: sudo config interface startup Ethernet0`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** With fast-fallover, the session over Ethernet0 (neighbor 10.0.12.1) drops within a second — no hold-time wait. ECMP for 10.0.2.0/24 collapses from two next-hops to one (via 10.0.12.3); the route survives, traffic reroutes to the surviving link. Expect near-zero data loss — a handful of packets at most during reconvergence `[VERIFY-ON-LAB: loss window on vs]`. `show bgp summary` shows one neighbor Idle/Active, one still Established. Recovery: link up → session re-establishes in a few seconds → route returns to two next-hops.
- **plan-B variant:** carrier loss on the veth from the container host would be a truer "cable pull," but host-side actions are out of scope; the admin shutdown is the sanctioned equivalent and still triggers fast-fallover.
- **injection-failed tell:** `show ip route 10.0.2.0/24` still shows two next-hops after inject → the shutdown didn't take.

---

**2. id: `c_shut_both_links` — "Total isolation: cut both roads" (ANCHOR B)**
- **type:** link · **risk:** medium · **enabled:** true
- **inject:**
  - `leaf1: sudo config interface shutdown Ethernet0,Ethernet4`
- **restore:**
  - `leaf1: sudo config interface startup Ethernet0,Ethernet4`
  - `h1: ping -c 5 10.0.2.10`
- **expected effects (words):** Both sessions drop within ~1 s (fast-fallover). 10.0.2.0/24 loses *all* next-hops and is withdrawn from the table entirely; h1→h3 goes to 100% loss. The starkest control-plane→data-plane consequence: no route, no forwarding. Recovery: both links up → both sessions re-establish within a few seconds → route and ECMP fully return.
- **plan-B variant:** shut the two links on **leaf2** instead (`leaf2: sudo config interface shutdown Ethernet0,Ethernet4`) — identical effect from the peer's side.
- **injection-failed tell:** `h1: ping 10.0.2.10` still succeeds after inject → at least one link stayed up.

---

**3. id: `c_bgp_admin_shut` — "Polite goodbye: administratively shut one neighbor"**
- **type:** config · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: sudo config bgp shutdown neighbor 10.0.12.1`
- **restore:**
  - `leaf1: sudo config bgp startup neighbor 10.0.12.1`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** A NOTIFICATION tears down just that session; `show bgp summary` shows 10.0.12.1 as `Idle (Admin)`. The **link stays up** — the perfect control-vs-data contrast to option 1: same ECMP-halving effect on the route, but the physical interface is untouched and Ethernet0's counters keep any non-BGP traffic. Route drops to one next-hop; minimal data loss. Recovery: `startup neighbor` re-establishes in a few seconds.
- **plan-B variant:** `leaf1: sudo config bgp shutdown all` / `startup all` — tears down *both* sessions administratively while both links stay up (contrast with option 2's link-down total loss).
- **injection-failed tell:** `show bgp summary` still shows 10.0.12.1 Established (a prefix count) after inject → shutdown didn't take.

---

**4. id: `c_hold_timer_blackhole` — "Silent peer: drop BGP packets, keep the link up"**
- **type:** config · **risk:** medium · **enabled:** true  *(enabled but NOT recommended for the live demo — the ~10 s healthy-looking blackhole is confusing on stage; use option 1 or 3 for demos)*
- **inject:**
  - `leaf1: iptables -A INPUT -p tcp --dport 179 -s 10.0.12.1 -j DROP`
  - `leaf1: iptables -A INPUT -p tcp --sport 179 -s 10.0.12.1 -j DROP`
- **restore:**
  - `leaf1: iptables -D INPUT -p tcp --dport 179 -s 10.0.12.1 -j DROP`
  - `leaf1: iptables -D INPUT -p tcp --sport 179 -s 10.0.12.1 -j DROP`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** THE hold-timer demonstration. The link stays up and the session reads Established for the first several seconds because no carrier signal says otherwise — but keepalives from 10.0.12.1 no longer arrive. At **~10 s** (hold time) leaf1 declares the session dead with a "hold timer expired" NOTIFICATION and only *then* halves ECMP. If the flow was hashed onto that link, traffic is black-holed for the whole hold window while everything *looks* healthy — the operational nightmare this course exists to teach `[VERIFY-ON-LAB: exact expiry ≈ hold time; which flows are affected depends on hashing]`. Recovery: delete the rules; session re-establishes in seconds.
- **plan-B variant:** extend the silence to *both* directions by also dropping leaf1's **outbound** BGP (`iptables -A OUTPUT -p tcp --dport 179 -d 10.0.12.1 -j DROP` plus the matching `--sport 179` rule; remove with `-D` on restore) — then each side expires on its own hold clock, instead of only leaf1 as in the primary (which drops inbound only).
- **injection-failed tell:** `show bgp summary` still shows 10.0.12.1 Established more than ~15 s after inject → the iptables rules didn't match (check addresses/chain).

---

**5. id: `c_withdraw_only` — "Session up, route gone: withdraw the prefix"**
- **type:** config · **risk:** medium · **enabled:** false  *(bgpcfgd may re-assert templated config on vs; withdraw method needs lab verification)*
- **inject:**
  - `leaf2: vtysh -c "configure terminal" -c "router bgp 65002" -c "address-family ipv4 unicast" -c "no network 10.0.2.0/24"`  `[VERIFY-ON-LAB: exact origination method (network statement vs redistribute) in the lab's FRR config]`
- **restore:**
  - `leaf2: vtysh -c "configure terminal" -c "router bgp 65002" -c "address-family ipv4 unicast" -c "network 10.0.2.0/24"`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** The purest control-vs-data lesson. Both sessions stay perfectly Established (keepalives flow), but leaf2 sends an UPDATE **withdrawing** 10.0.2.0/24. leaf1 removes the route from its table; h1→h3 dies with `show bgp summary` looking flawless. "BGP is up" and "the route exists" are shown to be different facts. Recovery: re-originate; the UPDATE re-adds the prefix in seconds.
- **plan-B variant:** on leaf2, remove the Vlan20 IP so the *connected* origin of 10.0.2.0/24 disappears (blunter, also withdraws the prefix).
- **injection-failed tell:** `leaf1: show ip route 10.0.2.0/24` still present after inject → origination wasn't actually removed (bgpcfgd may have re-added it).

---

**6. id: `c_wrong_asn` — "Identity mismatch: wrong remote-AS"**
- **type:** config · **risk:** medium · **enabled:** false  *(conflicts with bgpcfgd-managed neighbor config on vs; verify persistence)*
- **inject:**
  - `leaf1: vtysh -c "configure terminal" -c "router bgp 65001" -c "neighbor 10.0.12.1 remote-as 65099"`  `[VERIFY-ON-LAB: whether vtysh change sticks or bgpcfgd reverts it]`
- **restore:**
  - `leaf1: vtysh -c "configure terminal" -c "router bgp 65001" -c "neighbor 10.0.12.1 remote-as 65002"`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** The OPEN handshake now fails: leaf1 expects AS 65099 but 10.0.12.1 identifies as 65002, so a NOTIFICATION ("Bad Peer AS") fires and the FSM cannot reach Established — it flaps Idle/Active/Connect. ECMP halves to the one good session. Teaches that peering requires *agreement*, checked at OPEN. Recovery: correct the AS; session climbs to Established.
- **plan-B variant:** edit `CONFIG_DB BGP_NEIGHBOR|10.0.12.1` asn and restart the bgp service (heavier, more deterministic than a live vtysh edit).
- **injection-failed tell:** `show bgp summary` still shows 10.0.12.1 Established after inject → the AS change didn't apply (bgpcfgd likely reverted it).

---

**7. id: `c_clear_bgp` — "Hard reset: clear all BGP sessions"**
- **type:** churn · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: vtysh -c "clear ip bgp *"`
- **restore:** *(self-healing — restore = verify re-establishment)*
  - `h1: ping -c 5 10.0.2.10`
  - `leaf1: show bgp summary`
- **expected effects (words):** Both sessions are forcibly torn down and rebuilt from Idle → OPEN → … → Established. Uptime in `show bgp summary` resets to near-zero (your reconvergence stopwatch). Brief route churn as prefixes re-advertise; short, small data loss during the rebuild `[VERIFY-ON-LAB: loss window]`. A clean way to watch the FSM climb live. Recovery is automatic within a few seconds.
- **plan-B variant:** `leaf1: vtysh -c "clear ip bgp * soft"` — a *soft* clear that re-processes routes **without** dropping the sessions (contrast: uptime does NOT reset, no FSM restart).
- **injection-failed tell:** `show bgp summary` uptimes are unchanged (not reset) after inject → the clear didn't run.

---

**8. id: `c_stop_bgp_service` — "Kill the routing brain: stop the bgp container"**
- **type:** service · **risk:** medium · **enabled:** true
- **inject:**
  - `leaf1: sudo systemctl stop bgp`
- **restore:**
  - `leaf1: sudo systemctl start bgp`
  - `h1: ping -c 5 10.0.2.10`
- **expected effects (words):** bgpd, zebra, and fpmsyncd all die. Sessions drop (leaf2 will time out its side at hold). The revealing question: does h1→h3 keep working? If the chip retains its programmed routes, forwarding may persist even with the whole routing brain gone — control plane dead, data plane alive `[VERIFY-ON-LAB: whether ROUTE_TABLE/ASIC route lingers and for how long]`. Recovery: start bgp → sessions re-establish → fpmsyncd re-syncs routes into APPL_DB.
- **plan-B variant:** `leaf1: docker exec bgp supervisorctl stop bgpd` — kill only bgpd while zebra/fpmsyncd live (subtler: existing routes may persist in zebra→APPL_DB longer).
- **injection-failed tell:** `docker ps` still shows the bgp container running after inject → stop didn't take.

---

**9. id: `c_link_flap` — "The flapping link"**
- **type:** churn · **risk:** medium · **enabled:** false  *(repeated flaps; timing/no-dampening behavior on vs unverified)*
- **inject:**
  - `leaf1: sh -c 'for i in 1 2 3 4 5; do sudo config interface shutdown Ethernet0; sleep 2; sudo config interface startup Ethernet0; sleep 5; done'`
- **restore:**
  - `leaf1: sudo config interface startup Ethernet0`
  - `h1: ping -c 5 10.0.2.10`
- **expected effects (words):** Ethernet0's session repeatedly drops and re-establishes; ECMP oscillates between one and two next-hops; message counters (OPEN/NOTIFICATION) climb with each cycle; brief repeated loss for flows hashed onto that link. FRR applies no route dampening by default, so every flap fully reconverges — teaches both resilience and the churn cost of an unstable link. Recovery: ensure the final state is up.
- **plan-B variant:** flap the *session* instead of the link — loop `vtysh -c "clear ip bgp 10.0.12.1"` — churn without touching the interface.
- **injection-failed tell:** `show bgp summary` uptime for 10.0.12.1 never dips/resets during the loop → the flaps aren't affecting the session.

---

## Observe

Parsers available: `interface_status, mac_table, lldp_neighbors, vlan_membership, bgp_neighbors, route_count, ping_loss, redis_keys, propagation_lag`. New parsers are marked.

### Happy-path fact map

| scenario (observe id) | parser(s) | key fields to extract | notes |
|---|---|---|---|
| o_peering | bgp_neighbors | per neighbor: addr, remote_as, state, pfx_rcd, uptime; local router-id/AS | assert both 10.0.12.1 and 10.0.12.3 Established with pfx_rcd ≥1 |
| o_advertise | **NEW-PARSER: bgp_adv_rcv** (prefix lists per direction) | advertised set {10.0.1.0/24}; received set {10.0.2.0/24} per neighbor | assert advertise=own subnet, receive=peer subnet on both links |
| o_routes | route_count; **NEW-PARSER: route_entry** (prefix, source_code, next_hops[]) | 10.0.2.0/24 source=B; connected /31s source=C | assert 10.0.2.0/24 is BGP with ≥1 next-hop |
| o_ecmp | route_entry; bgp_neighbors | 10.0.2.0/24 next_hops = {10.0.12.1, 10.0.12.3}; multipath flag in FRR | assert exactly two next-hops |
| o_journey | redis_keys | FRR route present; APPL_DB ROUTE_TABLE key present; ASIC next-hop-group present | assert same prefix visible at all three altitudes `[VERIFY-ON-LAB: key formats]` |
| o_timers | bgp_neighbors | negotiated hold_time, keepalive_interval | assert hold≈10, keepalive≈3 `[VERIFY-ON-LAB]` |
| o_messages | **NEW-PARSER: bgp_msg_stats** (per type: sent, rcvd) | Opens, Updates, Keepalives, Notifications | assert Keepalives climbing; Notifications 0 on steady state |
| o_dataplane | ping_loss; bgp_neighbors; route_entry | ping h1→h3 loss 0%; both sessions Established; route present | all three planes green as baseline |

### After-chaos fact map (same command set for all options)

| chaos id | facts that should CHANGE | facts that should NOT change | measure_recovery |
|---|---|---|---|
| c_shut_one_link | one neighbor →Idle/Active; 10.0.2.0/24 →1 next-hop; Ethernet0 down | route still present; ping ~0% loss | yes — time to 2 next-hops again |
| c_shut_both_links | both neighbors down; 10.0.2.0/24 withdrawn; ping 100%; Ethernet0/4 down | Ethernet8 status; leaf1 VLAN/L2 state | yes — time to route return |
| c_bgp_admin_shut | 10.0.12.1 → Idle (Admin); route →1 next-hop | Ethernet0 status stays UP (control-only) | yes |
| c_hold_timer_blackhole | after ~10 s: 10.0.12.1 down; route →1 next-hop | Ethernet0 UP; session Established for first seconds | yes — measure expiry ≈ hold |
| c_withdraw_only | 10.0.2.0/24 gone; ping 100% | both sessions Established (control healthy!) | yes |
| c_wrong_asn | 10.0.12.1 never Established (flaps); route →1 next-hop | Ethernet0 UP | yes |
| c_clear_bgp | both uptimes reset to ~0; brief churn | steady-state route returns; ping ~0% | yes — reconverge time |
| c_stop_bgp_service | bgp container gone; sessions drop | ideally ping persists (data plane) `[VERIFY-ON-LAB]` | yes |
| c_link_flap | uptime for 10.0.12.1 oscillates; msg counters climb | other neighbor steady | no |

## Knowledge Card

*(Consumed by the runtime LLM for EXPLAIN moments. Meanings and expectations in words only.)*

### Objective

The learner can explain eBGP peering and the session FSM, distinguish advertise from receive, read a routing table and apply longest-prefix match, explain and verify ECMP, trace a BGP route through fpmsyncd into the SONiC pipeline, reason about keepalive/hold/fast-fallover timing (why some failures are instant and others take ~hold seconds), separate control plane from data plane, and diagnose which of the two a given failure broke.

### In-scope subtopics

AS numbers and eBGP over TCP/179; FSM Idle→Established; keepalive/hold timers and fast-fallover; UPDATE advertise/withdraw and NLRI; routing table sources (C/S/B) and longest-prefix match; best-path/ECMP and per-flow hashing; the bgpd→zebra→fpmsyncd→APPL_DB→ASIC route path; the four BGP message types and their counters; control-plane vs data-plane separation; the nine chaos options' blast radius and recovery.

### Key concepts (one sentence each)

1. eBGP is a negotiated TCP/179 session between two autonomous systems that only exchanges routes once its FSM reaches Established.
2. A session proves liveness with keepalives and dies if it hears nothing for the hold time; in this lab those are 3 s and 10 s.
3. Advertise and receive are independent directions — you control what you announce, your peer controls what you learn.
4. All route sources feed one table, and longest-prefix match — not the source protocol — decides which entry forwards a packet.
5. Equal-cost paths are installed together as ECMP, and forwarding hashes each flow onto one next-hop for consistency plus redundancy.
6. A BGP route reaches hardware via fpmsyncd writing APPL_DB ROUTE_TABLE, then the same orchagent→ASIC_DB→syncd pipeline as any other object.
7. Link carrier loss with fast-fallover drops a session immediately, but a silent peer (link up, packets stopped) is only detected at hold-timer expiry.
8. The four message types — OPEN, UPDATE, KEEPALIVE, NOTIFICATION — are the whole protocol, and their counters narrate the session.
9. BGP is control plane only; the chip's programmed routes are the data plane, so the two can fail — and be diagnosed — independently.
10. "Is BGP Established?" and "does the ping work?" are different questions, and the interesting failures make them disagree.

### Command field meanings

- **`show bgp summary`** — router-id and local AS on top; per-neighbor rows: Neighbor (peer IP), V (version 4), AS (peer AS), MsgRcvd/MsgSent, TblVer, InQ/OutQ, Up/Down (uptime, or how long down), **State/PfxRcd** (a number = Established with that many prefixes; a word = not up).
- **`show bgp neighbors <ip>`** — deep session detail: BGP state and uptime; negotiated **hold time** and **keepalive interval**; capabilities; a **message-statistics** block (Opens/Notifications/Updates/Keepalives, sent vs rcvd); local/foreign host:port; accepted-prefix count; last-reset reason.
- **`show bgp neighbors <ip> advertised-routes | received-routes | routes`** — prefixes sent to / received from / accepted from that neighbor.
- **`show ip route [<prefix>]`** — routing table with a legend (K/C/S/B/…); per entry: source code, prefix, next-hop(s), interface, `>` selected / `*` FIB; a prefix argument isolates one entry (and shows multiple next-hops for ECMP).
- **`vtysh -c "show ip bgp <prefix>"`** — FRR's BGP view of a prefix: candidate paths, best-path and **multipath** markings, next-hops, attributes.
- **`show ip interfaces`** — L3 addresses per interface with Admin/Oper (confirm the /31s are up).
- **`config bgp shutdown|startup neighbor <ip>`** / **`… all`** — administratively drop/restore a session (or all) without touching the link; shows as `Idle (Admin)`.
- **`config interface shutdown|startup <port[,port]>`** — admin down/up one or more links (triggers fast-fallover on the session).
- **`vtysh -c "clear ip bgp *"`** — hard-reset sessions (FSM restart, uptime resets); `… * soft` re-processes routes without dropping sessions.
- **`sonic-db-cli APPL_DB keys "ROUTE_TABLE:<prefix>"`** — the fpmsyncd-written route entry; **ASIC_DB** `SAI_OBJECT_TYPE_NEXT_HOP_GROUP` — the ECMP group at chip level.
- **`ping -c N <ip>`** (h1) — the data-plane truth; loss/rtt are data even on non-zero exit.

### Healthy-state expectations (baseline, in words)

- `show bgp summary`: local AS 65001; neighbors 10.0.12.1 and 10.0.12.3, both remote AS 65002, both Established, each with ≥1 prefix received.
- `show bgp neighbors 10.0.12.1`: hold time ≈10 s, keepalive ≈3 s; Keepalive counters climbing; Notifications 0.
- Advertise 10.0.1.0/24 to each neighbor; receive 10.0.2.0/24 from each.
- `show ip route 10.0.2.0/24`: source B, **two** next-hops (10.0.12.1 via Ethernet0, 10.0.12.3 via Ethernet4); the /31s are connected (C).
- Route visible in FRR, in APPL_DB ROUTE_TABLE, and as an ASIC next-hop-group `[VERIFY-ON-LAB: key formats]`.
- h1→10.0.2.10: 0% loss.

### Expected chaos effects per option (timings; keepalive 3 / hold 10)

- **c_shut_one_link:** session on Ethernet0 down within ~1 s (fast-fallover); route →1 next-hop; ≤ a few packets lost `[VERIFY-ON-LAB]`; recovery in ~3–6 s after startup.
- **c_shut_both_links:** both down within ~1 s; route withdrawn; 100% loss; recovery in ~3–6 s after startup.
- **c_bgp_admin_shut:** 10.0.12.1 → Idle (Admin) immediately; link stays UP; route →1 next-hop; recovery seconds after startup neighbor.
- **c_hold_timer_blackhole:** session reads Established for ~10 s then drops at hold expiry; flows hashed to that link black-holed for the window; route →1 next-hop after expiry `[VERIFY-ON-LAB: expiry ≈ hold]`.
- **c_withdraw_only:** sessions stay Established; 10.0.2.0/24 disappears; 100% loss; recovery seconds after re-originate.
- **c_wrong_asn:** 10.0.12.1 never reaches Established (Bad Peer AS, flapping); route →1 next-hop; recovery once AS corrected.
- **c_clear_bgp:** both uptimes reset to ~0; brief churn/loss; reconverge in a few seconds; soft variant does NOT reset uptime.
- **c_stop_bgp_service:** bgp container gone; sessions drop; data plane may persist `[VERIFY-ON-LAB]`; recovery on start + fpmsyncd re-sync.
- **c_link_flap:** uptime for 10.0.12.1 oscillates; OPEN/NOTIFICATION counters climb; repeated brief loss; no dampening.

### Misconceptions (wrong → why → correct)

1. **"BGP forwards the packets."** → BGP is control plane; it only computes routes handed to the chip, which forwards. → Session up and ping working are separate facts.
2. **"Advertised and received routes are the same list."** → They are independent directions with independent policy. → Always ask "which direction?" — advertise is what I send, receive is what I get.
3. **"A down link and a silent peer fail the same way."** → Carrier loss triggers immediate fast-fallover; a silent peer waits out the hold timer (~10 s). → Detection timing depends on whether the physical link signals the failure.
4. **"If the session is Established, traffic must flow."** → A withdraw or a data-path fault breaks forwarding with the session still up. → Established means "we're talking," not "the route exists and works."
5. **"ECMP picks one link and keeps the other idle."** → Both are installed; forwarding hashes flows across both. → ECMP is active/active per-flow, not active/standby.
6. **"BGP chooses paths per packet."** → It selects routes; the data plane hashes per *flow* to avoid reordering. → Path selection (control) and per-flow hashing (data) are different layers.
7. **"Killing bgpd instantly drops all traffic."** → The chip keeps programmed routes, so forwarding can persist briefly. → Control-plane death ≠ immediate data-plane death.
8. **"The routing table only holds BGP routes."** → It merges connected, static, and BGP; longest-prefix match arbitrates. → BGP is one source among several feeding one table.
9. **"Higher/longer prefixes are worse routes."** → Longest-prefix match prefers the *most specific* route. → A /32 beats a /24 for a contained address, by design.

### Out-of-scope (redirect if asked)

iBGP, route reflectors/confederations, communities, complex route-maps/policy beyond one withdraw, BGP path-attribute tie-breaking depth, BFD internals, IPv6/EVPN, graceful-restart deep mechanics, MTU effects (Lesson 4), L2 forwarding (Lesson 1).

### Polite redirect line

"That's interesting but outside this lesson's scope — eBGP peering, routes, ECMP, and reconvergence on leaf1/leaf2. I'd recommend reading it up separately (the FRR BGP docs or RFC 4271 are the right sources). Let's return to the session in front of us — pick a suggested question or run another on-topic command."

## Glossary

- **routing** — forwarding IP packets between subnets based on a routing table (contrast: switching within a VLAN).
- **routing table** — the merged set of best routes from all sources, consulted per packet.
- **prefix** — a network plus mask (e.g. 10.0.2.0/24) naming a range of addresses.
- **next-hop** — the neighbor address a matching packet is forwarded toward.
- **longest-prefix match** — the rule that the most specific (longest-mask) matching route wins.
- **connected route (C)** — a route to a subnet directly on a local interface, needing no protocol.
- **static route (S)** — a manually configured route.
- **BGP route (B)** — a route learned via BGP.
- **AS (autonomous system)** — a network under one administrative control, identified by a number.
- **eBGP** — BGP between different autonomous systems.
- **BGP session** — the negotiated TCP/179 peering over which routes are exchanged.
- **FSM (finite state machine)** — BGP's session lifecycle Idle→Connect→Active→OpenSent→OpenConfirm→Established.
- **Established** — the FSM state in which peers actually exchange routes.
- **keepalive** — the periodic heartbeat message (and its interval) proving a session is alive.
- **hold time** — the silence tolerated before a session is declared dead.
- **fast-fallover** — dropping a session immediately on link carrier loss instead of waiting for hold.
- **UPDATE** — the BGP message that advertises and/or withdraws prefixes.
- **NLRI** — the prefixes carried in an UPDATE.
- **withdraw** — an UPDATE telling a peer to remove a previously advertised prefix.
- **advertise / receive** — the outbound / inbound directions of route exchange.
- **best path** — the route BGP selects as winner among candidates for a prefix.
- **ECMP** — equal-cost multi-path: installing multiple equal routes and load-sharing across them.
- **multipath** — BGP's marking that several paths tie and are all installed.
- **next-hop group** — the chip-level object bundling ECMP next-hops.
- **flow hashing** — choosing one ECMP next-hop per flow from packet fields, to avoid reordering.
- **OPEN** — the BGP session-setup message negotiating AS, router-id, and hold time.
- **KEEPALIVE (message)** — the heartbeat message type.
- **NOTIFICATION** — the BGP message that tears a session down with a reason.
- **control plane** — the software that decides routes (bgpd/zebra); forwards no user traffic.
- **data plane** — the chip that forwards packets at line rate using programmed routes.
- **bgpd** — the FRR BGP daemon that runs sessions and selects routes.
- **zebra** — FRR's RIB manager merging routes from all protocols.
- **fpmsyncd** — the process bridging zebra's chosen routes into APPL_DB ROUTE_TABLE.
- **RIB / FIB** — routing information base (control-plane table) / forwarding information base (installed/data-plane table).
- **vtysh** — the FRR shell used to view and configure the routing stack.
- **reconvergence** — the network re-computing and re-installing routes after a change.

## QnA

### Scope keywords (gate input; generous synonyms)

```
bgp, ebgp, ibgp, peer, peering, neighbor, session, tcp 179, port 179,
autonomous system, asn, as number, fsm, state machine, idle, connect,
active, opensent, openconfirm, established, keepalive, hold time, holdtime,
hold timer, timer, timers, fast fallover, fallover, bfd, advertise,
advertised, receive, received, update, withdraw, nlri, prefix, route,
routing, routing table, rib, fib, next-hop, nexthop, longest prefix match,
lpm, connected route, static route, best path, bestpath, ecmp, multipath,
equal cost, load balance, load sharing, hashing, flow, next-hop group,
open, notification, message, control plane, data plane, reconverge,
reconvergence, bgpd, zebra, fpmsyncd, vtysh, frr, route_table, show ip route,
show bgp summary, clear ip bgp, 10.0.2.0/24, 10.0.12.1, ethernet0, leaf1, leaf2
```

### Question bank (8 per qna step; TOP 3 marked ★; ordered easy → deep)

#### q_peering
1. ★ What does "Established" mean in `show bgp summary`, and how do I tell at a glance?
2. ★ Why do leaf1 and leaf2 have different AS numbers, and what makes this eBGP?
3. ★ What are the two neighbors leaf1 has, and why two?
4. What transport does BGP run over, and why does that matter later?
5. What are the FSM states between Idle and Established?
6. What does the number in the State/PfxRcd column actually count?
7. If a neighbor showed "Active," what would that tell you?
8. What is the router-id, and where does leaf1's come from?

#### q_advertise
1. ★ What's the difference between advertised-routes and received-routes?
2. ★ Which prefix does leaf1 advertise, and which does it receive?
3. ★ Why does leaf1 receive 10.0.2.0/24 over both links?
4. What is an UPDATE message, and what can it carry?
5. What is a withdraw, and when would leaf2 send one?
6. What's the difference between received-routes and routes (accepted)?
7. Who decides what leaf1 is allowed to receive?
8. If advertised-routes were empty, what would you suspect?

#### q_routes
1. ★ How does the router pick between 10.0.1.0/24 and 10.0.2.0/24 for a packet to h3?
2. ★ What do the C and B source codes mean in `show ip route`?
3. ★ Why is 10.0.2.0/24 a BGP route while 10.0.12.0/31 is connected?
4. What is longest-prefix match, with an example?
5. What is a next-hop, and how is it used at forwarding time?
6. How does a connected route differ from one learned by a protocol?
7. Where does this table get merged from — is BGP the only source?
8. If 10.0.2.0/24 vanished from this table, what are the possible causes?

#### q_ecmp
1. ★ Why does 10.0.2.0/24 have two next-hops instead of one?
2. ★ Does ECMP use both links at once or keep one as backup?
3. ★ How does the switch decide which link a given flow takes?
4. What makes two paths "equal cost" here?
5. What happens to ECMP when one of the two links goes down?
6. Why hash per flow instead of per packet?
7. How would I confirm multipath in FRR's own view?
8. How could ECMP plus a per-link fault cause "some flows work, some don't"?

#### q_journey
1. ★ Trace 10.0.2.0/24 from bgpd all the way to the chip.
2. ★ What does fpmsyncd do, and why is it the on-ramp to the SONiC pipeline?
3. ★ How is a route's path to hardware different from a VLAN's (Lesson 2)?
4. What is zebra's job in this chain?
5. Where do the two ECMP next-hops show up at the ASIC level?
6. Which database does fpmsyncd write, and with what key?
7. If the route is in FRR but not in APPL_DB, who's to blame?
8. If it's in APPL_DB but not the chip, which container do you check?

#### q_timers
1. ★ What are keepalive and hold time, and what are they in this lab?
2. ★ Why does a silent peer take ~10 s to detect but a dead link is instant?
3. ★ What is fast-fallover, and when does it help?
4. How are the hold and keepalive values negotiated between peers?
5. What is the operational danger of the hold-timer window?
6. Why did we set 3/10 instead of the classic 60/180?
7. Where does `show bgp neighbors` show the negotiated timers?
8. How would BFD change this detection story (at a high level)?

#### q_messages
1. ★ What are the four BGP message types and what does each do?
2. ★ Which message tears a session down, and how would I see it?
3. ★ What do rising KEEPALIVE counts versus a jump in UPDATEs tell you?
4. When is an OPEN sent, and what does it negotiate?
5. What would a NOTIFICATION's reason code tell you after a failure?
6. Where in the CLI are these counters shown?
7. How would the counters look right after a `clear ip bgp *`?
8. Which message carries a withdraw?

#### q_dataplane
1. ★ What's the difference between the control plane and the data plane here?
2. ★ Why might h1→h3 keep working after you stop the bgp container?
3. ★ How can the session be Established while the ping fails?
4. Which parts of SONiC are control plane and which are data plane?
5. Why is "is BGP up?" not the same question as "does the ping work?"
6. What does the chip need in order to forward without BGP running?
7. How would you prove, with three commands, that all planes are healthy?
8. Which chaos options break control-only, and which break data?

#### q_baseline
1. ★ What does "Established" mean in `show bgp summary`, and how do I tell at a glance?
2. ★ Why do leaf1 and leaf2 have different AS numbers, and what makes this eBGP?
3. ★ What are the two neighbors leaf1 has, and why two?

#### q_impact
1. ★ What failed, and which facts changed after the injection?
2. ★ Why did the route count and ping loss change the way they did?
3. ★ What state is each BGP neighbor in now, and why?

## Verify-On-Lab

1. **Timers:** confirm negotiated hold ≈10 s and keepalive ≈3 s in `show bgp neighbors`; confirm the lab's FRR config actually sets 3/10.
2. **Fast-fallover:** confirm a link shutdown drops the session immediately (sub-second) rather than waiting for hold; confirm it's enabled by default in the lab.
3. **Loss windows:** measure data loss for c_shut_one_link, c_shut_both_links, c_clear_bgp (packets lost during reconvergence).
4. **Hold-timer expiry:** for c_hold_timer_blackhole, confirm the session stays Established for ~hold seconds then drops with a "hold timer expired" NOTIFICATION; confirm which flows blackhole (hashing).
5. **Route pipeline key formats:** APPL_DB `ROUTE_TABLE:10.0.2.0/24` shape; ASIC_DB next-hop-group object for the ECMP pair.
6. **Data-plane persistence:** for c_stop_bgp_service, confirm whether h1↔h3 keeps forwarding with bgp stopped, and for how long; whether ROUTE_TABLE/ASIC route lingers.
7. **bgpcfgd interference:** for c_withdraw_only and c_wrong_asn, confirm whether live vtysh edits stick or are reverted by bgpcfgd/templated config; adjust method (CONFIG_DB + service restart) if reverted.
8. **c_withdraw_only origination method:** confirm whether 10.0.2.0/24 is originated by a `network` statement or redistribute-connected in the lab's FRR config; use the matching removal.
9. **c_link_flap:** confirm no route dampening by default; capture per-flap reconverge timing.
10. **soft clear contrast:** confirm `clear ip bgp * soft` does NOT reset uptime while `clear ip bgp *` does.
11. **soft-reconfiguration inbound:** confirm it is enabled for both neighbors (otherwise `received-routes` in o_advertise errors); if it stays off, change o_advertise to `routes`.

## Machine Summary

```json
{
  "id": "bgp_reconvergence",
  "steps": [
    {"id": "t_peering", "kind": "teach", "core": true},
    {"id": "o_peering", "kind": "observe", "core": true},
    {"id": "q_peering", "kind": "qna", "core": true},
    {"id": "t_advertise", "kind": "teach", "core": false},
    {"id": "o_advertise", "kind": "observe", "core": false},
    {"id": "q_advertise", "kind": "qna", "core": false},
    {"id": "t_routes", "kind": "teach", "core": true},
    {"id": "o_routes", "kind": "observe", "core": true},
    {"id": "q_routes", "kind": "qna", "core": true},
    {"id": "t_ecmp", "kind": "teach", "core": true},
    {"id": "o_ecmp", "kind": "observe", "core": true},
    {"id": "q_ecmp", "kind": "qna", "core": true},
    {"id": "t_journey", "kind": "teach", "core": false},
    {"id": "o_journey", "kind": "observe", "core": false},
    {"id": "q_journey", "kind": "qna", "core": false},
    {"id": "t_timers", "kind": "teach", "core": false},
    {"id": "o_timers", "kind": "observe", "core": false},
    {"id": "q_timers", "kind": "qna", "core": false},
    {"id": "t_messages", "kind": "teach", "core": false},
    {"id": "o_messages", "kind": "observe", "core": false},
    {"id": "q_messages", "kind": "qna", "core": false},
    {"id": "t_dataplane", "kind": "teach", "core": false},
    {"id": "o_dataplane", "kind": "observe", "core": false},
    {"id": "q_dataplane", "kind": "qna", "core": false},
    {"id": "q_baseline", "kind": "qna", "core": true},
    {"id": "chaos", "kind": "chaos_select", "core": true},
    {"id": "q_impact", "kind": "qna", "core": true},
    {"id": "restore", "kind": "restore", "core": true}
  ],
  "commands": {
    "o_peering": ["leaf1: vtysh -c \"show bgp summary\"", "leaf1: show ip interfaces"],
    "o_advertise": ["leaf1: vtysh -c \"show bgp neighbors 10.0.12.1 advertised-routes\"", "leaf1: vtysh -c \"show bgp neighbors 10.0.12.1 received-routes\"", "leaf1: vtysh -c \"show bgp neighbors 10.0.12.3 received-routes\""],
    "o_routes": ["leaf1: show ip route", "leaf1: show ip route 10.0.2.0/24"],
    "o_ecmp": ["leaf1: show ip route 10.0.2.0/24", "leaf1: vtysh -c \"show ip bgp 10.0.2.0/24\""],
    "o_journey": ["leaf1: vtysh -c \"show ip route 10.0.2.0/24\"", "leaf1: sonic-db-cli APPL_DB keys \"ROUTE_TABLE:10.0.2.0/24\"", "leaf1: sonic-db-cli ASIC_DB keys \"ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP_GROUP*\""],
    "o_timers": ["leaf1: vtysh -c \"show bgp neighbors 10.0.12.1\""],
    "o_messages": ["leaf1: vtysh -c \"show bgp neighbors 10.0.12.1\""],
    "o_dataplane": ["h1: ping -c 5 10.0.2.10", "leaf1: vtysh -c \"show bgp summary\"", "leaf1: show ip route 10.0.2.0/24"],
    "after_chaos": ["leaf1: vtysh -c \"show bgp summary\"", "leaf1: show ip route 10.0.2.0/24", "leaf1: show interfaces status", "leaf1: sonic-db-cli APPL_DB keys \"ROUTE_TABLE:10.0.2.0/24\"", "h1: ping -c 10 10.0.2.10", "leaf2: vtysh -c \"show bgp summary\""]
  },
  "chaos_ids": ["c_shut_one_link", "c_shut_both_links", "c_bgp_admin_shut", "c_hold_timer_blackhole", "c_withdraw_only", "c_wrong_asn", "c_clear_bgp", "c_stop_bgp_service", "c_link_flap"],
  "enabled_chaos": ["c_shut_one_link", "c_shut_both_links", "c_bgp_admin_shut", "c_hold_timer_blackhole", "c_clear_bgp", "c_stop_bgp_service"],
  "keywords": ["bgp", "ebgp", "ibgp", "peer", "peering", "neighbor", "session", "tcp 179", "autonomous system", "asn", "fsm", "state machine", "idle", "active", "established", "keepalive", "hold time", "hold timer", "timer", "fast fallover", "fallover", "advertise", "advertised", "receive", "received", "update", "withdraw", "nlri", "prefix", "route", "routing", "routing table", "rib", "fib", "next-hop", "nexthop", "longest prefix match", "lpm", "connected route", "static route", "best path", "bestpath", "ecmp", "multipath", "equal cost", "load balance", "hashing", "flow", "next-hop group", "open", "notification", "message", "control plane", "data plane", "reconverge", "reconvergence", "bgpd", "zebra", "fpmsyncd", "vtysh", "frr", "route_table"],
  "glossary_terms": ["routing", "routing table", "prefix", "next-hop", "longest-prefix match", "connected route (C)", "static route (S)", "BGP route (B)", "AS (autonomous system)", "eBGP", "BGP session", "FSM (finite state machine)", "Established", "keepalive", "hold time", "fast-fallover", "UPDATE", "NLRI", "withdraw", "advertise / receive", "best path", "ECMP", "multipath", "next-hop group", "flow hashing", "OPEN", "KEEPALIVE (message)", "NOTIFICATION", "control plane", "data plane", "bgpd", "zebra", "fpmsyncd", "RIB / FIB", "vtysh", "reconvergence"]
}
```
