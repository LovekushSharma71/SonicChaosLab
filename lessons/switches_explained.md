# Lesson: switches_explained — Switches explained

## Meta

- **id:** `switches_explained`
- **difficulty:** intro
- **requires:** none (lab deployed and at baseline; no prior lesson)
- **target image:** docker-sonic-vs, branch **202405** (Containerlab topology: leaf1/leaf2 SONiC, h1–h4 Linux hosts)
- **devices used in this lesson:** `leaf1`, `h1` (leaf2/h3 appear only as silent endpoints)
- **lab prerequisites assumed by this lesson (bake into topology):**
  - h1 `eth1` = `10.0.1.10/24`, default gateway `10.0.1.1`, attached to leaf1 `Ethernet8`, access member of `Vlan10`
  - leaf1 `Vlan10` interface = `10.0.1.1/24`
  - **pinned host MACs** (needed for deterministic chaos restore): h1 `eth1` = `02:00:00:00:01:10`, h3 `eth1` = `02:00:00:00:02:10`
  - all ports MTU 9100, admin up at baseline
- **command execution targets:** `leaf1:` = docker exec into SONiC vs (root; `sudo` kept for fidelity with docs, harmless as root), `h1:` = docker exec into host container
- **source URLs relied on:**
  - SONiC CLI Reference (202405) — Interfaces: <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#interfaces>
  - SONiC CLI Reference (202405) — VLAN & FDB (`show vlan brief/config`, `config vlan …`, `show mac`, `show mac aging-time`, `sonic-clear fdb all`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#vlan--fdb>
  - SONiC CLI Reference (202405) — LLDP (`show lldp table/neighbors`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#lldp>
  - SONiC CLI Reference (202405) — ARP (`show arp`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#arp--ndp>
  - SONiC CLI Reference (202405) — Feature (`config feature state`, `show feature status`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#feature>
  - SONiC Architecture wiki (containers, Redis DBs, APPL_DB/ASIC_DB roles, lldp_syncd): <https://github.com/sonic-net/SONiC/wiki/Architecture>
  - IEEE 802.1AB (LLDP concept), RFC 826 (ARP concept) — concept grounding only, no commands
  - `sonic-db-cli` is part of sonic-utilities (not in the Command Reference; standard public tooling shipped in the image)

## Coverage Notes

- **TEACHES:** frames & MAC addresses; admin vs operational port status; VLANs, access ports, broadcast domains; MAC learning / flooding / aging (FDB lifecycle); LLDP neighbor discovery; ARP + default gateway (lite — only enough to explain why a ping populates the MAC table and where L2 ends); interface counters (intro); first peek at Redis (APPL_DB vs ASIC_DB as places where live tables reside).
- **REINFORCES:** nothing (first lesson).
- **ASSUMES:** general software literacy (key-value store, cache, daemon/process, container); ability to read a terminal table. No networking knowledge assumed.
- **PREVIEWS (named, deliberately not taught):** CONFIG_DB and the full config pipeline (Lesson 2); routing/BGP (Lesson 3 — the S8 boundary scenario is the on-ramp); MTU behavior (Lesson 4 — the MTU column is pointed at once).
- **DELIBERATELY OUT OF SCOPE:** spanning tree / loops (topology is loop-free), trunk engineering, IPv6, deep ARP states, QoS.

## Step Plan

| # | step id | kind | title | core-or-optional |
|---|---------|------|-------|------------------|
| 1 | t_ports | teach | S1: A switch is a decision machine — ports, links, admin vs operational | core |
| 2 | o_ports | observe | S1: See every port's real state (status, speed, MTU) | core |
| 3 | q_ports | qna | S1: Questions — ports & link state | core |
| 4 | t_lldp | teach | S2: How switches introduce themselves — LLDP | optional |
| 5 | o_lldp | observe | S2: Who is really plugged into what | optional |
| 6 | q_lldp | qna | S2: Questions — LLDP | optional |
| 7 | t_vlan | teach | S3: VLANs — one box, many isolated switches | core |
| 8 | o_vlan | observe | S3: Vlan10's members, tagging mode, gateway IP | core |
| 9 | q_vlan | qna | S3: Questions — VLANs & access ports | core |
| 10 | t_mac | teach | S4: The MAC table — learning by listening, flooding when ignorant | core |
| 11 | o_mac | observe | S4: Make the switch learn live (h1 pings gateway, FDB fills) | core |
| 12 | q_mac | qna | S4: Questions — MAC learning | core |
| 13 | t_age | teach | S5: Forgetting on purpose — aging and the cold-start table | optional |
| 14 | o_age | observe | S5: Read the aging clock; count entries | optional |
| 15 | q_age | qna | S5: Questions — aging & flooding | optional |
| 16 | t_redis | teach | S6: Where the tables really live — first peek at Redis | optional |
| 17 | o_redis | observe | S6: Hunt the learned MAC in ASIC_DB and APPL_DB; read an LLDP row | optional |
| 18 | q_redis | qna | S6: Questions — tables in Redis | optional |
| 19 | t_counters | teach | S7: Counters — the switch's diary | optional |
| 20 | o_counters | observe | S7: Watch RX/TX move when h1 talks | optional |
| 21 | q_counters | qna | S7: Questions — counters | optional |
| 22 | t_boundary | teach | S8: The edge of the L2 world — why h1 never "switches" to h3 | optional |
| 23 | o_boundary | observe | S8: Ping h3, then prove no h3 MAC was ever learned | optional |
| 24 | q_boundary | qna | S8: Questions — the L2/L3 boundary | optional |
| 25 | chaos | chaos_select | Pick one failure to inject (8 options) | core |
| 26 | q_impact | qna | Questions — interrogate the failure you injected | core |
| 27 | restore | restore | Heal the lab, verify baseline (re-ping, re-count) | core |

## Teach Sections

### SECTION: t_ports — S1: A switch is a decision machine — ports, links, admin vs operational

**Essence.** A switch is a box with one job: a message arrives on one port, and the box decides which port (or ports) it leaves on. Every scenario in this lesson examines one piece of that decision. Before any deciding can happen, the doors themselves must work — so we start with ports.

**Mechanism.** At this layer the unit of traffic is a **frame**: an envelope holding a destination address, a source address, and payload. Frames enter and exit through **ports** — physical or virtual sockets. A working connection between two ports is a **link**, and a link has two independent kinds of "up":

- **Admin status** — what you *asked for*. It is configuration: "this port should be enabled." Think of it as the desired state in a spec.
- **Operational status** — what *actually happened*. The port only becomes operationally up if the admin says up AND the other end cooperates (peer powered, connected, speaking). Command output abbreviates this column to "Oper."

Admin down forces the operational state down. Admin up guarantees nothing — it is a request, not a result. Whenever a network "doesn't work," the first split-second question is always: which of these two is down, on which end? That distinction — intent vs. reality — will follow you through this entire course.

**On SONiC.** The command you are about to run, `show interfaces status`, prints one row per port. Read four columns: the port name (Ethernet0, Ethernet4, Ethernet8 …), Oper, Admin, and MTU. In this lab, leaf1's Ethernet0 and Ethernet4 run to the other switch (leaf2), and Ethernet8 runs to the host h1. All three should show Oper up, Admin up. Note the MTU column reading 9100 on every port — ignore it today, but remember it exists: an entire later lesson is about what happens when that number lies to you.

**Boundaries.** Nothing here explains *how* the switch decides where frames go — that is the MAC table, three scenarios from now. And "up" does not mean "working": you will meet failures later where every status column looks perfect while traffic dies.

### SECTION: t_lldp — S2: How switches introduce themselves — LLDP

**Essence.** Devices on a link exchange standardized business cards so each side knows who is physically attached. The protocol is **LLDP** (Link Layer Discovery Protocol, IEEE 802.1AB). It moves no user traffic — it exists purely so that humans and tools can ask the switch "what is plugged into you?" and get a live, truthful answer instead of trusting a wiring diagram.

**Mechanism.** Every LLDP-speaking device periodically (typically every 30 seconds) sends a small frame out of each port containing: its own name, which of *its* ports this is, what kind of device it is, and a **TTL** (time-to-live — "you may trust this card for N seconds," typically 120). The receiver files the card in a **neighbor** table keyed by local port. Two properties matter:

1. Cards are *link-local*: a switch never forwards them onward. Your neighbor table only ever shows direct physical neighbors.
2. The table is *lease-based*: if cards stop arriving, the entry survives until its TTL runs out, then quietly disappears. LLDP therefore notices silence slowly — worth remembering when we later kill it on purpose.

**On SONiC.** LLDP runs in its own container (a theme you will meet properly in Lesson 2: SONiC is a fleet of containers). A helper process copies every received card into a live database table called LLDP_ENTRY_TABLE — your first hint that everything the CLI shows you is actually rows in a database. `show lldp table` prints the summary: LocalPort (where the card arrived), RemoteDevice (the neighbor's name), RemotePortID (which of the neighbor's ports faces you). `show lldp neighbors Ethernet0` shows one card in full detail, including the TTL. Expect leaf2 to appear twice — once via Ethernet0, once via Ethernet4 — because two parallel cables join the switches. The host h1 will usually NOT appear: plain Linux hosts don't speak LLDP unless someone installs a daemon.

**Boundaries.** LLDP is eyes, not hands: disabling it changes nothing about how traffic flows — a fact one of the chaos options lets you prove.

### SECTION: t_vlan — S3: VLANs — one box, many isolated switches

**Essence.** A **VLAN** (virtual LAN) makes one physical switch behave like several smaller, completely isolated switches. Each VLAN is just a number (1–4094) plus a set of member ports. Traffic entering a port in VLAN 10 can only ever exit other ports of VLAN 10 — as if the other VLANs were different boxes in a different building.

**Mechanism.** The switch stamps every incoming frame with the VLAN it belongs to, and all later decisions happen *inside* that VLAN only. How the stamp is decided depends on the port's membership mode. An **access port** is the simple kind: it belongs to exactly one VLAN, and the host connected to it sends completely ordinary frames — the switch stamps them itself and strips the stamp on the way out. The host never knows VLANs exist. (The other mode — tagged, or "trunk" — carries frames for many VLANs with the stamp written inside each frame; this lab doesn't use it, but you'll see the column that would say so.) The practical consequence: a VLAN *is* a **broadcast domain** — the exact set of ports a shouted (broadcast) frame reaches. Broadcast domains are the blast radius of L2: floods, broadcasts, and mistakes all stop at the VLAN edge.

**On SONiC.** `show vlan brief` prints one block per VLAN: its ID, the IP address configured on it (more on that in a moment), member ports, and each member's tagging mode. `show vlan config` shows the same membership as flat rows — easier to eyeball the Mode column. In this lab: leaf1 has Vlan10 with exactly one member, Ethernet8, untagged — that's h1's access port. And Vlan10 carries the address 10.0.1.1/24: the switch itself owns an IP *inside* the VLAN. Hold that thought — it is the secret door out of the L2 world, and scenario S8 walks through it.

**Boundaries.** VLANs isolate; they never connect. Getting from Vlan10 to Vlan20 (h1 to h3) requires routing — Lesson 3's whole subject. Also: membership is configuration, not discovery; the switch will happily enforce a wrong VLAN assignment, which is exactly what one of the chaos options exploits.

### SECTION: t_mac — S4: The MAC table — learning by listening, flooding when ignorant

**Essence.** Here is the actual decision. The switch keeps a hash map — key: (VLAN, destination address), value: exit port — and it builds that map entirely by eavesdropping on traffic. Nobody configures it. This map is the **MAC table**, also called the **FDB** (forwarding database), and it is the beating heart of every Ethernet switch on the planet.

**Mechanism.** A **MAC address** is the 48-bit identity of a network interface, written like `02:00:00:00:01:10` — every frame carries a source MAC and a destination MAC. The algorithm has two halves:

- **Learning (write path):** every arriving frame is testimony — "source MAC S just arrived on port P." The switch writes (VLAN, S) → P into the table. That's it. Hosts teach the switch where they are simply by talking.
- **Forwarding (read path):** look up the destination MAC. Hit → forward out exactly that one port. Miss → **flooding**: copy the frame out *every* port in the VLAN except the one it came from. Flooding looks wasteful but is self-healing: the destination's *reply* is a new frame whose source is the missing MAC — which the learning half immediately writes down. One round trip, and the ignorance is gone.

Note what the table never contains: IP addresses. Switches forward on MACs within a VLAN; they are completely blind to IP.

**On SONiC.** `show mac` prints the live table: Vlan, MacAddress, Port, and Type (Dynamic = learned; Static = pinned by an operator — rare) plus a total count at the bottom; `show mac -c` prints just the count. The observe step choreographs the learning live from a guaranteed blank slate: it first wipes the learned table (a harmless reset — the table is a cache, and you will study exactly this action as chaos option B), shows the empty table, then h1 pings its gateway (10.0.1.1 — the switch's own Vlan10 address), then looks again. Expect a new Dynamic entry: h1's MAC (pinned in this lab to 02:00:00:00:01:10) in Vlan 10 on Ethernet8. You will have watched a switch learn.

**Boundaries.** The table is a cache, not a config — and caches expire. That's the next scenario. And one subtlety for later: a *wrong* entry is far worse than a missing one (missing → flood → self-heals; wrong → silent blackhole). Chaos option 8 weaponizes exactly this.

### SECTION: t_age — S5: Forgetting on purpose — aging and the cold-start table

**Essence.** MAC table entries are leases, not facts. Every dynamic entry expires after a period of silence, called the **aging time**. Forgetting is a feature: it is what lets hosts move.

**Mechanism.** Each dynamic entry carries a countdown that resets every time a frame from that MAC arrives. If the host goes quiet long enough, the countdown ends and the entry is deleted. The next frame *to* that host is then an **unknown unicast** — a frame addressed to a single MAC the table doesn't know — and it gets flooded, exactly like scenario S4 described, until the host's reply re-teaches the switch. Why forget at all? Because hosts move: unplug h1 from Ethernet8, plug it in elsewhere, and a permanent entry pointing at Ethernet8 would blackhole its traffic forever. Aging bounds how long any stale claim survives. The cost is negligible: relearning takes one round trip.

**On SONiC.** `show mac aging-time` prints the configured aging period in seconds; `show mac -c` gives the current entry count. Two things to notice in the observe step: the aging value itself (note it — it explains how long chaos aftermath lingers), and how small the table is in a 4-device lab. Chaos option B (`sonic-clear fdb all`) is simply "aging, everywhere, right now" — the observe/diff machinery will let you time how fast the table refills.

**Boundaries.** Static entries never age — they are operator promises, not observations. And do not confuse MAC aging with ARP timeouts: different table, different layer, different clock (ARP appears in S8).

### SECTION: t_redis — S6: Where the tables really live — first peek at Redis

**Essence.** On SONiC, every table you have looked at so far — interfaces, VLANs, MACs, LLDP neighbors — is not hidden inside a proprietary blob. Each is literally rows in **Redis**, an in-memory key-value database running on the switch. The `show` commands are pretty-printers. This is SONiC's superpower: if you can read Redis, you can read the switch's mind — and in this course you always will, right next to the CLI view.

**Mechanism.** SONiC runs several logical Redis databases, each with a number and a job. Two matter today:

- **APPL_DB** (database 0) — tables that SONiC services *produce* as their working state for the forwarding layer. The LLDP card file lives here: keys shaped like `LLDP_ENTRY_TABLE:Ethernet0`, one per port with a neighbor.
- **ASIC_DB** (database 1) — the exact objects the switching chip (here: a *virtual* chip, since this is a software switch) has been told to hold, in a vendor-neutral vocabulary called SAI. Learned MACs surface here as `…SAI_OBJECT_TYPE_FDB_ENTRY…` keys — the chip reports what it learned, and this database is the record.

There is a third name to file away: **CONFIG_DB** (database 4) — your *intent*, everything you configure. Lesson 2 is entirely about how intent flows from CONFIG_DB through the other databases down to the chip. Today we only peek at the two "live state" databases.

**On SONiC.** `sonic-db-cli <DBNAME> keys "<pattern>"` lists matching keys; `sonic-db-cli <DBNAME> hgetall "<key>"` dumps one row's fields. The observe step first has h1 ping the gateway (so a fresh MAC entry certainly exists), then hunts for FDB entries in ASIC_DB *and* in APPL_DB's FDB_TABLE, then dumps one LLDP row. On this virtual switch expect the dynamic MAC to appear in ASIC_DB; APPL_DB's FDB_TABLE is the channel used for *programmed* entries and may be empty here — seeing which database holds what is the entire point of the exercise.

**Boundaries.** Reading Redis is always safe. *Writing* raw keys bypasses every validation layer SONiC has — that is a chaos experiment (option 8 does it deliberately), never a habit.

### SECTION: t_counters — S7: Counters — the switch's diary

**Essence.** Status tells you whether a port *could* work; **counters** tell you whether it actually *did*. Every port keeps running totals of frames in and out, plus buckets for the ones it had to throw away. When a network misbehaves subtly, counters are usually where the truth leaks out.

**Mechanism.** Counters are monotonic: they only ever increase, so you never read one — you read it twice and subtract. The vocabulary: **RX** = received (into the switch), **TX** = transmitted (out of the switch), and per-direction buckets for OK frames, errors (damaged frames), **drops** (healthy frames the switch discarded — no room, no rule, no VLAN…), and overruns. A healthy quiet port shows OK counters creeping and error/drop buckets frozen. A drop bucket that moves while users complain is a smoking gun.

**On SONiC.** Counters are polled from the (virtual) chip into a dedicated database (COUNTERS_DB — Lesson 2 territory) every few seconds, so freshly generated traffic can take a moment to appear in the output. The observe step uses a clean experimental pattern you should steal for real life: `sonic-clear counters` sets a personal zero-point (it does not touch the switch's true totals — it just makes *your* next reading start from zero), then h1 generates a known burst of pings, then `show interfaces counters` — watch RX_OK and TX_OK move on Ethernet8, and confirm RX_DRP / RX_ERR stay flat.

**Boundaries.** On this virtual switch, counter freshness is best-effort — trends are trustworthy, exact per-packet accounting is not. Queue, PFC, and watermark counters exist on real systems and are out of scope here.

### SECTION: t_boundary — S8: The edge of the L2 world — why h1 never "switches" to h3

**Essence.** h1 (10.0.1.10, Vlan10 on leaf1) can reach h3 (10.0.2.10, Vlan20 on leaf2) — you will prove it with a ping — yet no switch ever "switches" a frame to h3. The frame's journey ends at the VLAN border, and something categorically different (routing) carries the payload onward. This scenario makes you *see* the border in the tables, and it is the doorway into Lesson 3.

**Mechanism.** Two ideas, both small:

- **ARP** (Address Resolution Protocol): IP-speaking hosts need a MAC to put on the frame. ARP is the shouted question — "who has IP X? tell me your MAC" — sent as a broadcast, answered by the owner. Every host keeps a little cache of answers.
- **Default gateway:** before ARPing, a host compares the destination IP with its own subnet. Same subnet → ARP for the destination directly. *Different* subnet → don't even try; instead hand the packet to a designated local router, the default gateway, by ARPing for the *gateway's* IP and addressing the frame to the *gateway's* MAC.

10.0.2.10 is outside h1's 10.0.1.0/24, so h1 wraps the packet for h3 inside a frame addressed to 10.0.1.1's MAC — leaf1's own Vlan10 interface. At Layer 2, h1 only ever converses with its gateway. h3's MAC never crosses into Vlan10, never gets learned, never appears.

**On SONiC.** The observe step pings h3 from h1 (it succeeds — the two switches route it; how they know the way is Lesson 3's flagship topic), then collects the evidence: `show mac` on leaf1 — h1's MAC is there, h3's is nowhere; `show arp` on leaf1 — the switch resolved 10.0.1.10 on Vlan10 (the gateway keeps its own ARP cache, because routing made it a *sender* of frames toward h1); and h1's own neighbor cache (`ip neigh show`) — containing the gateway, not h3. Working ping + absent MAC = the packet was routed, not switched. That is the boundary.

**Boundaries.** How leaf1 knows that 10.0.2.0/24 lives behind leaf2 — the routing table, BGP, reconvergence — is deliberately left dark until Lesson 3. Today you only need to know the L2 world has an edge, and you've now stood on it.

## Commands

Convention: `<target>: <command>` — targets are `leaf1` (SONiC CLI via docker exec) and `h1` (host container). Commands run top-to-bottom; the app parses what it can and shows raw output for the rest.

### Per-scenario observe commands

#### o_ports
- `leaf1: show interfaces status`

#### o_lldp
- `leaf1: show lldp table`
- `leaf1: show lldp neighbors Ethernet0`

#### o_vlan
- `leaf1: show vlan brief`
- `leaf1: show vlan config`

#### o_mac  *(mutating-lite: wipes the self-repopulating MAC cache + generates traffic; no config change; no cleanup needed)*
- `leaf1: sonic-clear fdb all`
- `leaf1: show mac`
- `leaf1: show mac -c`
- `h1: ping -c 3 10.0.1.1`
- `leaf1: show mac`
- `leaf1: show mac -c`

#### o_age
- `leaf1: show mac aging-time`
- `leaf1: show mac -c`

#### o_redis  *(mutating-lite: generates traffic; no config change)*
- `h1: ping -c 3 10.0.1.1`
- `leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_FDB_ENTRY*"`
- `leaf1: sonic-db-cli APPL_DB keys "FDB_TABLE*"`
- `leaf1: sonic-db-cli APPL_DB keys "LLDP_ENTRY_TABLE*"`
- `leaf1: sonic-db-cli APPL_DB hgetall "LLDP_ENTRY_TABLE:Ethernet0"`  `[VERIFY-ON-LAB: exact key name/separator — take it from the keys listing above]`

#### o_counters  *(mutating-lite: resets the user-view counter baseline + generates traffic)*
- `leaf1: sonic-clear counters`
- `h1: ping -c 10 10.0.1.1`
- `leaf1: show interfaces counters`

#### o_boundary  *(mutating-lite: generates traffic)*
- `h1: ping -c 3 10.0.2.10`
- `h1: ip neigh show`
- `leaf1: show mac`
- `leaf1: show arp`

### After-chaos commands

Run after any injection (the engine diffs these against baseline facts):

- `leaf1: show interfaces status`
- `leaf1: show vlan brief`
- `leaf1: show mac`
- `leaf1: show mac -c`
- `leaf1: show lldp table`
- `leaf1: show ip interfaces`
- `h1: ping -c 5 10.0.1.1`
- `h1: ping -c 5 10.0.2.10`
- `leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_FDB_ENTRY*"`

### Vocabulary

Globs defining on-topic experiment commands for this lesson (safety/read-only gate applies separately):

```
show interfaces status*
show interfaces description*
show interfaces counters*
show interfaces switchport*
show mac*
show vlan*
show lldp*
show arp*
show ip interfaces*
show feature status*
sonic-db-cli APPL_DB *
sonic-db-cli ASIC_DB *
sonic-db-cli CONFIG_DB *
sonic-db-cli STATE_DB *
redis-cli *
ping*
ip neigh*
ip link show*
ip addr show*
sonic-clear counters
sonic-clear fdb*
config interface shutdown*
config interface startup*
config vlan*
config interface ip*
config feature state lldp*
docker ps*
```

## Chaos Options

All options are restorable to baseline. "Injection-failed tell" = how the app (or user) detects that the injection itself did not take on sonic-vs, as distinct from the failure working as intended.

---

**1. id: `c_shut_access` — "Unplug the host: shut h1's access port" (ANCHOR A)**
- **type:** config · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: sudo config interface shutdown Ethernet8`
- **restore:**
  - `leaf1: sudo config interface startup Ethernet8`
  - `h1: ping -c 3 10.0.1.1`  *(repopulate FDB/ARP, prove recovery)*
- **expected effects (words):** Ethernet8 flips to Admin down and Oper down within a second or two. h1→gateway ping goes to 100% loss immediately. h1's FDB entry disappears (flush on port-down) `[VERIFY-ON-LAB: whether vs flushes immediately or entry lingers until aging]`. VLAN membership, LLDP entries on Ethernet0/4, and inter-switch state are untouched. After restore: link returns Oper up within seconds; first ping re-ARPs and re-learns; loss ends immediately after link-up.
- **plan-B variant:** shut from the *host* side instead — `h1: ip link set eth1 down` / `... up`. Same user impact, but leaf1 shows Ethernet8 Admin **up** / Oper **down** — the perfect admin-vs-operational teaching contrast.
- **injection-failed tell:** `show interfaces status` still lists Ethernet8 Admin=up after inject → the config write did not land (retry; check CONFIG_DB PORT|Ethernet8 admin_status).

---

**2. id: `c_clear_fdb` — "Total amnesia: flush the MAC table" (ANCHOR B)**
- **type:** config · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: sonic-clear fdb all`
- **restore:** *(no config to undo — restore = force relearn and verify)*
  - `h1: ping -c 3 10.0.1.1`
  - `leaf1: show mac -c`
- **expected effects (words):** MAC entry count drops to zero (or near zero) instantly. User traffic is barely dented: the next frame to an unknown MAC floods, the reply relearns, so expect zero or at most one lost ping `[VERIFY-ON-LAB: first-ping loss after flush]`. This is the "failure" that isn't one — it teaches that the FDB is a disposable cache.
- **plan-B variant:** natural aging instead of a flush — stop all traffic from h1 and wait out the aging time (slow; shows the same lifecycle without a command).
- **injection-failed tell:** `show mac -c` unchanged immediately after inject → clear didn't execute.

---

**3. id: `c_vlan_member_del` — "Kicked out of the club: remove Ethernet8 from Vlan10"**
- **type:** config · **risk:** medium · **enabled:** true
- **inject:**
  - `leaf1: sudo config vlan member del 10 Ethernet8`
- **restore:**
  - `leaf1: sudo config vlan member add -u 10 Ethernet8`
  - `h1: ping -c 3 10.0.1.1`
- **expected effects (words):** Port stays Oper/Admin up — status looks perfect. But h1's untagged frames now arrive at a port with no VLAN membership and are dropped at ingress; h1→gateway ping goes to 100% loss within a second. h1's FDB entry vanishes (membership gone) and never relearns. First taste of "up but broken."
- **plan-B variant:** flip the port out of L2 entirely: `leaf1: sudo config switchport mode routed Ethernet8` (restore: `... mode access Ethernet8` then re-add membership) `[VERIFY-ON-LAB: mode change ordering constraints with existing membership]`.
- **injection-failed tell:** `show vlan brief` still lists Ethernet8 under VLAN 10 → deletion didn't land.

---

**4. id: `c_wrong_vlan` — "Right port, wrong neighborhood: move Ethernet8 to Vlan50"**
- **type:** config · **risk:** medium · **enabled:** true
- **inject:**
  - `leaf1: sudo config vlan add 50`
  - `leaf1: sudo config vlan member del 10 Ethernet8`
  - `leaf1: sudo config vlan member add -u 50 Ethernet8`
- **restore:**
  - `leaf1: sudo config vlan member del 50 Ethernet8`
  - `leaf1: sudo config vlan member add -u 10 Ethernet8`
  - `leaf1: sudo config vlan del 50`
  - `h1: ping -c 3 10.0.1.1`
- **expected effects (words):** Same user pain as option 3 (gateway ping 100% loss) but *different evidence*: the port is up AND the switch happily learns h1's MAC — in VLAN 50. `show mac` shows the entry alive in the wrong VLAN; Vlan50 has no gateway IP, so ARP for 10.0.1.1 dies unanswered inside an isolated broadcast domain. Teaches: learning ≠ working; VLANs isolate ruthlessly.
- **plan-B variant:** leave Ethernet8 in Vlan10 but add it as **tagged** instead of untagged (`config vlan member del 10 Ethernet8` then `config vlan member add 10 Ethernet8`): host sends untagged, switch now expects tagged — mismatch with subtler symptoms `[VERIFY-ON-LAB: untagged-frame handling on tagged-only member in vs]`.
- **injection-failed tell:** `show vlan brief` doesn't show VLAN 50 with member Ethernet8 → sequence failed midway (check which step).

---

**5. id: `c_gw_ip_remove` — "The switch part works, the gateway is gone"**
- **type:** config · **risk:** medium · **enabled:** true
- **inject:**
  - `leaf1: sudo config interface ip remove Vlan10 10.0.1.1/24`
- **restore:**
  - `leaf1: sudo config interface ip add Vlan10 10.0.1.1/24`
  - `h1: ping -c 3 10.0.1.1`
- **expected effects (words):** Everything L2 stays healthy: port up, VLAN membership intact, LLDP fine, h1's MAC learned (its ARP broadcasts still flood and still teach). But nobody owns 10.0.1.1 anymore, so h1's ARP goes unanswered and both pings (to gateway and to h3) fail — h1→h3 dies because the escape door out of Vlan10 is gone. The subtle one: every L2 table looks perfect; only `show ip interfaces` betrays the missing address. Loss is immediate either way; only the error signature differs — with a warm ARP cache h1 keeps transmitting echo requests that nothing answers (silent timeouts), with a cold cache the failure surfaces one step earlier as unanswered ARP ("Destination Host Unreachable") `[VERIFY-ON-LAB: confirm both signatures]`.
- **plan-B variant:** remove Vlan20's IP on leaf2 instead — breaks only the h3-side return path; h1's gateway pings keep working while end-to-end dies (splits the failure between L2-local health and end-to-end reachability).
- **injection-failed tell:** `show ip interfaces` on leaf1 still lists Vlan10 with 10.0.1.1/24 → removal didn't land.

---

**6. id: `c_lldp_stop` — "Silence the introductions: disable LLDP"**
- **type:** service · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: sudo config feature state lldp disabled`
- **restore:**
  - `leaf1: sudo config feature state lldp enabled`
- **expected effects (words):** Zero data-plane impact — both pings keep working at 0% loss the whole time (the proof that discovery ≠ forwarding). leaf1's lldp container stops (visible via `docker ps` / `show feature status`); leaf1's own neighbor table empties immediately or shortly after container stop `[VERIFY-ON-LAB: local table behavior on stop]`. The interesting slow burn happens on the *peer*: leaf2 keeps showing leaf1 as a neighbor until the TTL runs out (~120 s) — silence is detected on a delay `[VERIFY-ON-LAB: exact ageout timing on vs]`. After restore, entries reappear within one announcement interval (~30 s).
- **plan-B variant:** `leaf1: sudo systemctl stop lldp` / `sudo systemctl start lldp` (service-level instead of feature-level — same effect, different management surface).
- **injection-failed tell:** `show feature status` still lists lldp enabled, or the lldp container still runs in `docker ps` → feature change didn't land.

---

**7. id: `c_mac_flap` — "Identity crisis: h1 keeps changing its MAC"**
- **type:** churn · **risk:** medium · **enabled:** false  *(host-side learn-latency behavior on vs unverified)*
- **inject:**
  - `h1: sh -c 'for i in 1 2 3 4 5; do ip link set eth1 down; ip link set eth1 address 02:00:00:00:01:1$i; ip link set eth1 up; sleep 1; ping -c 1 -W 1 10.0.1.1; done'`
- **restore:**
  - `h1: sh -c 'ip link set eth1 down; ip link set eth1 address 02:00:00:00:01:10; ip link set eth1 up'`
  - `h1: ping -c 3 10.0.1.1`
- **expected effects (words):** The FDB churns: entry count stays ~1 for Ethernet8 but the MAC value keeps changing — last writer wins. Old identities linger until aging removes them, so intermediate reads may show several 02:00:00:00:01:1x entries `[VERIFY-ON-LAB: whether vs keeps multiple or replaces]`. Gateway ARP re-resolves each flap; expect a lost ping per identity change. Teaches: the FDB tracks *claims*, not truth — whoever spoke last owns the entry.
- **plan-B variant:** create a macvlan sub-interface on h1 with a second MAC and alternate pings between the two identities (no link flapping — pure table churn).
- **injection-failed tell:** `show mac` never shows any 02:00:00:00:01:1x MAC other than the baseline → host MAC changes aren't reaching the switch (check h1 link state).

---

**8. id: `c_static_mac_wrong_port` — "Poisoned map: pin h1's MAC to the wrong port"**
- **type:** config · **risk:** high · **enabled:** false  *(no CLI for static MAC in 202405; raw DB write; vs support unverified)*
- **inject:**
  - `leaf1: sonic-db-cli CONFIG_DB hset "FDB|Vlan10|02:00:00:00:01:10" port Ethernet0`
  - `leaf1: sonic-db-cli CONFIG_DB hset "FDB|Vlan10|02:00:00:00:01:10" type static`  `[VERIFY-ON-LAB: CONFIG_DB static-FDB key format and whether vs consumes it at all]`
- **restore:**
  - `leaf1: sonic-db-cli CONFIG_DB del "FDB|Vlan10|02:00:00:00:01:10"`
  - `leaf1: sonic-clear fdb all`
  - `h1: ping -c 3 10.0.1.1`
- **expected effects (words):** If the write is consumed: `show mac` gains a Static entry claiming h1 lives on Ethernet0 (the inter-switch link). Frames *to* h1 now exit the wrong port and vanish; h1's own frames still arrive but the static entry outranks learning, so the blackhole persists — the "wrong is worse than missing" lesson made real. Gateway→h1 direction dies while h1→switch traffic keeps flowing: asymmetric, silent, everything "up."
- **plan-B variant:** poison from the traffic side instead — from h3's L2 domain this is not reachable in this topology, so the realistic alternative is a gratuitous-ARP style claim: give a scratch container on Vlan10 h1's IP with a different MAC and let it talk `[VERIFY-ON-LAB: requires an extra Vlan10 attachment; skip unless topology grows]`.
- **injection-failed tell:** no Static-type row ever appears in `show mac` after inject → the raw CONFIG_DB write was ignored (expected risk on vs); the option self-reports as "injection not effective" rather than as a network failure.

---

## Observe

Parsers available: `interface_status, mac_table, lldp_neighbors, vlan_membership, bgp_neighbors, route_count, ping_loss, redis_keys, propagation_lag`. New parsers are marked.

### Happy-path fact map

| scenario (observe id) | parser(s) | key fields to extract | notes |
|---|---|---|---|
| o_ports | interface_status | per port: name, oper, admin, speed, mtu — anchor on Ethernet0/4/8 | assert all three oper=up admin=up mtu=9100 |
| o_lldp | lldp_neighbors | per row: local_port, remote_device, remote_port_id | expect ≥2 rows (leaf2 via Ethernet0 and Ethernet4); h1 normally absent |
| o_vlan | vlan_membership | vlan_id, ip_address, members[], tagging per member | expect vlan 10, ip 10.0.1.1/24, member Ethernet8 untagged |
| o_mac | mac_table (post-wipe + post-ping), ping_loss | entries[]: vlan, mac, port, type; count; ping: tx, rx, loss% | count 0 after wipe, then new Dynamic entry 02:00:00:00:01:10 → Ethernet8 in vlan 10; loss ≈0% |
| o_age | mac_table; **NEW-PARSER: mac_aging_time** (single line → integer seconds) | aging_seconds; count | aging value baseline-recorded `[VERIFY-ON-LAB: default aging value on vs]` |
| o_redis | redis_keys | per query: db, pattern, key_count, sample_keys[] | expect ≥1 FDB key in ASIC_DB after ping; APPL_DB FDB_TABLE may be 0 `[VERIFY-ON-LAB]`; ≥2 LLDP_ENTRY_TABLE keys |
| o_counters | **NEW-PARSER: interface_counters** (per port: rx_ok, tx_ok, rx_err, rx_drp, tx_err, tx_drp) | Ethernet8 rx_ok/tx_ok deltas; error/drop buckets | counters may lag one poll cycle on vs `[VERIFY-ON-LAB: poll interval / freshness]`; optional scenario — parser can ship later, raw display acceptable meanwhile |
| o_boundary | ping_loss, mac_table; **NEW-PARSER: arp_table** (rows: ip, mac, iface) from `show arp`; h1 `ip neigh show` displayed raw | ping h1→h3 loss 0%; mac_table contains h1's MAC only (no 02:00:00:00:02:10); arp_table has 10.0.1.10 on Vlan10 | punchline is the *absence* of h3's MAC — diff logic must support asserting absence |

### After-chaos fact map (same command set for all options; per-option focus + recovery)

| chaos id | facts that should CHANGE | facts that should NOT change | measure_recovery |
|---|---|---|---|
| c_shut_access | Ethernet8 Admin+Oper→down; ping(gw) 100%; ping(h3) 100%; mac_table loses h1 entry `[VERIFY-ON-LAB: flush-on-down]`; ASIC_DB FDB keys shrink | vlan_membership; lldp (Ethernet0/4); Ethernet0/4 status | yes — time from `startup` to first successful ping |
| c_clear_fdb | mac count →0 momentarily | interface_status; vlan; lldp; ping ≈0% loss | yes — time to count ≥1 after restore ping |
| c_vlan_member_del | vlan_membership loses Ethernet8; ping(gw+h3) 100%; mac entry gone | interface_status (all up!); lldp | yes |
| c_wrong_vlan | vlan_membership: new vlan 50 w/ Ethernet8; mac entry present but vlan=50; ping 100% | interface_status; lldp | yes |
| c_gw_ip_remove | vlan_membership ip field empty; ping(gw) →100% immediately; ping(h3) 100% | interface_status; mac_table (h1 entry persists via ARP retries); lldp | yes |
| c_lldp_stop | lldp_neighbors on leaf1 →0 rows `[VERIFY-ON-LAB]`; feature state | ping 0% loss both probes; mac_table; vlan; interface_status | no (ageout too slow to time usefully) |
| c_mac_flap | mac_table entry MAC value churn; transient ping loss | interface_status; vlan; lldp | no |
| c_static_mac_wrong_port | mac_table gains Static row (mac→Ethernet0); ping(gw) drops (return path dead) | interface_status; vlan; lldp | yes |

## Knowledge Card

*(Consumed by the runtime LLM for EXPLAIN moments. No outputs here — field meanings and expectations in words only.)*

### Objective

The learner can explain how an Ethernet switch decides where a frame goes (VLAN membership + MAC-table lookup, with flooding as the miss path), can read that machinery live on SONiC (`show interfaces status / vlan / mac / lldp / arp` and the Redis tables beneath them), and can reason about the difference between intent (admin, config) and reality (operational status, learned state) when a failure is injected.

### In-scope subtopics

Port/link state (admin vs operational), frames and MAC addresses, VLANs and access ports, broadcast domains, MAC learning/flooding/aging, LLDP discovery and TTL ageout, ARP and default gateway (only as needed for the ping choreography and the L2/L3 boundary), interface counters (RX/TX/drops, poll-based freshness), APPL_DB and ASIC_DB as live-state stores, `sonic-db-cli` reads, effects and recovery of the eight chaos options.

### Key concepts (one sentence each)

1. A switch forwards frames by looking up (VLAN, destination MAC) in a table it builds purely by recording the source MAC and arrival port of every frame it sees.
2. A lookup miss is not an error: the frame floods to all ports in the VLAN, and the destination's reply immediately teaches the switch the missing entry.
3. Admin status is a request written in config; operational status is the achieved reality — a port that is admin-down can never be operationally up, while admin-up guarantees nothing.
4. A VLAN is a numbered partition of the switch, an access port belongs to exactly one VLAN with the host unaware, and a VLAN is precisely one broadcast domain.
5. Dynamic MAC entries are leases refreshed by traffic and evicted after the aging time, so stale claims self-destruct and hosts can move.
6. A wrong MAC entry is worse than a missing one: missing floods and self-heals, wrong forwards into a silent blackhole.
7. LLDP is periodic link-local self-introduction with a TTL lease — it observes the topology but never carries or influences user traffic.
8. Hosts sending to another subnet address the *frame* to their default gateway's MAC (resolved via ARP), so the far host's MAC never appears in the local L2 domain.
9. On SONiC every table the CLI shows is rows in Redis: APPL_DB holds service-produced state (e.g., LLDP_ENTRY_TABLE), ASIC_DB holds the chip-level objects (e.g., learned FDB entries), CONFIG_DB holds intent.
10. Counters are monotonic and polled: read twice, subtract, and treat a moving drop/error bucket as the loudest signal a status table will never give you.

### Command field meanings

- **`show interfaces status`** — one row per port. Interface: SONiC port name. Lanes: internal chip lane IDs (ignore on vs). Speed: configured/negotiated rate. MTU: max frame payload size the port accepts (baseline 9100 here). Alias: alternate naming (platform-dependent). Oper: actual link state (up/down). Admin: configured intent (up/down). Type/Asym PFC: media/QoS details, out of scope.
- **`show lldp table`** — summary of the neighbor card-file. LocalPort: where the announcement arrived. RemoteDevice: neighbor's advertised hostname. RemotePortID: which of the *neighbor's* ports faces us. Capability: letter codes — R router, B bridge/switch, O other. RemotePortDescr: neighbor's free-text port description. Trailing line: total entry count.
- **`show lldp neighbors <port>`** — one card in detail: ChassisID (neighbor's MAC-based identity), SysName, SysDescr (OS/version string), TTL (seconds the entry may be trusted without refresh), MgmtIP if advertised, capability flags, PortID/PortDescr.
- **`show vlan brief`** — per VLAN: VLAN ID; IP Address if a VLAN interface owns one (10.0.1.1/24 on Vlan10 = h1's gateway); Ports: members; Port Tagging: untagged (access-style) or tagged; DHCP Helper / Proxy ARP: unused here (expect empty/disabled).
- **`show vlan config`** — same membership as flat rows: Name (VlanNN), VID (number), Member (port), Mode (untagged/tagged).
- **`show mac`** — the FDB. No.: row index. Vlan: VLAN the entry lives in. MacAddress: the learned/pinned MAC. Port: exit port for frames *to* that MAC. Type: Dynamic (learned, ages out) or Static (operator-pinned, never ages). Footer: total count. `show mac -c`: count only.
- **`show mac aging-time`** — single line stating the aging period in seconds for dynamic entries.
- **`show arp`** — the switch's own IP→MAC resolutions (it needs them because its VLAN interface makes it a traffic *sender*). Address: neighbor IP. MacAddress: resolved MAC. Iface: port/VLAN the neighbor was seen on. Vlan: VLAN ID when applicable.
- **`show ip interfaces`** — L3 addresses owned by the switch per interface, with Admin/Oper — the only table that betrays chaos option 5.
- **`show interfaces counters`** — per port: STATE (U=up, D=down, X=disabled); RX_OK/TX_OK: good frames in/out; RX_BPS/TX_BPS + UTIL: rates (may read N/A between polls on vs); RX_ERR/TX_ERR: damaged frames; RX_DRP/TX_DRP: healthy frames discarded; RX_OVR/TX_OVR: overruns. `sonic-clear counters` zeroes the *user's* view only.
- **`sonic-db-cli <DB> keys "<pat>"`** — lists keys matching the glob in that logical DB; an empty result means zero matching rows, not an error. `hgetall "<key>"` dumps one row as field/value pairs.
- **`ping -c N <ip>`** (h1) — N probes; read transmitted, received, % loss, and rtt min/avg/max. Non-zero exit with partial loss is data, not tool failure.
- **`ip neigh show`** (h1) — the host's ARP cache: IP, device, MAC (`lladdr`), and a state word (REACHABLE/STALE/etc. — treat anything with a MAC as "resolved").

### Healthy-state expectations (baseline, in words)

- Ethernet0, Ethernet4, Ethernet8 on leaf1: Oper up, Admin up, MTU 9100.
- `show vlan brief`: exactly VLAN 10, IP 10.0.1.1/24, member Ethernet8, untagged.
- `show lldp table`: leaf2 visible twice (via Ethernet0 and Ethernet4) with its port IDs; typically no entry for Ethernet8 (h1 doesn't speak LLDP); entry count ≥2.
- `show mac` after any h1 ping: at least one Dynamic entry — 02:00:00:00:01:10, Vlan 10, Ethernet8. Before any traffic (or after aging), the table may legitimately be empty.
- `show mac aging-time`: a positive number of seconds `[VERIFY-ON-LAB: capture the default and pin it in this card]`.
- Redis: ASIC_DB has ≥1 SAI_OBJECT_TYPE_FDB_ENTRY key after a ping; APPL_DB has ≥2 LLDP_ENTRY_TABLE keys; APPL_DB FDB_TABLE expected empty on vs `[VERIFY-ON-LAB]`.
- h1→10.0.1.1 and h1→10.0.2.10: 0% loss, sub-10 ms rtt typical for containers `[VERIFY-ON-LAB: typical rtt]`.
- Counters: RX_OK/TX_OK on Ethernet8 increase by roughly the ping count (± poll lag); RX_DRP/RX_ERR flat at ~0.

### Expected chaos effects per option (timings included)

- **c_shut_access:** Admin+Oper down on Ethernet8 within ~1–2 s of inject; both pings 100% loss immediately; FDB entry for h1 gone `[VERIFY-ON-LAB: immediate flush vs aging]`; everything else static. Recovery: Oper up within ~1–5 s of startup `[VERIFY-ON-LAB]`, first post-recovery ping succeeds (it re-ARPs and re-teaches the FDB in the same round trip).
- **c_clear_fdb:** count→0 instantly; ping loss ≈0 (at most the first probe) `[VERIFY-ON-LAB]`; relearn occurs on the first frame after flush — recovery time ≈ one ping interval. The correct "explanation" emphasizes the non-event: flooding absorbed the failure.
- **c_vlan_member_del:** loss 100% starting within ~1 s; port status stays perfect — the diff's most interesting row is the *unchanged* interface_status next to the vanished membership; no relearn while injected. Recovery: membership re-add restores forwarding on the next ARP (~1 s).
- **c_wrong_vlan:** loss 100%; MAC *is* learned but with vlan=50 — explanation must connect "entry exists" with "wrong broadcast domain, gateway unreachable"; recovery like option 3.
- **c_gw_ip_remove:** L2 facts all healthy; gateway ping fails immediately — with a warm h1 ARP cache the echoes go out and die unanswered (timeouts), with a cold cache ARP itself fails ("Destination Host Unreachable") `[VERIFY-ON-LAB: confirm both signatures]`; h1→h3 fails identically (no escape from Vlan10). Recovery: re-adding the IP restores replies on the next ARP exchange (seconds).
- **c_lldp_stop:** zero data-plane change (both pings 0% loss throughout — say so explicitly); leaf1's neighbor table empties `[VERIFY-ON-LAB: on stop or on TTL]`; peer-side entries persist ~TTL (≈120 s). Recovery: entries return within ~30 s (announcement interval) of re-enable.
- **c_mac_flap:** FDB entry for Ethernet8 changes identity across reads; ~1 lost ping per flap; old identities linger until aging `[VERIFY-ON-LAB: replace vs accumulate on vs]`. No recovery measurement — restore just pins the original MAC back.
- **c_static_mac_wrong_port:** if consumed, a Static row appears pointing h1's MAC at Ethernet0; gateway→h1 replies exit the wrong port so ping shows 100% loss while *nothing else changes at all* — the purest "up but broken" in the lesson. If no Static row appears, report "injection ineffective on vs" (known limitation), not a network conclusion.

### Misconceptions (wrong → why it's wrong → correct)

1. **"Switches forward using IP addresses."** → The FDB keys on (VLAN, MAC); IP never enters the lookup. → Switches are L2: MAC in, MAC out; IP is the router's business (Lesson 3).
2. **"Admin up means the link works."** → Admin is written intent; a dead peer or host-side down leaves Oper down while Admin reads up. → Both must be up, and only the operational status reflects reality — always read the pair.
3. **"Someone has to configure the MAC table."** → It is populated exclusively by learning from source MACs; clearing it is routine and near-harmless. → It's a self-building cache; `sonic-clear fdb all` costs at most one flooded round trip.
4. **"Flooding means something is broken (a broadcast storm)."** → Unknown-unicast flooding is the designed miss-path and self-terminates via learning; storms require forwarding loops, which this loop-free topology cannot form. → Flooding is normal and momentary; storms are a different pathology, out of scope here.
5. **"Clearing the FDB causes an outage."** → The very next frame floods and the reply relearns; measured loss is ≈0. → The FDB is disposable state, and chaos option B proves it live.
6. **"VLANs need separate physical switches or cables."** → VLANs are logical partitions enforced at ingress/egress on one box. → One switch hosts many isolated broadcast domains; membership config is the only wall.
7. **"If the switch learned the host's MAC, connectivity must be fine."** → Learning happens per-VLAN; an entry in the wrong VLAN (option 4) coexists with 100% loss. → An FDB entry is evidence, never proof — check membership and the gateway too.
8. **"LLDP down = network down."** → LLDP is observation only; option 6 shows 0% loss with LLDP dead. → Losing LLDP blinds discovery, not forwarding.
9. **"The default gateway is some external router box."** → Here the gateway is the switch's own VLAN interface (10.0.1.1 on Vlan10). → One SONiC box is both the L2 switch and the L3 gateway — the boundary runs through the device, not between devices.

### Out-of-scope (redirect if asked)

Spanning tree/loop prevention, trunking design, QoS/buffers, IPv6, BGP/routing internals (Lesson 3), the config pipeline and orchagent/syncd (Lesson 2), MTU failures (Lesson 4), SONiC installation/platform bring-up, hardware ASIC specifics.

### Polite redirect line

"That's a great question, but it sits outside this lesson's Layer-2 scope — I'd genuinely recommend reading up on it separately (the SONiC wiki's Architecture page or any Ethernet-switching primer will treat it properly). Let's steer back to what leaf1 is showing us — pick one of the suggested questions or run another on-topic command."

## Glossary

- **switch** — device that forwards Ethernet frames between its ports based on MAC addresses within a VLAN.
- **frame** — the Layer-2 unit of transmission: destination MAC, source MAC, payload, checksum.
- **packet** — the Layer-3 (IP) unit of data; it travels *inside* frames, re-wrapped at every hop.
- **port** — a physical or virtual attachment point on the switch (Ethernet0, Ethernet8, …).
- **link** — an operational connection between two ports.
- **admin status** — configured intent for a port: enabled (up) or disabled (down).
- **operational status (Oper)** — the port's actual achieved state; requires admin up plus a cooperating far end.
- **MTU** — maximum transmission unit: the largest frame payload a port will carry (9100 baseline here; starring role in Lesson 4).
- **MAC address** — 48-bit identifier of a network interface, e.g. 02:00:00:00:01:10.
- **MAC table / FDB** — forwarding database mapping (VLAN, MAC) → port; built by learning, consumed by forwarding.
- **learning** — recording each frame's source MAC and arrival port into the FDB.
- **flooding** — copying a frame to all ports of its VLAN (except ingress) when the destination MAC is unknown.
- **unknown unicast** — a frame to a single MAC that has no FDB entry; the thing that gets flooded.
- **aging / aging time** — automatic expiry of dynamic FDB entries after a silence period.
- **static entry** — operator-pinned FDB row that never ages.
- **VLAN** — virtual LAN: numbered partition of a switch forming one isolated broadcast domain.
- **access port** — port belonging to exactly one VLAN, exchanging untagged frames with an unaware host.
- **tagged / untagged** — whether the VLAN number is carried inside frames on a port (tagged) or implied by membership (untagged).
- **broadcast domain** — the set of ports a broadcast frame reaches; identical to a VLAN's member set.
- **broadcast** — a frame addressed to every device in the broadcast domain (all-ones destination MAC).
- **ingress / egress** — direction relative to the switch: ingress = arriving on a port, egress = leaving through one.
- **LLDP** — Link Layer Discovery Protocol: periodic link-local self-announcements building a neighbor table.
- **neighbor (LLDP)** — a directly connected device known via received LLDP announcements.
- **TTL (LLDP)** — seconds a received announcement may be trusted before the entry expires.
- **ARP** — Address Resolution Protocol: broadcast question resolving an IP to a MAC, answered by the owner.
- **default gateway** — the local router a host hands packets to when the destination IP is outside its own subnet.
- **subnet** — a contiguous IP address range (e.g. 10.0.1.0/24) whose members reach each other directly, without a router.
- **VLAN interface (SVI)** — an IP address owned by the switch inside a VLAN (Vlan10 = 10.0.1.1/24), making the switch that VLAN's gateway.
- **router** — device that forwards *packets between* IP subnets; contrasted with a switch, which forwards frames within a VLAN.
- **counter** — monotonic per-port total (frames/bytes, errors, drops) read by subtraction between two samples.
- **RX / TX** — receive (into the switch) / transmit (out of the switch) directions for counters.
- **drop** — a healthy frame the switch discarded (no membership, no buffer, policy); counted per direction.
- **Redis** — in-memory key-value database; SONiC's shared memory for all state.
- **APPL_DB** — Redis DB 0: state produced by SONiC services for the forwarding layer (e.g., LLDP_ENTRY_TABLE, FDB_TABLE).
- **ASIC_DB** — Redis DB 1: the chip-level object store (SAI objects), including learned FDB entries.
- **CONFIG_DB** — Redis DB 4: operator intent; only name-dropped here, dissected in Lesson 2.
- **COUNTERS_DB** — Redis DB 2: polled counter values for ports and queues; named here, explored in Lesson 2.
- **SAI** — Switch Abstraction Interface: the vendor-neutral vocabulary ASIC_DB objects are written in (named only; Lesson 2).
- **sonic-db-cli** — SONiC utility for reading (and, dangerously, writing) the Redis databases from the shell.
- **ping / ICMP** — echo request/reply probe measuring reachability and loss between two IP endpoints.

## QnA

### Scope keywords (gate input; generous synonyms)

```
switch, switching, layer 2, l2, ethernet, frame, mac, mac address, mac table,
fdb, forwarding database, cam table, learn, learning, flood, flooding,
unknown unicast, broadcast, broadcast domain, vlan, access port, untagged,
tagged, trunk, membership, lldp, neighbor, discovery, ttl, link, port,
interface, admin, oper, operational, link up, link down, shutdown, startup,
aging, age out, expire, arp, arp table, ip neighbor, gateway, default gateway,
svi, vlan interface, counters, rx, tx, drops, errors, redis, appl_db, asic_db,
config_db, fdb_table, lldp_entry_table, sonic-db-cli, ping, icmp, loss,
ethernet0, ethernet4, ethernet8, vlan10, h1, leaf1
```

### Question bank (8 per qna step; TOP 3 marked ★; ordered easy → deep)

#### q_ports
1. ★ What is the difference between the Admin and Oper columns?
2. ★ Why can a port be Admin up but Oper down — what are the possible causes?
3. ★ Which ports on leaf1 go to leaf2 and which go to h1, and how would I check without a diagram?
4. What exactly is a frame, and how is it different from a packet?
5. Does admin-down on one end change what the *other* end's switch displays?
6. What does the MTU column mean, and why is it 9100 here instead of 1500?
7. If I shut a port and re-enable it, what has to happen before Oper reads up again?
8. Why does SONiC name ports Ethernet0/4/8 with gaps instead of 1,2,3?

#### q_lldp
1. ★ Why does leaf2 appear twice in the LLDP table?
2. ★ Why doesn't h1 show up as an LLDP neighbor?
3. ★ If I unplug a cable, how long until LLDP notices, and why the delay?
4. What's inside an LLDP announcement besides the device name?
5. Why are LLDP frames never forwarded to other ports?
6. What is the TTL in the detailed neighbor view, and who chooses it?
7. Could two switches disagree about being neighbors — one sees the other, but not vice versa?
8. Where does the CLI get this data — is the lldp container answering my command directly?

#### q_vlan
1. ★ What does "untagged" mean in the member list, and what would "tagged" change?
2. ★ Why does Vlan10 have an IP address — I thought switches don't have IPs?
3. ★ If I created Vlan20 on leaf1 and put a host in it, could it talk to h1? Why not?
4. What happens to a frame that arrives on a port with no VLAN membership?
5. Is VLAN 1 special? What VLANs exist on this switch right now?
6. How does the switch decide which VLAN an incoming frame belongs to on an access port?
7. What exactly is a broadcast domain, and how do I compute its size from this output?
8. Why do we say a VLAN "is" a broadcast domain rather than "has" one?

#### q_mac
1. ★ Walk me through exactly how h1's MAC ended up in this table — who wrote it?
2. ★ What happens to a frame whose destination MAC is not in the table?
3. ★ Why did pinging the *gateway* teach the switch about *h1* — isn't that backwards?
4. What does Type=Dynamic mean, and when would I ever see Static?
5. Why doesn't the switch store IP addresses in this table?
6. Can one MAC appear on two ports, or one port own many MACs?
7. What would the table look like if two hosts were attached to Ethernet8 through a dumb hub?
8. Is flooding a security problem — can a host in Vlan10 see frames meant for others?

#### q_age
1. ★ What is the aging time on this switch, and what exactly happens when it expires for an entry?
2. ★ Why forget entries at all — wouldn't a permanent table be faster?
3. ★ What's the worst case a user experiences right after an entry ages out?
4. Does normal traffic reset the aging countdown, or only some frame types?
5. How is MAC aging different from ARP cache expiry on h1?
6. Do static entries age? Why would anyone pin one?
7. If h1 moves from Ethernet8 to another port, how fast does the table catch up, aging aside?
8. Is `sonic-clear fdb all` dangerous on a production switch with thousands of MACs?

#### q_redis
1. ★ Which database actually holds the learned MAC we just created, and why that one?
2. ★ What's the difference between APPL_DB and ASIC_DB in one sentence each?
3. ★ If `show mac` and the Redis FDB keys disagreed, which would you trust and why?
4. What does the key naming convention (TABLE:key or TABLE|key) tell me?
5. Why is reading Redis safe but writing it dangerous?
6. What is SAI, and why are ASIC_DB keys so verbosely named?
7. Where would my *configuration* (like VLAN membership) live, as opposed to this live state?
8. Is there one Redis per container, or one shared Redis for the whole switch?

#### q_counters
1. ★ Why did we run sonic-clear counters first — what does it actually reset?
2. ★ Which counters should move during our ping burst, and by roughly how much?
3. ★ What does a rising RX_DRP with flat RX_ERR suggest, versus the reverse?
4. Why might the counters not show my pings immediately?
5. What's the difference between an error and a drop?
6. Why are counters monotonic instead of resettable rates?
7. Which port's TX counts my ping *replies* toward h1 — walk the path.
8. Can counters prove where a lost packet died, or only narrow it down?

#### q_boundary
1. ★ The ping to h3 worked — so why is h3's MAC absent from leaf1's table?
2. ★ What exactly did h1 put in the destination-MAC field of those frames, and how did it know it?
3. ★ Where does the switch's L2 job end and something else take over, physically and logically?
4. Why does leaf1 have an ARP table at all — I thought ARP was a host thing?
5. What would happen if h1 had no default gateway configured?
6. Why doesn't h1 just ARP for 10.0.2.10 directly and see who answers?
7. What is in h1's neighbor cache after the ping, and what state is the entry in?
8. If both hosts were in Vlan10 on the same switch, how would this picture change?

#### q_impact
1. ★ What changed after the injection — which ports, MAC entries, or pings?
2. ★ Why did reachability break or survive the way it did?
3. ★ What would restore have to undo to heal this?

## Verify-On-Lab

Consolidated checklist of every `[VERIFY-ON-LAB]` marker in this lesson:

1. **FDB location on vs:** after an h1 ping, confirm learned MAC appears in ASIC_DB (`SAI_OBJECT_TYPE_FDB_ENTRY*`) and whether APPL_DB `FDB_TABLE*` stays empty — then delete the losing claim from t_redis / Observe / Knowledge Card.
2. **LLDP_ENTRY_TABLE key format:** exact key string (separator, port name) for the `hgetall` in o_redis.
3. **Default MAC aging time** on docker-sonic-vs: capture the value from `show mac aging-time`, pin it into the Knowledge Card healthy-state section.
4. **FDB flush on port-down:** does `config interface shutdown Ethernet8` remove h1's entry immediately, or does it linger until aging (c_shut_access effects, after-chaos map).
5. **Link recovery time** after `startup Ethernet8` (seconds until Oper up + first successful ping) — pin timing in Knowledge Card.
6. **First-ping loss after `sonic-clear fdb all`** — 0 or 1 lost probes?
7. **c_vlan_member_del plan-B:** `config switchport mode routed` ordering constraints when the port still has membership.
8. **c_wrong_vlan plan-B:** vs handling of untagged frames arriving on a tagged-only member.
9. **c_gw_ip_remove signatures:** confirm loss is immediate with both warm and cold h1 ARP cache, and record the two error signatures (silent timeouts vs "Destination Host Unreachable").
10. **c_lldp_stop local behavior:** does leaf1's own `show lldp table` empty at container stop or at TTL; **peer-side ageout** timing on leaf2 (~TTL 120 s?).
11. **LLDP reappearance time** after re-enable (~30 s announcement interval?).
12. **c_mac_flap on vs:** does the FDB replace the entry per new MAC or accumulate multiple 02:00:00:00:01:1x rows until aging; per-flap ping loss.
13. **c_static_mac_wrong_port:** whether CONFIG_DB `FDB|Vlan10|<mac>` writes are consumed on vs at all; exact key format; if dead, keep option disabled permanently or re-target to APPL_DB producer path.
14. **Counter freshness on vs:** poll interval, lag between ping burst and visible RX_OK/TX_OK movement; whether BPS/UTIL columns read N/A.
15. **Typical container-to-container rtt** for healthy-state expectations.
16. **Pinned host MACs present:** confirm topology actually sets h1=02:00:00:00:01:10, h3=02:00:00:00:02:10 (chaos restore depends on it).

## Machine Summary

```json
{
  "id": "switches_explained",
  "steps": [
    {"id": "t_ports", "kind": "teach", "core": true},
    {"id": "o_ports", "kind": "observe", "core": true},
    {"id": "q_ports", "kind": "qna", "core": true},
    {"id": "t_lldp", "kind": "teach", "core": false},
    {"id": "o_lldp", "kind": "observe", "core": false},
    {"id": "q_lldp", "kind": "qna", "core": false},
    {"id": "t_vlan", "kind": "teach", "core": true},
    {"id": "o_vlan", "kind": "observe", "core": true},
    {"id": "q_vlan", "kind": "qna", "core": true},
    {"id": "t_mac", "kind": "teach", "core": true},
    {"id": "o_mac", "kind": "observe", "core": true},
    {"id": "q_mac", "kind": "qna", "core": true},
    {"id": "t_age", "kind": "teach", "core": false},
    {"id": "o_age", "kind": "observe", "core": false},
    {"id": "q_age", "kind": "qna", "core": false},
    {"id": "t_redis", "kind": "teach", "core": false},
    {"id": "o_redis", "kind": "observe", "core": false},
    {"id": "q_redis", "kind": "qna", "core": false},
    {"id": "t_counters", "kind": "teach", "core": false},
    {"id": "o_counters", "kind": "observe", "core": false},
    {"id": "q_counters", "kind": "qna", "core": false},
    {"id": "t_boundary", "kind": "teach", "core": false},
    {"id": "o_boundary", "kind": "observe", "core": false},
    {"id": "q_boundary", "kind": "qna", "core": false},
    {"id": "chaos", "kind": "chaos_select", "core": true},
    {"id": "q_impact", "kind": "qna", "core": true},
    {"id": "restore", "kind": "restore", "core": true}
  ],
  "commands": {
    "o_ports": ["leaf1: show interfaces status"],
    "o_lldp": ["leaf1: show lldp table", "leaf1: show lldp neighbors Ethernet0"],
    "o_vlan": ["leaf1: show vlan brief", "leaf1: show vlan config"],
    "o_mac": ["leaf1: sonic-clear fdb all", "leaf1: show mac", "leaf1: show mac -c", "h1: ping -c 3 10.0.1.1", "leaf1: show mac", "leaf1: show mac -c"],
    "o_age": ["leaf1: show mac aging-time", "leaf1: show mac -c"],
    "o_redis": ["h1: ping -c 3 10.0.1.1", "leaf1: sonic-db-cli ASIC_DB keys \"ASIC_STATE:SAI_OBJECT_TYPE_FDB_ENTRY*\"", "leaf1: sonic-db-cli APPL_DB keys \"FDB_TABLE*\"", "leaf1: sonic-db-cli APPL_DB keys \"LLDP_ENTRY_TABLE*\"", "leaf1: sonic-db-cli APPL_DB hgetall \"LLDP_ENTRY_TABLE:Ethernet0\""],
    "o_counters": ["leaf1: sonic-clear counters", "h1: ping -c 10 10.0.1.1", "leaf1: show interfaces counters"],
    "o_boundary": ["h1: ping -c 3 10.0.2.10", "h1: ip neigh show", "leaf1: show mac", "leaf1: show arp"],
    "after_chaos": ["leaf1: show interfaces status", "leaf1: show vlan brief", "leaf1: show mac", "leaf1: show mac -c", "leaf1: show lldp table", "leaf1: show ip interfaces", "h1: ping -c 5 10.0.1.1", "h1: ping -c 5 10.0.2.10", "leaf1: sonic-db-cli ASIC_DB keys \"ASIC_STATE:SAI_OBJECT_TYPE_FDB_ENTRY*\""]
  },
  "chaos_ids": ["c_shut_access", "c_clear_fdb", "c_vlan_member_del", "c_wrong_vlan", "c_gw_ip_remove", "c_lldp_stop", "c_mac_flap", "c_static_mac_wrong_port"],
  "enabled_chaos": ["c_shut_access", "c_clear_fdb", "c_vlan_member_del", "c_wrong_vlan", "c_gw_ip_remove", "c_lldp_stop"],
  "keywords": ["switch", "switching", "layer 2", "l2", "ethernet", "frame", "mac", "mac address", "mac table", "fdb", "forwarding database", "cam table", "learn", "learning", "flood", "flooding", "unknown unicast", "broadcast", "broadcast domain", "vlan", "access port", "untagged", "tagged", "trunk", "membership", "lldp", "neighbor", "discovery", "ttl", "link", "port", "interface", "admin", "oper", "operational", "link up", "link down", "shutdown", "startup", "aging", "age out", "expire", "arp", "arp table", "ip neighbor", "gateway", "default gateway", "svi", "vlan interface", "counters", "rx", "tx", "drops", "errors", "redis", "appl_db", "asic_db", "config_db", "fdb_table", "lldp_entry_table", "sonic-db-cli", "ping", "icmp", "loss", "ethernet0", "ethernet4", "ethernet8", "vlan10", "h1", "leaf1"],
  "glossary_terms": ["switch", "frame", "packet", "port", "link", "admin status", "operational status (Oper)", "MTU", "MAC address", "MAC table / FDB", "learning", "flooding", "unknown unicast", "aging / aging time", "static entry", "VLAN", "access port", "tagged / untagged", "broadcast domain", "broadcast", "ingress / egress", "LLDP", "neighbor (LLDP)", "TTL (LLDP)", "ARP", "default gateway", "subnet", "VLAN interface (SVI)", "router", "counter", "RX / TX", "drop", "Redis", "APPL_DB", "ASIC_DB", "CONFIG_DB", "COUNTERS_DB", "SAI", "sonic-db-cli", "ping / ICMP"]
}
```
