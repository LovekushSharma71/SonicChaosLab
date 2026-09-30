### SECTION: t_containers — S1: SONiC is a fleet of containers, not a monolith

**Essence.** A traditional switch OS is one giant proprietary program. SONiC is the opposite: a plain Linux machine running a handful of **Docker containers**, each owning one job, all coordinating through a shared database. Understanding SONiC means learning who the containers are and how they hand work to each other — this whole lesson is that map.

**Mechanism.** Think microservices, but for a switch. The important members of the fleet:

- **database** — runs Redis, the shared memory every other container reads and writes. Nothing else stores state; they all meet here.
- **swss** (Switch State Service) — the brain of the control-to-hardware translation. It hosts **orchagent** (the orchestrator) plus a set of small managers (`*mgrd`) and syncers (`*syncd`) that move state between databases.
- **syncd** — the hands: it takes desired hardware state and pushes it into the ASIC through **SAI** (a vendor-neutral chip API). On this lab the "ASIC" is software, but syncd behaves identically.
- **bgp** — the routing brain: the FRR suite (bgpd, zebra, fpmsyncd). Lesson 3's whole subject.
- **lldp**, **teamd**, **pmon**, **snmp**, **telemetry** — discovery, link-aggregation, platform monitoring, and management surfaces.

The payoff of this design is exactly what your chaos options will exploit: because subsystems are isolated containers, you can kill one and watch precisely what depends on it — something impossible in a monolith.

**On SONiC.** Two views. `show feature status` is the SONiC-level roster: each feature, whether it's enabled, and its auto-restart policy. `docker ps` is the ground truth from Linux: the actual running containers with uptimes. Expect them to agree — every enabled feature has a matching running container. Note database, swss, and syncd especially: they are the spine of the forwarding pipeline you are about to trace.

**Boundaries.** Naming the containers is not the same as knowing the message flow between them — that is the next two scenarios. And "enabled" (feature intent) versus "running" (container reality) is the same intent-vs-reality split you met as admin-vs-operational in Lesson 1, now one level up.

### SECTION: t_configdb — S2: CONFIG_DB — the book of intent

**Essence.** Every configuration you set — a port's speed, a VLAN's members, a BGP neighbor — lands in one place: **CONFIG_DB**, Redis database number 4. It is pure *intent*: what you want to be true. Nothing in CONFIG_DB touches hardware directly; it is the top of a chain that other containers watch and react to.

**Mechanism.** CONFIG_DB is a set of tables, each a family of keys. The shape is `TABLE|key` with fields underneath. A few you will meet:

- `PORT|Ethernet8` — fields like `admin_status`, `mtu`, `speed`.
- `VLAN|Vlan10` and `VLAN_MEMBER|Vlan10|Ethernet8` — the VLAN and its membership.
- `BGP_NEIGHBOR|10.0.12.1` — a peer definition (Lesson 3).
- `DEVICE_METADATA|localhost` — the switch's own identity (hostname, MAC, BGP ASN).

Crucially, CONFIG_DB is *declarative and inert*. Writing a key here is like committing a desired-state file: it only matters because a manager process is subscribed and will act on the change. That subscription is the pipeline, next.

**On SONiC.** `sonic-db-cli CONFIG_DB keys "PORT|*"` lists every port's intent key; `hgetall "PORT|Ethernet8"` dumps one port's desired fields. `sonic-db-cli CONFIG_DB keys "VLAN*"` shows the VLAN and VLAN_MEMBER families; `keys "BGP_NEIGHBOR*"` shows the peers you'll dissect in Lesson 3. Everything you configured with a `config …` command in Lesson 1 is sitting right here as plain key-value rows — the CLI was only a friendly writer on top of this database.

**Boundaries.** CONFIG_DB says nothing about whether intent was *achieved* — a port can be `admin_status up` here while physically down. Achieved reality lives in STATE_DB (scenario S4). And configuring by writing CONFIG_DB raw bypasses the validating CLI — safe to read, risky to write, and one chaos option probes exactly that.

### SECTION: t_pipeline — S3: One change, four databases — the full pipeline

**Essence.** This is the heart of SONiC. A single configuration change does not jump to the chip; it *falls* through a chain of databases, each hop performed by a subscribed process that translates the state one step closer to hardware. Learn this staircase and every SONiC behavior — including every failure in this course — becomes readable.

**Mechanism.** The staircase, top to bottom:

1. **CONFIG_DB** (intent) — you write `VLAN|Vlan30`.
2. A **manager** in swss (here `vlanmgrd`) is subscribed to that table. It validates the intent and writes a translated form into…
3. **APPL_DB** (application database, number 0) — the "make it so" instruction for the forwarding layer, e.g. a `VLAN_TABLE` entry.
4. **orchagent** (in swss) consumes APPL_DB, works out the concrete hardware objects required, and writes them to…
5. **ASIC_DB** (number 1) — desired chip objects in **SAI** vocabulary.
6. **syncd** consumes ASIC_DB and calls **SAI** to program the **(virtual) ASIC**.

Two properties define the whole system. It is **producer/consumer over Redis**: each stage publishes, the next stage is subscribed — nobody calls anybody directly. And it is **eventually consistent**: a change is not atomic across all databases; there is a real, measurable **propagation lag** between the top and the bottom. Usually milliseconds, but under load it grows — and one chaos option makes that lag visible by shoving fifty changes through at once.

**On SONiC.** The observe step performs the classic demonstration on a throwaway object so nothing real breaks: it confirms `Vlan30` does not exist, creates it with `config vlan add 30`, then watches it *appear in each database in order* — CONFIG_DB first, then a VLAN entry in APPL_DB, then a new SAI VLAN object in ASIC_DB — and finally deletes it. One wrinkle at the bottom: ASIC_DB names its objects with opaque machine ids, so the new VLAN shows up there as the SAI VLAN object **count rising by one** (the step reads the count before and after), not as the string "Vlan30". You are watching intent fall into hardware, one Redis hop at a time. (The step is self-cleaning: Vlan30 is removed even if something errors.)

**Boundaries.** We trace a VLAN because it is simple and safe; a *route* takes a longer path with an extra actor (fpmsyncd), which Lesson 3 walks. The exact manager names and key formats are what the observe step is for — confirm them live rather than trusting a diagram.

### SECTION: t_statedb — S4: STATE_DB — what actually happened

**Essence.** CONFIG_DB is what you asked for; **STATE_DB** (number 6) is what the system observes to be *true*. It is the reality-readback database — the place a manager records "this actually came up," "this link is really down," "this transceiver is present." It is how SONiC closes the loop between intent and outcome.

**Mechanism.** Where CONFIG_DB flows downward (intent → hardware), STATE_DB is written *upward* by syncers watching the real system. `portsyncd`/`portmgrd`, for example, learn the Linux/ASIC operational state of a port and record it in `STATE_DB PORT_TABLE|Ethernet8` with fields like `oper_status`. The `show interfaces status` "Oper" column you read in Lesson 1 is, underneath, a STATE_DB read. Because STATE_DB reflects reality, it can *disagree* with CONFIG_DB — and that disagreement is the single most useful diagnostic in the system: intent up, state down means "you asked, the world refused."

**On SONiC.** The observe step reads `STATE_DB PORT_TABLE|Ethernet8` and lays it beside `show interfaces status`. Same truth, two windows — one raw, one formatted. Seeing them agree teaches you where the "Oper" column actually comes from; later, when a chaos option makes intent and reality diverge, you will know exactly which database to trust.

**Boundaries.** STATE_DB records outcomes, not desires — you never *configure* here. And it is not a full event log; it is current-truth, overwritten as reality changes.

### SECTION: t_asicdb — S5: ASIC_DB and SAI — the machine language of switching

**Essence.** At the bottom of the staircase, intent has been compiled all the way down to **SAI objects** in **ASIC_DB** (number 1) — the vendor-neutral instruction set the chip understands. This is as close to hardware as software gets, and on this lab the "chip" is a software ASIC that obeys the very same objects a real Broadcom or Mellanox part would.

**Mechanism.** **SAI** (Switch Abstraction Interface) is a standardized C API and object model: ports, VLANs, routes, next-hops, and FDB entries all become typed objects like `SAI_OBJECT_TYPE_PORT`, `SAI_OBJECT_TYPE_VLAN`, `SAI_OBJECT_TYPE_FDB_ENTRY`. ASIC_DB stores the desired set of these objects; syncd makes the real SAI calls. Because SAI objects have long machine-generated identifiers, SONiC keeps a translation between the **VID** (virtual object id used inside the databases) and the **RID** (the real id the chip returned) — a map you can read directly. This indirection is what lets SONiC speak one vocabulary to chips from many vendors.

**On SONiC.** The observe step lists `SAI_OBJECT_TYPE_PORT` and `SAI_OBJECT_TYPE_VLAN` keys in ASIC_DB and peeks at the VID-to-RID mapping. You will recognize the FDB object type from Lesson 1 — the learned MAC you hunted lived exactly here. Now you can see its neighbors: the ports and VLANs, all as chip objects.

**Boundaries.** SAI internals (attribute semantics, per-vendor quirks) are a deep specialty; here we only need that ASIC_DB is the compiled bottom of the pipeline and syncd is its executor. Reading is safe; this database is never edited by hand.

### SECTION: t_counters — S6: COUNTERS_DB — how counters are farmed

**Essence.** The RX/TX numbers you read in Lesson 1 are not fetched from the chip on demand — they are **harvested on a schedule** into their own database, **COUNTERS_DB** (number 2), by a dedicated polling mechanism. This scenario shows the machinery behind the diary.

**Mechanism.** SONiC runs a **flex counter** poller that periodically reads statistics from the ASIC and stores them in COUNTERS_DB. But chip statistics are keyed by opaque object ids, not friendly names, so SONiC also maintains a **name map** — `COUNTERS_PORT_NAME_MAP` translates `Ethernet8` to the object id whose counters you actually want. So `show interfaces counters` really does three things: read the name map, resolve your port to an id, and read that id's counter row — all from COUNTERS_DB, all reflecting the *last poll*, not this instant. That polling interval is exactly why fresh traffic takes a moment to appear.

**On SONiC.** The observe step reads `COUNTERS_PORT_NAME_MAP` (see the port→id translation), dumps one counter row, and shows `show interfaces counters` for comparison. You are looking at the plumbing under a command you already trust.

**Boundaries.** Poll intervals and which statistics are collected are configurable and platform-dependent; on the virtual switch treat freshness as best-effort. Queue/PFC/watermark counters exist but stay out of scope.

### SECTION: t_admin_path — S7: The journey of one shutdown across the databases

**Essence.** Now put the staircase in motion with a real, reversible change and *watch every step*. Shutting a port is the cleanest possible trace: one field, `admin_status`, travels from intent (CONFIG_DB) to instruction (APPL_DB) to reality (STATE_DB), and you can read it at each stop.

**Mechanism.** `config interface shutdown Ethernet4` writes `admin_status=down` into `CONFIG_DB PORT|Ethernet4`. `portmgrd`/`portsyncd` (in swss) react: the intent is translated into `APPL_DB PORT_TABLE:Ethernet4`, orchagent programs the port object down through ASIC_DB/syncd, the (virtual) link drops, and the readback lands in `STATE_DB PORT_TABLE|Ethernet4` as `oper_status=down`. One command, the whole pipeline, legible at every layer. We pick **Ethernet4** deliberately: it is one of the two parallel inter-switch links, so the other link keeps h1↔h3 traffic flowing (Lesson 3's ECMP) — this trace is observation-only, with no user outage.

**On SONiC.** The observe step reads `admin_status` in CONFIG_DB, runs the shutdown, then reads `admin_status` again in CONFIG_DB, in APPL_DB `PORT_TABLE`, and `oper_status` in STATE_DB — before restoring with `startup`. You watch a single value ripple down the staircase in the correct order. (Self-cleaning: Ethernet4 is brought back up even on error.)

**Boundaries.** This traces the *port* pipeline specifically; other object types have their own managers but the same producer/consumer shape. The propagation is fast here — the *reason* it can be slow is load, which the churn chaos option demonstrates separately.

### SECTION: t_procs — S8: Inside a container — supervised processes

**Essence.** Each container is not one program but a little supervised system of its own. A process manager starts and watches several daemons; if one dies, policy decides whether to restart it or take the whole container down. Zoom in and the "brain" containers resolve into named processes you can list.

**Mechanism.** Inside a SONiC container, **supervisord** launches and monitors the daemons. In **swss** you'll find orchagent alongside managers/syncers like portmgrd, vlanmgrd, intfmgrd, neighsyncd. In **bgp** you'll find bgpd and zebra (FRR) plus fpmsyncd (the route→APPL_DB bridge you'll meet in Lesson 3). In **syncd**, the syncd process itself. This is why SONiC's failure modes are granular: a single daemon can crash and be restarted without rebooting the box — and, conversely, killing the right daemon disables exactly one capability, which several chaos options do on purpose.

**On SONiC.** The observe step runs `docker exec swss supervisorctl status` and `docker exec bgp supervisorctl status` to list each container's supervised processes and their run state. The names you see (orchagent, bgpd, zebra, fpmsyncd) are the exact actors named in the pipeline scenarios — now visible as live processes.

**Boundaries.** Auto-restart policy and the full process inventory vary by feature; we only need the mental model of "container = supervised bundle of daemons." Debugging individual daemon internals is beyond this lesson.
