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
