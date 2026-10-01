### SECTION: t_ports — S1: A switch is a decision machine — ports, links, admin vs operational

**Essence.** A switch is a box with one job: a message arrives on one port, and the box decides which port (or ports) it leaves on. Every scenario in this lesson examines one piece of that decision. Before any deciding can happen, the doors themselves must work — so we start with ports.

**Mechanism.** At this layer the unit of traffic is a **frame**: an envelope holding a destination address, a source address, and payload. Frames enter and exit through **ports** — physical or virtual sockets. A working connection between two ports is a **link**, and a link has two independent kinds of "up":

- **Admin status** — what you *asked for*. It is configuration: "this port should be enabled." Think of it as the desired state in a spec.
- **Operational status** — what *actually happened*. The port only becomes operationally up if the admin says up AND the other end cooperates (peer powered, connected, speaking). Command output abbreviates this column to "Oper."

Admin down forces the operational state down. Admin up guarantees nothing — it is a request, not a result. Whenever a network "doesn't work," the first split-second question is always: which of these two is down, on which end? That distinction — intent vs. reality — will follow you through this entire course.

**On SONiC.** The command you are about to run, `show interfaces status`, prints one row per port. Read four columns: the port name (Ethernet0, Ethernet4, Ethernet8 …), Oper, Admin, and MTU. In this lab, leaf1's Ethernet0 and Ethernet4 run to the other switch (leaf2), and Ethernet8 runs to the host h1. Expect Admin up on all four lesson ports — and here comes this lab's first broken window: **the Oper column reads "down" on this virtual switch even while traffic flows** (the virtual chip never reports link state back to the CLI's database). The step's second command, `ip -br link show`, asks the Linux kernel directly — its `LOWER_UP` flag is the real carrier truth, and you'll see it disagree with the CLI, port by port. Note the MTU column reading 9100 on every port — ignore it today, but remember it exists: an entire later lesson is about what happens when that number lies to you.

**Boundaries.** Nothing here explains *how* the switch decides where frames go — that is the MAC table, three scenarios from now. And "up" does not mean "working": you have just seen the reverse too — a status column can look broken while traffic is perfect. Trust, but verify against a second source; this course always shows you where the second source lives.

### SECTION: t_lldp — S2: How switches introduce themselves — LLDP

**Essence.** Devices on a link exchange standardized business cards so each side knows who is physically attached. The protocol is **LLDP** (Link Layer Discovery Protocol, IEEE 802.1AB). It moves no user traffic — it exists purely so that humans and tools can ask the switch "what is plugged into you?" and get a live, truthful answer instead of trusting a wiring diagram.

**Mechanism.** Every LLDP-speaking device periodically (typically every 30 seconds) sends a small frame out of each port containing: its own name, which of *its* ports this is, what kind of device it is, and a **TTL** (time-to-live — "you may trust this card for N seconds," typically 120). The receiver files the card in a **neighbor** table keyed by local port. Two properties matter:

1. Cards are *link-local*: a switch never forwards them onward. Your neighbor table only ever shows direct physical neighbors.
2. The table is *lease-based*: if cards stop arriving, the entry survives until its TTL runs out, then quietly disappears. LLDP therefore notices silence slowly — worth remembering when we later kill it on purpose.

**On SONiC.** LLDP runs in its own container (a theme you will meet properly in Lesson 2: SONiC is a fleet of containers). A helper process copies every received card into a live database table called LLDP_ENTRY_TABLE — your first hint that everything the CLI shows you is actually rows in a database. `show lldp table` prints the summary: LocalPort (where the card arrived), RemoteDevice (the neighbor's name), RemotePortID (which of the neighbor's ports faces you). On hardware you would see leaf2 twice — once via Ethernet0, once via Ethernet4 (two parallel cables) — and h1 absent (plain Linux hosts don't speak LLDP unless a daemon is installed). **On this lab image the LLDP daemon isn't shipped at all** (verified: no lldpd/lldpcli binaries), so the observe step shows the command erroring on an empty card file — run it anyway: recognizing "the service behind this table doesn't exist here" is itself a diagnostic skill, and the table shape above is what you'll read on real gear.

**Boundaries.** LLDP is eyes, not hands: disabling it changes nothing about how traffic flows. (The chaos option that would prove this is parked on this image — there is no LLDP service to kill.)

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

**On SONiC.** `show mac` prints the live table: Vlan, MacAddress, Port, and Type (Dynamic = learned; Static = pinned by an operator — rare) plus a total count at the bottom; `show mac -c` prints just the count. On this virtual lab the readable truth is the kernel bridge's table — `bridge fdb show br Bridge` — because the virtual ASIC never reports learn events back into the database `show mac` renders (a preview of Lesson 2's pipeline). The observe step choreographs the learning live from a guaranteed blank slate: it wipes the learned table (a harmless reset — the table is a cache, and you will study exactly this action as chaos option B), shows it empty, has h1 ping its gateway (10.0.1.1 — the switch's own Vlan10 address), then looks again. Expect a new learned entry: h1's MAC (pinned in this lab to 02:00:00:00:01:10) in vlan 10 on Ethernet8 — and note the final `show mac` reading empty while the bridge holds the truth. You will have watched a switch learn.

**Boundaries.** The table is a cache, not a config — and caches expire. That's the next scenario. And one subtlety for later: a *wrong* entry is far worse than a missing one (missing → flood → self-heals; wrong → silent blackhole). Chaos option 8 weaponizes exactly this.

### SECTION: t_age — S5: Forgetting on purpose — aging and the cold-start table

**Essence.** MAC table entries are leases, not facts. Every dynamic entry expires after a period of silence, called the **aging time**. Forgetting is a feature: it is what lets hosts move.

**Mechanism.** Each dynamic entry carries a countdown that resets every time a frame from that MAC arrives. If the host goes quiet long enough, the countdown ends and the entry is deleted. The next frame *to* that host is then an **unknown unicast** — a frame addressed to a single MAC the table doesn't know — and it gets flooded, exactly like scenario S4 described, until the host's reply re-teaches the switch. Why forget at all? Because hosts move: unplug h1 from Ethernet8, plug it in elsewhere, and a permanent entry pointing at Ethernet8 would blackhole its traffic forever. Aging bounds how long any stale claim survives. The cost is negligible: relearning takes one round trip.

**On SONiC.** `show mac aging-time` names the SONiC-level aging surface (unset on this lab — it reports "not configured"); the kernel's live value sits in `ip -d link show Bridge` as `ageing_time` in centiseconds (30000 = 300 s). Two things to notice in the observe step: the aging value itself (note it — it explains how long chaos aftermath lingers), and how small the table is in a 6-device lab (`bridge fdb show br Bridge`). Chaos option B (the FDB flush) is simply "aging, everywhere, right now" — the observe/diff machinery will let you time how fast the table refills.

**Boundaries.** Static entries never age — they are operator promises, not observations. And do not confuse MAC aging with ARP timeouts: different table, different layer, different clock (ARP appears in S8).

### SECTION: t_redis — S6: Where the tables really live — first peek at Redis

**Essence.** On SONiC, every table you have looked at so far — interfaces, VLANs, MACs, LLDP neighbors — is not hidden inside a proprietary blob. Each is literally rows in **Redis**, an in-memory key-value database running on the switch. The `show` commands are pretty-printers. This is SONiC's superpower: if you can read Redis, you can read the switch's mind — and in this course you always will, right next to the CLI view.

**Mechanism.** SONiC runs several logical Redis databases, each with a number and a job. Two matter today:

- **APPL_DB** (database 0) — tables that SONiC services *produce* as their working state for the forwarding layer. On hardware the LLDP card file lives here: keys shaped like `LLDP_ENTRY_TABLE:Ethernet0`, one per port with a neighbor. (This image ships no LLDP stack, so the step's LLDP hunt reads empty — you're probing where the rows *would* be.)
- **ASIC_DB** (database 1) — the exact objects the switching chip (here: a *virtual* chip, since this is a software switch) has been told to hold, in a vendor-neutral vocabulary called SAI. On real hardware, learned MACs surface here as `…SAI_OBJECT_TYPE_FDB_ENTRY…` keys — the chip reports what it learned, and this database is the record.

There is a third name to file away: **CONFIG_DB** (database 4) — your *intent*, everything you configure. Lesson 2 is entirely about how intent flows from CONFIG_DB through the other databases down to the chip. Today we only peek at the two "live state" databases.

**On SONiC.** `sonic-db-cli <DBNAME> keys "<pattern>"` lists matching keys; `sonic-db-cli <DBNAME> hgetall "<key>"` dumps one row's fields. The observe step first has h1 ping the gateway (so a fresh MAC entry certainly exists), then hunts for FDB entries in ASIC_DB *and* in APPL_DB's FDB_TABLE, then probes where LLDP rows would live. Verified on this lab: **every hunt comes back empty** — the virtual chip's learn events never reach the Redis pipeline, and the LLDP service doesn't exist on this image — while the kernel bridge (`bridge fdb show br Bridge`, the step's final command) proves the MAC *was* learned. That contrast is this lab's live proof that each database only shows what some service *wrote into it*, not the wire: no writer, no rows, even when the forwarding is fine.

**Boundaries.** Reading Redis is always safe. *Writing* raw keys bypasses every validation layer SONiC has — that is a chaos experiment (option 8 does it deliberately), never a habit.

### SECTION: t_counters — S7: Counters — the switch's diary

**Essence.** Status tells you whether a port *could* work; **counters** tell you whether it actually *did*. Every port keeps running totals of frames in and out, plus buckets for the ones it had to throw away. When a network misbehaves subtly, counters are usually where the truth leaks out.

**Mechanism.** Counters are monotonic: they only ever increase, so you never read one — you read it twice and subtract. The vocabulary: **RX** = received (into the switch), **TX** = transmitted (out of the switch), and per-direction buckets for OK frames, errors (damaged frames), **drops** (healthy frames the switch discarded — no room, no rule, no VLAN…), and overruns. A healthy quiet port shows OK counters creeping and error/drop buckets frozen. A drop bucket that moves while users complain is a smoking gun.

**On SONiC.** Counters are polled from the chip into a dedicated database (COUNTERS_DB — Lesson 2 territory) every few seconds. The observe step uses a clean experimental pattern you should steal for real life: `sonic-clear counters` sets a personal zero-point (it does not touch the switch's true totals — it just makes *your* next reading start from zero), then h1 generates a known burst of pings, then `show interfaces counters`. On hardware you would watch RX_OK and TX_OK move on Ethernet8 while RX_DRP / RX_ERR stay flat. **On this lab image every cell reads N/A** (verified: the virtual chip implements no counters and the poller has nothing to poll) — another broken window: the *workflow* is the lesson here; the numbers arrive when the chip is real.

**Boundaries.** On this virtual switch, counter freshness is best-effort — trends are trustworthy, exact per-packet accounting is not. Queue, PFC, and watermark counters exist on real systems and are out of scope here.

### SECTION: t_boundary — S8: The edge of the L2 world — why h1 never "switches" to h3

**Essence.** h1 (10.0.1.10, Vlan10 on leaf1) can reach h3 (10.0.2.10, Vlan20 on leaf2) — you will prove it with a ping — yet no switch ever "switches" a frame to h3. The frame's journey ends at the VLAN border, and something categorically different (routing) carries the payload onward. This scenario makes you *see* the border in the tables, and it is the doorway into Lesson 3.

**Mechanism.** Two ideas, both small:

- **ARP** (Address Resolution Protocol): IP-speaking hosts need a MAC to put on the frame. ARP is the shouted question — "who has IP X? tell me your MAC" — sent as a broadcast, answered by the owner. Every host keeps a little cache of answers.
- **Default gateway:** before ARPing, a host compares the destination IP with its own subnet. Same subnet → ARP for the destination directly. *Different* subnet → don't even try; instead hand the packet to a designated local router, the default gateway, by ARPing for the *gateway's* IP and addressing the frame to the *gateway's* MAC.

10.0.2.10 is outside h1's 10.0.1.0/24, so h1 wraps the packet for h3 inside a frame addressed to 10.0.1.1's MAC — leaf1's own Vlan10 interface. At Layer 2, h1 only ever converses with its gateway. h3's MAC never crosses into Vlan10, never gets learned, never appears.

**On SONiC.** The observe step pings h3 from h1 (it succeeds — the two switches route it; how they know the way is Lesson 3's flagship topic), then collects the evidence: the FDB (`bridge fdb show br Bridge`) on leaf1 — h1's MAC is there, h3's is nowhere; `show arp` on leaf1 — the switch resolved 10.0.1.10 on Vlan10 (the gateway keeps its own ARP cache, because routing made it a *sender* of frames toward h1); and h1's own neighbor cache (`ip neigh show`) — containing the gateway, not h3. Working ping + absent MAC = the packet was routed, not switched. That is the boundary.

**Boundaries.** How leaf1 knows that 10.0.2.0/24 lives behind leaf2 — the routing table, BGP, reconvergence — is deliberately left dark until Lesson 3. Today you only need to know the L2 world has an edge, and you've now stood on it.
