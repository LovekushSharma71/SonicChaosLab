# Lesson: inside_sonic — Inside SONiC: from CLI to ASIC

## Meta

- **id:** `inside_sonic`
- **difficulty:** core
- **requires:** `switches_explained` (assumes: ports/admin-vs-operational, VLANs, FDB, and the one-line idea that SONiC state lives in Redis)
- **target image:** docker-sonic-vs, branch **202405** (Containerlab: leaf1/leaf2 SONiC, h1–h4 Linux hosts)
- **devices used in this lesson:** `leaf1` primarily; `h1` for the one end-to-end reachability probe
- **lab prerequisites assumed by this lesson (bake into topology):**
  - same baseline as Lesson 1, plus the two inter-switch links up and BGP established (leaf1 neighbors 10.0.12.1 and 10.0.12.3) so CONFIG_DB has a `BGP_NEIGHBOR` table to point at
  - nested docker is ABSENT on docker-sonic-vs — container-level views map to `supervisorctl status` (one supervisord runs all process groups); `docker ps` / `docker exec` work only on real SONiC hardware/VMs
- **command execution targets:** `leaf1:` = docker exec into SONiC vs (root; `sudo` retained for doc fidelity), `h1:` = docker exec into host container
- **source URLs relied on:**
  - SONiC Architecture wiki (containers swss/syncd/bgp/lldp/database, the CONFIG_DB→APPL_DB→ASIC_DB pipeline, orchagent, syncd, SAI, STATE_DB, COUNTERS_DB, flex counters): <https://github.com/sonic-net/SONiC/wiki/Architecture>
  - SONiC CLI Reference (202405) — Feature (`show feature status`, `config feature state`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#feature>
  - SONiC CLI Reference (202405) — System State (`show services`, `show processes`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#system-state>
  - SONiC CLI Reference (202405) — VLAN & FDB (`config vlan add -m`, `config vlan del`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#vlan--fdb>
  - SONiC CLI Reference (202405) — Interfaces (`config interface shutdown/startup`, `show interfaces status`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#interfaces>
  - SONiC CLI Reference (202405) — Loading/Reloading Configuration (`config reload`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#loading-reloading-and-saving-configuration>
  - `sonic-db-cli`, `supervisorctl`, `systemctl`, `docker` — standard tooling shipped in the image (not in the Command Reference)

## Coverage Notes

- **TEACHES:** SONiC as a fleet of Docker containers and their roles (database/swss/syncd/bgp/lldp/…); CONFIG_DB as operator intent; the full config pipeline CONFIG_DB → \*mgrd → APPL_DB → orchagent → ASIC_DB → syncd → SAI → virtual ASIC; propagation and eventual consistency (the lag between databases); STATE_DB as reality readback; ASIC_DB/SAI object model; COUNTERS_DB and flex-counter polling; supervised processes inside a container.
- **REINFORCES:** admin-vs-operational (now traced through the databases); FDB/VLAN as CONFIG_DB objects; the Redis peek from Lesson 1 (now the whole machine).
- **ASSUMES:** Lesson 1 concepts; software literacy (producer/consumer, message queue, cache coherence, daemon, key-value store).
- **PREVIEWS (named, not taught):** BGP/FRR internals and the route-specific pipeline (Lesson 3 re-walks this exact machine for one route); MTU as a CONFIG_DB field (Lesson 4).
- **DELIBERATELY OUT OF SCOPE:** writing raw Redis as normal practice, warm/fast reboot internals, kubernetes/feature-owner mechanics, telemetry/gNMI, platform/PMON hardware paths.

## Step Plan

| # | step id | kind | title | core-or-optional |
|---|---------|------|-------|------------------|
| 1 | t_containers | teach | S1: SONiC is a fleet of containers, not a monolith | core |
| 2 | o_containers | observe | S1: The fleet, live | core |
| 3 | q_containers | qna | S1: Questions — containers & roles | core |
| 4 | t_configdb | teach | S2: CONFIG_DB — the book of intent | core |
| 5 | o_configdb | observe | S2: Read your own intent | core |
| 6 | q_configdb | qna | S2: Questions — CONFIG_DB | core |
| 7 | t_pipeline | teach | S3: One change, four databases — the full pipeline | core |
| 8 | o_pipeline | observe | S3: Create Vlan30, watch it fall through the databases, delete it | core |
| 9 | q_pipeline | qna | S3: Questions — pipeline & propagation | core |
| 10 | t_statedb | teach | S4: STATE_DB — what actually happened | optional |
| 11 | o_statedb | observe | S4: Configured vs actual port state | optional |
| 12 | q_statedb | qna | S4: Questions — STATE_DB | optional |
| 13 | t_asicdb | teach | S5: ASIC_DB and SAI — the machine language of switching | optional |
| 14 | o_asicdb | observe | S5: SAI objects and the VID-to-RID map | optional |
| 15 | q_asicdb | qna | S5: Questions — ASIC_DB & SAI | optional |
| 16 | t_counters | teach | S6: COUNTERS_DB — how counters are farmed | optional |
| 17 | o_counters | observe | S6: The port-name map and a live counter row | optional |
| 18 | q_counters | qna | S6: Questions — COUNTERS_DB | optional |
| 19 | t_admin_path | teach | S7: The journey of one shutdown across the databases | optional |
| 20 | o_admin_path | observe | S7: Trace admin_status through CONFIG_DB, APPL_DB, STATE_DB | optional |
| 21 | q_admin_path | qna | S7: Questions — admin status propagation | optional |
| 22 | t_procs | teach | S8: Inside a container — supervised processes | optional |
| 23 | o_procs | observe | S8: orchagent, syncd, bgpd as ordinary processes | optional |
| 24 | q_procs | qna | S8: Questions — processes | optional |
| 25 | q_baseline | qna | Questions — interrogate the healthy baseline | core |
| 26 | chaos | chaos_select | Pick one failure to inject (8 options) | core |
| 27 | q_impact | qna | Questions — interrogate the failure you injected | core |
| 28 | restore | restore | Heal the lab, verify baseline | core |

## Teach Sections

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

## Commands

Convention: `<target>: <command>` — `leaf1` (SONiC vs via docker exec) and `h1` (host container). Mutating observe steps carry cleanup commands that run even on error.

### Per-scenario observe commands

#### o_containers
- `leaf1: supervisorctl status`
- `leaf1: show feature status`

#### o_configdb
- `leaf1: sonic-db-cli CONFIG_DB keys "PORT|*"`
- `leaf1: sonic-db-cli CONFIG_DB hgetall "PORT|Ethernet8"`
- `leaf1: sonic-db-cli CONFIG_DB keys "VLAN*"`
- `leaf1: sonic-db-cli CONFIG_DB keys "BGP_NEIGHBOR*"`

#### o_pipeline  *(mutating: creates and deletes Vlan30; cleanup = `sudo config vlan del 30`)*
- `leaf1: sonic-db-cli CONFIG_DB keys "VLAN|Vlan30"`
- `leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_VLAN*"`
- `leaf1: sudo config vlan add 30`
- `leaf1: sonic-db-cli CONFIG_DB keys "VLAN|Vlan30"`
- `leaf1: sonic-db-cli APPL_DB keys "VLAN_TABLE:Vlan30"`
- `leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_VLAN*"`
- `leaf1: sudo config vlan del 30`

#### o_statedb
- `leaf1: sonic-db-cli STATE_DB keys "PORT_TABLE|Ethernet8"`
- `leaf1: sonic-db-cli STATE_DB hgetall "PORT_TABLE|Ethernet8"`
- `leaf1: show interfaces status`

#### o_asicdb
- `leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_PORT*"`
- `leaf1: sonic-db-cli ASIC_DB keys "ASIC_STATE:SAI_OBJECT_TYPE_VLAN*"`
- `leaf1: sonic-db-cli ASIC_DB hgetall "VIDTORID"`

#### o_counters
- `leaf1: sonic-db-cli COUNTERS_DB keys "COUNTERS_PORT_NAME_MAP"`
- `leaf1: sonic-db-cli COUNTERS_DB hgetall "COUNTERS_PORT_NAME_MAP"`
- `leaf1: show interfaces counters`

#### o_admin_path  *(mutating: shut+startup Ethernet4; cleanup = `sudo config interface startup Ethernet4`)*
- `leaf1: sonic-db-cli CONFIG_DB hget "PORT|Ethernet4" admin_status`
- `leaf1: sudo config interface shutdown Ethernet4`
- `leaf1: sonic-db-cli CONFIG_DB hget "PORT|Ethernet4" admin_status`
- `leaf1: sonic-db-cli APPL_DB hget "PORT_TABLE:Ethernet4" admin_status`
- `leaf1: sonic-db-cli STATE_DB hget "PORT_TABLE|Ethernet4" oper_status`
- `leaf1: sudo config interface startup Ethernet4`

#### o_procs
- `leaf1: supervisorctl status orchagent portmgrd vlanmgrd neighsyncd`
- `leaf1: supervisorctl status bgpd zebra fpmsyncd staticd`

### After-chaos commands

Run after any injection (the engine diffs these against baseline facts):

- `leaf1: supervisorctl status`
- `leaf1: show feature status`
- `leaf1: show interfaces status`
- `leaf1: show vlan brief`
- `leaf1: sonic-db-cli CONFIG_DB dbsize`
- `leaf1: sonic-db-cli APPL_DB dbsize`
- `leaf1: sonic-db-cli ASIC_DB dbsize`
- `h1: ping -c 5 10.0.1.1`
- `h1: ping -c 5 10.0.2.10`

### Vocabulary

```
show feature status*
show feature config*
show services*
show processes*
show interfaces status*
show interfaces counters*
show vlan*
show ip route*
show runningconfiguration*
sonic-db-cli CONFIG_DB *
sonic-db-cli APPL_DB *
sonic-db-cli ASIC_DB *
sonic-db-cli STATE_DB *
sonic-db-cli COUNTERS_DB *
redis-cli *
supervisorctl status*
supervisorctl *
config vlan*
config interface shutdown*
config interface startup*
ping*
```

## Chaos Options

All options are restorable to baseline. "Injection-failed tell" = how to detect the injection itself did not take on sonic-vs.

---

**1. id: `c_stop_swss` — "Decapitate the pipeline: stop the swss container" (ANCHOR A)**
- **type:** service · **risk:** high · **enabled:** true
- **inject:**
  - `leaf1: supervisorctl stop orchagent`
- **restore:**
  - `leaf1: supervisorctl start orchagent`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** orchagent and the managers vanish (`docker ps` loses swss; `show feature status` may still read enabled — intent vs reality again). The pipeline's middle is gone, so *new* configuration no longer reaches hardware. Existing forwarding often keeps working for a while because syncd and the ASIC still hold their programmed state — a striking lesson that the control plane and the already-programmed data plane are separable `[VERIFY-ON-LAB: whether h1↔h3 keeps flowing with swss down, and for how long]`. Recovery on `start` replays state from CONFIG_DB/APPL_DB; expect a rebuild period before everything is green `[VERIFY-ON-LAB: recovery time; whether syncd needs co-restart]`.
- **plan-B variant:** `leaf1: sudo config feature state swss disabled` / `enabled` (feature surface instead of systemd).
- **injection-failed tell:** `docker ps` still lists a running swss after inject → stop didn't take.

---

**2. id: `c_vlan_churn_50` — "Flood the pipeline: fifty VLANs at once" (ANCHOR B)**
- **type:** churn · **risk:** medium · **enabled:** true
- **inject:**
  - `leaf1: sudo config vlan add -m 100-149`
- **restore:**
  - `leaf1: sudo config vlan del -m 100-149`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** Fifty new VLANs stampede down the staircase. `dbsize` jumps in CONFIG_DB, then APPL_DB, then ASIC_DB — and the *gap in time* between those jumps is the propagation lag made visible. Existing traffic (h1↔h3) should be unaffected; this is a control-plane load test, not a data-plane break. Measure how long until ASIC_DB fully catches up `[VERIFY-ON-LAB: per-stage lag on vs; whether any transient CPU spike]`.
- **plan-B variant:** add the fifty VLANs one-by-one in a timed loop to draw a per-object lag curve instead of one bulk jump.
- **injection-failed tell:** `show vlan brief` shows far fewer than 50 new VLANs, or `config vlan add -m` errored on the range → sequence didn't fully apply.

---

**3. id: `c_stop_orchagent` — "Kill only the translator: stop orchagent"**
- **type:** service · **risk:** high · **enabled:** false  *(supervisor-level kill + recovery semantics on vs unverified)*
- **inject:**
  - `leaf1: docker exec swss supervisorctl stop orchagent`
- **restore:**
  - `leaf1: docker exec swss supervisorctl start orchagent`  `[VERIFY-ON-LAB: whether a clean restart is possible or the whole swss must restart]`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** APPL_DB still fills from the managers, but nothing drains it into ASIC_DB — the staircase breaks at exactly one step. New config strands in APPL_DB; existing hardware state persists. The most surgical demonstration of orchagent's role. Auto-restart policy may resurrect it or take swss down `[VERIFY-ON-LAB]`.
- **plan-B variant:** kill the orchagent PID inside swss instead of using supervisorctl (same effect, blunter).
- **injection-failed tell:** `supervisorctl status` still shows orchagent RUNNING → stop didn't take (or auto-restart already replaced it).

---

**4. id: `c_pause_swss` — "Freeze, don't kill: pause the swss container"**
- **type:** service · **risk:** medium · **enabled:** false  *(container-freeze behavior on vs unverified)*
- **inject:**
  - `leaf1: docker pause swss`
- **restore:**
  - `leaf1: docker unpause swss`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** The subtle one. `docker ps` still lists swss as "up" (Paused) — it *exists* but its processes are frozen, so the pipeline stalls silently. Intent enters CONFIG_DB and goes nowhere; no error, no crash, just no progress. Teaches that "container present" ≠ "container working," mirroring admin-vs-operational one layer up.
- **plan-B variant:** `kill -STOP` / `-CONT` the orchagent PID (freeze one daemon instead of the whole container).
- **injection-failed tell:** `docker ps` shows swss without a Paused state after inject → pause didn't take.

---

**5. id: `c_stop_lldp_feature` — "Turn off a whole feature container: LLDP"**
- **type:** service · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: sudo config feature state lldp disabled`
- **restore:**
  - `leaf1: sudo config feature state lldp enabled`
- **expected effects (words):** A clean, safe demonstration of feature→container coupling. `show feature status` flips lldp to disabled and the lldp container leaves `docker ps` within seconds; APPL_DB's LLDP_ENTRY_TABLE stops being refreshed and its rows age out. Zero data-plane impact — h1↔h3 keeps flowing at 0% loss. Recovery: re-enabling brings the container back and neighbors repopulate.
- **plan-B variant:** `leaf1: sudo systemctl stop lldp` / `start lldp`.
- **injection-failed tell:** `docker ps` still shows the lldp container running after inject → feature change didn't take.

---

**6. id: `c_redis_rogue_key` — "Bypass the CLI: write raw intent into CONFIG_DB"**
- **type:** config · **risk:** medium · **enabled:** false  *(unvalidated-write handling on vs unverified)*
- **inject:**
  - `leaf1: sonic-db-cli CONFIG_DB hset "VLAN|Vlan999" vlanid 999`  `[VERIFY-ON-LAB: whether vlanmgrd consumes an un-validated raw key and programs it]`
- **restore:**
  - `leaf1: sonic-db-cli CONFIG_DB del "VLAN|Vlan999"`
  - `leaf1: sudo config vlan del 999`  *(belt-and-suspenders if it propagated)*
- **expected effects (words):** Tests whether the pipeline trusts CONFIG_DB blindly. If the manager consumes the raw key, Vlan999 appears in APPL_DB and ASIC_DB despite never passing the CLI's validation — a pointed lesson in why writing Redis directly is dangerous. If the manager rejects malformed intent, nothing propagates — an equally useful result about where validation actually lives.
- **plan-B variant:** write a malformed field into an existing VLAN key and watch the logs for a rejection (`leaf1: tail -50 /var/log/syslog`).
- **injection-failed tell:** neither propagation nor a log complaint appears → the write went nowhere observable (report as "no observable effect," not a network failure).

---

**7. id: `c_stop_syncd` — "Sever the ASIC link: stop syncd"**
- **type:** service · **risk:** high · **enabled:** false  *(recovery frequently requires swss co-restart on vs)*
- **inject:**
  - `leaf1: sudo systemctl stop syncd`
- **restore:**
  - `leaf1: sudo systemctl start syncd`
  - `leaf1: sudo systemctl restart swss`  `[VERIFY-ON-LAB: whether swss restart is required to re-sync]`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** The bottom of the staircase disappears. ASIC_DB may keep accepting desired objects, but nobody pushes them to the (virtual) chip; the data plane freezes at its last programmed state and drifts from intent. Often the messiest to recover — the pipeline usually needs a coordinated restart. The clearest picture of syncd as the sole hardware executor.
- **plan-B variant:** `leaf1: docker stop syncd` / `docker start syncd`.
- **injection-failed tell:** `docker ps` still shows syncd running → stop didn't take.

---

**8. id: `c_config_reload` — "The big replay: reload the entire configuration"**
- **type:** churn · **risk:** high · **enabled:** false  *(long, disruptive; whole-pipeline replay timing on vs unverified)*
- **inject:**
  - `leaf1: sudo config reload -y`
- **restore:** *(self-completing — restore = wait for services and verify baseline)*
  - `h1: ping -c 3 10.0.1.1`
  - `h1: ping -c 3 10.0.2.10`
- **expected effects (words):** The nuclear replay: services stop, CONFIG_DB is re-applied from scratch, and the entire staircase rebuilds from the top. Everything churns — dbsizes dip and recover, containers restart, BGP re-establishes. A whole-system view of the pipeline cold-starting. Expect a minute-scale outage window `[VERIFY-ON-LAB: total recovery time on vs]`.
- **plan-B variant:** `leaf1: sudo systemctl restart swss` (partial replay of just the swss stage).
- **injection-failed tell:** `docker ps` uptimes are unchanged after inject → reload didn't actually run.

---

## Observe

Parsers available: `interface_status, mac_table, lldp_neighbors, vlan_membership, bgp_neighbors, route_count, ping_loss, redis_keys, propagation_lag`. New parsers are marked.

### Happy-path fact map

| scenario (observe id) | parser(s) | key fields to extract | notes |
|---|---|---|---|
| o_containers | **NEW-PARSER: feature_status** (feature, state, autorestart); **NEW-PARSER: docker_ps** (name, status, uptime) | enabled features vs running containers | assert database/swss/syncd/bgp/lldp all running and enabled |
| o_configdb | redis_keys | per query: db, pattern, key_count, sample_keys[]; PORT hgetall fields | expect PORT keys incl. Ethernet0/4/8; VLAN + VLAN_MEMBER; ≥1 BGP_NEIGHBOR |
| o_pipeline | redis_keys (per stage), propagation_lag | Vlan30 key in CONFIG_DB and APPL_DB; SAI VLAN object count before vs after in ASIC_DB; per-stage timestamps | absent→present (CONFIG_DB/APPL_DB) and count+1 (ASIC_DB), in order; lag ms `[VERIFY-ON-LAB: APPL_DB VLAN_TABLE key format; ASIC_DB VLAN object-count behavior]` |
| o_statedb | redis_keys; interface_status | STATE_DB PORT_TABLE oper_status vs `show interfaces status` Oper | assert the two agree for Ethernet8 |
| o_asicdb | redis_keys | counts of SAI_OBJECT_TYPE_PORT / _VLAN keys; VIDTORID sample | assert ≥ (port count) port objects; VID→RID map non-empty `[VERIFY-ON-LAB: VIDTORID key name]` |
| o_counters | redis_keys; **NEW-PARSER: interface_counters** | COUNTERS_PORT_NAME_MAP has Ethernet8→oid; one counter row present | name-map resolves; counters reflect last poll `[VERIFY-ON-LAB: poll interval]` |
| o_admin_path | redis_keys, propagation_lag | admin_status in CONFIG_DB then APPL_DB; oper_status in STATE_DB, across the shutdown | CONFIG_DB down → APPL_DB down → STATE_DB oper down, in order; then restored |
| o_procs | **NEW-PARSER: supervisor_status** (process, state) | swss: orchagent + managers RUNNING; bgp: bgpd/zebra/fpmsyncd RUNNING | assert the named actors are RUNNING `[VERIFY-ON-LAB: exact process names on vs]` |

### After-chaos fact map (same command set for all options)

| chaos id | facts that should CHANGE | facts that should NOT change | measure_recovery |
|---|---|---|---|
| c_stop_swss | docker_ps loses swss; new-config path dead | ideally h1↔h3 ping (data plane persists) `[VERIFY-ON-LAB]` | yes — time to all-green after start |
| c_vlan_churn_50 | CONFIG_DB/APPL_DB/ASIC_DB dbsize +≈50 each, staggered in time | interface_status; ping 0% loss | yes — time to ASIC_DB catch-up |
| c_stop_orchagent | orchagent not RUNNING; APPL_DB→ASIC_DB stalls | docker_ps still lists swss; existing ping | yes |
| c_pause_swss | swss shows Paused; pipeline stalls silently | docker_ps still lists swss; existing ping | yes |
| c_stop_lldp_feature | feature lldp disabled; lldp container gone; LLDP_ENTRY_TABLE ages out | ping 0% loss both probes; interface_status; vlan | no (ageout slow) |
| c_redis_rogue_key | maybe Vlan999 in APPL_DB/ASIC_DB (or nothing) | ping; interface_status | no |
| c_stop_syncd | syncd container gone; data plane frozen at last state | ASIC_DB may retain objects | yes |
| c_config_reload | everything churns; dbsizes dip+recover; container uptimes reset | eventual baseline restored | yes — total rebuild time |

## Knowledge Card

*(Consumed by the runtime LLM for EXPLAIN moments. No outputs here — meanings and expectations in words only.)*

### Objective

The learner can describe SONiC as cooperating containers around a shared Redis, name the databases in the config pipeline and what each holds, trace a single change from CONFIG_DB intent to ASIC_DB/SAI reality (and read every hop live), explain propagation lag and eventual consistency, distinguish intent (CONFIG_DB) from reality (STATE_DB), and reason about which container/database a given failure breaks.

### In-scope subtopics

Container roles (database/swss/syncd/bgp/lldp/…); CONFIG_DB tables and the declarative-intent model; the CONFIG_DB→\*mgrd→APPL_DB→orchagent→ASIC_DB→syncd→SAI staircase; producer/consumer over Redis; propagation lag/eventual consistency; STATE_DB readback; SAI object model and VID/RID; COUNTERS_DB and flex-counter polling with the name map; supervisord-managed processes; the eight chaos options' blast radius and recovery.

### Key concepts (one sentence each)

1. SONiC is a set of Docker containers coordinating exclusively through a shared Redis, not a single monolithic program.
2. CONFIG_DB (db 4) holds declarative intent and changes nothing by itself — it matters only because manager processes are subscribed to it.
3. A configuration change falls through four databases (CONFIG_DB→APPL_DB→ASIC_DB, with STATE_DB as readback), each hop performed by a subscribed process.
4. The pipeline is producer/consumer over Redis and eventually consistent, so there is a real, measurable propagation lag between intent and hardware.
5. swss hosts orchagent and the managers/syncers; syncd is the sole executor that programs the (virtual) ASIC via SAI.
6. STATE_DB (db 6) records observed reality and can disagree with CONFIG_DB — that disagreement is the system's most useful diagnostic.
7. ASIC_DB (db 1) stores desired hardware state as vendor-neutral SAI objects, with a VID-to-RID map bridging database ids and real chip ids.
8. COUNTERS_DB (db 2) is filled by a flex-counter poller, and a name map translates port names to the object ids whose counters you read.
9. Containers are supervised bundles of daemons, so a single daemon can fail (or be killed) with granular, legible impact.
10. Because subsystems are isolated, the control plane and an already-programmed data plane can be separated — stopping swss need not immediately stop forwarding.

### Command field meanings

- **`show feature status`** — Feature: subsystem name. State: enabled/disabled (intent). AutoRestart: restart policy. SystemState/others: runtime detail. Reads from CONFIG_DB/STATE_DB feature tables.
- **`docker ps`** — Linux ground truth: CONTAINER ID, IMAGE, STATUS (Up/Paused/Exited + uptime), NAMES. The reality behind "feature enabled."
- **`sonic-db-cli <DB> keys "<pat>"` / `hgetall "<key>"` / `hget "<key>" <field>` / `dbsize`** — list keys / dump a row / read one field / count keys in a logical DB. Empty results mean zero rows, not error.
- **CONFIG_DB tables** — `PORT|<name>` (admin_status, mtu, speed…), `VLAN|Vlan<n>`, `VLAN_MEMBER|Vlan<n>|<port>`, `BGP_NEIGHBOR|<ip>`, `DEVICE_METADATA|localhost` (hostname, mac, bgp_asn).
- **APPL_DB tables** — producer output for the forwarding layer, e.g. `PORT_TABLE:<name>`, `VLAN_TABLE:Vlan<n>` (note the `:` separator vs CONFIG_DB's `|`).
- **STATE_DB tables** — observed reality, e.g. `PORT_TABLE|<name>` with `oper_status`.
- **ASIC_DB** — `ASIC_STATE:SAI_OBJECT_TYPE_*` desired chip objects; `VIDTORID` maps virtual ids to real ids `[VERIFY-ON-LAB: exact key]`.
- **COUNTERS_DB** — `COUNTERS_PORT_NAME_MAP` (name→oid); `COUNTERS:<oid>` rows of statistics.
- **`docker exec <ctr> supervisorctl status`** — per-container process list with RUNNING/STOPPED/EXITED states.
- **`config vlan add -m <range>` / `del -m <range>`** — bulk VLAN create/delete; `config interface shutdown/startup <port>` — admin down/up; `config reload -y` — stop services and re-apply CONFIG_DB from scratch.
- **`ping -c N <ip>`** (h1) — reachability probe; loss and rtt are data even on non-zero exit.

### Healthy-state expectations (baseline, in words)

- `docker ps` and `show feature status` agree: database, swss, syncd, bgp, lldp (and teamd/pmon/snmp) running/enabled.
- CONFIG_DB has PORT keys for Ethernet0/4/8, a VLAN + VLAN_MEMBER family, and ≥1 BGP_NEIGHBOR (10.0.12.1 / 10.0.12.3).
- Vlan30 absent before the pipeline demo; after `config vlan add 30` it appears in CONFIG_DB, then APPL_DB `VLAN_TABLE`, then in ASIC_DB as a +1 in the SAI VLAN object count (ASIC keys are opaque ids) — in that order, within a small lag `[VERIFY-ON-LAB: per-stage key names/timing]`.
- STATE_DB `PORT_TABLE|Ethernet8` `oper_status` matches `show interfaces status` Oper.
- ASIC_DB has one SAI_OBJECT_TYPE_PORT per front-panel port and a non-empty VID-to-RID map.
- swss supervisor lists orchagent + managers RUNNING; bgp lists bgpd/zebra/fpmsyncd RUNNING `[VERIFY-ON-LAB: names]`.
- h1→10.0.1.1 and h1→10.0.2.10: 0% loss.

### Expected chaos effects per option (timings)

- **c_stop_swss:** swss leaves `docker ps` immediately; feature may still read enabled; new config no longer programs; existing h1↔h3 may keep flowing `[VERIFY-ON-LAB]`. Recovery on start = a rebuild period (tens of seconds?) `[VERIFY-ON-LAB]`.
- **c_vlan_churn_50:** CONFIG_DB dbsize +≈50 immediately; APPL_DB then ASIC_DB rise after it — the visible lag; no data-plane loss. Catch-up time load-dependent `[VERIFY-ON-LAB]`.
- **c_stop_orchagent:** orchagent STOPPED; APPL_DB fills but ASIC_DB stops changing; existing forwarding persists; auto-restart may intervene `[VERIFY-ON-LAB]`.
- **c_pause_swss:** swss shows Paused; pipeline silently stalls; no crash/log; unpause resumes.
- **c_stop_lldp_feature:** lldp container gone within seconds; LLDP_ENTRY_TABLE ages out; 0% data-plane loss throughout; neighbors return after re-enable.
- **c_redis_rogue_key:** Vlan999 either propagates (validation lives in CLI, not pipeline) or is ignored (manager validates) — report which; no data-plane impact either way.
- **c_stop_syncd:** syncd gone; ASIC_DB may still accept objects but chip freezes at last state; recovery usually needs coordinated swss restart `[VERIFY-ON-LAB]`.
- **c_config_reload:** full churn; dbsizes dip and recover; containers restart; BGP re-establishes; minute-scale outage `[VERIFY-ON-LAB]`.

### Misconceptions (wrong → why → correct)

1. **"A `config` command talks to the chip directly."** → It only writes CONFIG_DB; subscribed managers translate it downward. → Configuration is declarative intent; hardware change is an asynchronous consequence of the pipeline.
2. **"All the databases update atomically together."** → They are producer/consumer stages with real lag; changes appear top-first, bottom-last. → SONiC is eventually consistent; propagation lag is expected and measurable.
3. **"If swss is down, packets stop immediately."** → syncd and the ASIC retain programmed state, so forwarding can persist. → Control plane and already-programmed data plane are separable.
4. **"CONFIG_DB shows whether something is actually working."** → CONFIG_DB is intent only; achieved reality lives in STATE_DB. → Compare the two — intent-up/state-down is the key diagnostic.
5. **"ASIC_DB is Broadcom/Mellanox-specific."** → It stores vendor-neutral SAI objects; syncd adapts them to the real chip. → SAI is the abstraction that lets one SONiC speak to many ASICs.
6. **"`show interfaces counters` reads the chip live."** → It reads COUNTERS_DB, filled by a periodic poller via a name map. → Counters reflect the last poll, hence the freshness lag.
7. **"A container is a single program."** → Each is a supervisord-managed bundle of daemons. → Failure and recovery are per-daemon and granular.
8. **"Writing CONFIG_DB with sonic-db-cli is the same as using the CLI."** → Raw writes skip CLI validation; the pipeline may accept or reject them. → Use the CLI to configure; use raw reads to observe; raw writes are experiments.

### Out-of-scope (redirect if asked)

BGP/FRR internals and the route pipeline (Lesson 3), MTU behavior (Lesson 4), Layer-2 forwarding mechanics (Lesson 1), warm/fast reboot internals, kubernetes feature ownership, telemetry/gNMI, platform/PMON hardware bring-up, SAI attribute-level semantics.

### Polite redirect line

"Good question, but it's outside this lesson's scope — how SONiC's containers and databases move a config into the ASIC. I'd recommend reading it up separately (the SONiC wiki Architecture page is the right source). Let's get back to the pipeline on leaf1 — pick a suggested question or run another on-topic command."

## Glossary

- **container** — an isolated Docker process bundle running one SONiC subsystem.
- **database container** — the container running Redis; SONiC's shared state store.
- **swss** — Switch State Service container hosting orchagent and the managers/syncers.
- **orchagent** — the process in swss that consumes APPL_DB and programs ASIC_DB.
- **manager (mgrd)** — a swss process that translates CONFIG_DB intent into APPL_DB entries.
- **syncer (syncd/sync)** — a process that copies state between layers (e.g. records reality into STATE_DB).
- **syncd** — the container/process that programs the ASIC by calling SAI.
- **SAI** — Switch Abstraction Interface: the vendor-neutral object API syncd uses to program the chip.
- **virtual ASIC** — the software switching chip in docker-sonic-vs that obeys the same SAI objects as real hardware.
- **CONFIG_DB** — Redis DB 4: declarative operator intent (PORT, VLAN, BGP_NEIGHBOR, …).
- **APPL_DB** — Redis DB 0: application/forwarding-layer state produced from intent.
- **ASIC_DB** — Redis DB 1: desired chip objects in SAI vocabulary.
- **STATE_DB** — Redis DB 6: observed reality (oper_status and similar readback).
- **COUNTERS_DB** — Redis DB 2: polled counter values plus the port-name map.
- **pipeline** — the ordered flow CONFIG_DB→APPL_DB→ASIC_DB→SAI→chip.
- **producer/consumer** — the pattern where one stage publishes to Redis and the next is subscribed.
- **propagation lag** — the measurable time for a change to travel from CONFIG_DB to ASIC_DB.
- **eventual consistency** — the guarantee that databases converge over time, not instantly or atomically.
- **intent** — desired state written to CONFIG_DB.
- **readback** — observed reality recorded into STATE_DB.
- **VID / RID** — virtual object id (in the databases) / real object id (returned by the chip).
- **flex counter** — the SONiC poller that harvests ASIC statistics into COUNTERS_DB.
- **name map** — COUNTERS_PORT_NAME_MAP translating port names to counter object ids.
- **supervisord** — the in-container process manager starting and watching daemons.
- **feature** — a SONiC-level toggle that enables/disables a subsystem's container.
- **dbsize** — the count of keys in a Redis database, a quick churn gauge.
- **sonic-db-cli** — utility to read (and, dangerously, write) the Redis databases.
- **config reload** — command that stops services and re-applies CONFIG_DB from scratch.

## QnA

### Scope keywords (gate input; generous synonyms)

```
sonic, architecture, container, containers, docker, docker ps, feature,
feature status, swss, orchagent, syncd, sai, bgp container, lldp container,
database container, redis, config_db, appl_db, asic_db, state_db, counters_db,
pipeline, producer, consumer, subscribe, publish, propagation, propagation lag,
eventual consistency, intent, readback, oper_status, admin_status, mgrd,
manager, syncer, vlanmgrd, portmgrd, portsyncd, fpmsyncd, sonic-db-cli, dbsize,
keys, hgetall, vid, rid, vidtorid, flex counter, counters, name map,
supervisord, supervisorctl, process, daemon, systemctl, feature state,
config reload, vlan30, ethernet4, leaf1
```

### Question bank (8 per qna step; TOP 3 marked ★; ordered easy → deep)

#### q_containers
1. ★ What are the main SONiC containers and what does each one do?
2. ★ What's the difference between a feature being "enabled" and its container being "running"?
3. ★ Which containers form the spine of the forwarding pipeline, and why those?
4. Where do all the containers actually keep their state?
5. Why build a switch OS out of containers instead of one program?
6. If `show feature status` and `docker ps` disagreed, which would you believe?
7. What does the database container run, and what breaks if it stops?
8. How is this different from a traditional vendor switch OS architecture?

#### q_configdb
1. ★ What exactly does CONFIG_DB hold, and what does it *not* hold?
2. ★ If CONFIG_DB is just data, how does writing to it ever change the hardware?
3. ★ Where did the VLAN and port settings from Lesson 1 actually go?
4. What's the key-naming convention (TABLE|key) telling me?
5. What is DEVICE_METADATA and why does it matter?
6. Why is CONFIG_DB called "declarative"?
7. Could two operators' changes to CONFIG_DB conflict, and who resolves it?
8. What's the difference between `show runningconfiguration` and reading CONFIG_DB directly?

#### q_pipeline
1. ★ Walk me through every stop Vlan30 made from `config vlan add 30` to the chip.
2. ★ Why isn't the change atomic — what does "eventual consistency" mean here?
3. ★ Which process moves data from CONFIG_DB to APPL_DB, and which from APPL_DB to ASIC_DB?
4. Why does APPL_DB use `:` separators while CONFIG_DB uses `|`?
5. What would I expect to see if I read ASIC_DB a millisecond after the CLI returns?
6. Where does a *route* enter this pipeline differently from a VLAN?
7. How would heavy load change the propagation lag, and why?
8. If Vlan30 appeared in CONFIG_DB but never in ASIC_DB, where would you look first?

#### q_statedb
1. ★ What's the difference between CONFIG_DB and STATE_DB for a port?
2. ★ When would `admin_status` and `oper_status` legitimately disagree?
3. ★ Which database is the "Oper" column of `show interfaces status` really reading?
4. Who writes STATE_DB, and in which direction does its data flow?
5. Can I configure anything by writing STATE_DB? Why not?
6. Is STATE_DB a history log or a snapshot of now?
7. How would STATE_DB help you diagnose a port that won't come up?
8. What other kinds of reality (besides ports) would you expect to find here?

#### q_asicdb
1. ★ What is a SAI object, and why does ASIC_DB use them instead of vendor commands?
2. ★ What is the VID-to-RID map for?
3. ★ Where did the learned MAC from Lesson 1 live in this database?
4. Which process consumes ASIC_DB, and what does it do with it?
5. Why are the ASIC_DB keys so long and machine-generated?
6. On a virtual switch, what is actually "programmed" when syncd calls SAI?
7. How does SAI let one SONiC image run on different vendors' chips?
8. If ASIC_DB has a port object but the port is down, what does that tell you?

#### q_counters
1. ★ Why did `show interfaces counters` sometimes lag behind my traffic in Lesson 1?
2. ★ What is COUNTERS_PORT_NAME_MAP and why is it needed?
3. ★ Which process fills COUNTERS_DB, and how often?
4. What key holds an individual port's counter values?
5. Are these counters read from the chip on demand or on a schedule?
6. Why keep counters in their own database instead of APPL_DB?
7. How would you turn a port name into its counter row by hand?
8. What limits counter accuracy on the virtual switch?

#### q_admin_path
1. ★ Trace `admin_status` from CONFIG_DB to STATE_DB after a shutdown.
2. ★ Why did we pick Ethernet4 for this trace instead of Ethernet8?
3. ★ In what order do the databases reflect the shutdown, and why that order?
4. Which process reacts to the CONFIG_DB change for a port?
5. What does `oper_status=down` in STATE_DB confirm that CONFIG_DB cannot?
6. Would traffic to h3 drop during this trace? Explain.
7. How is this the same machinery as the Vlan30 pipeline demo?
8. If APPL_DB never got the change, which container would you suspect?

#### q_procs
1. ★ What is supervisord doing inside each container?
2. ★ Which processes live in swss, and which in bgp?
3. ★ Why can one daemon crash without rebooting the whole switch?
4. What is fpmsyncd, and which lesson will need it?
5. How does auto-restart policy interact with a crashed daemon?
6. Why does killing one daemon disable exactly one capability?
7. How would you check whether orchagent is actually running?
8. What's the relationship between a "feature," a container, and its processes?

#### q_baseline
1. ★ What are the main SONiC containers and what does each one do?
2. ★ What's the difference between a feature being "enabled" and its container being "running"?
3. ★ Which containers form the spine of the forwarding pipeline, and why those?

#### q_impact
1. ★ What broke, and which databases or containers changed after the injection?
2. ★ Why did the config intent stop reaching the applied state, or not?
3. ★ Which capability is gone now, and which processes explain it?

## Verify-On-Lab

1. **swss stop, data-plane persistence:** with swss stopped, does h1↔h3 keep forwarding, and for how long? Recovery time on `start`; does syncd need co-restart?
2. **Pipeline key formats:** confirm APPL_DB `VLAN_TABLE:Vlan30` key shape and that an ASIC_DB SAI VLAN object appears on `config vlan add 30`; capture per-stage timing for propagation_lag.
3. **VIDTORID key name** in ASIC_DB (exact key for the VID→RID dump).
4. **COUNTERS poll interval** on vs; lag between traffic and visible counter movement.
5. **Supervisor process names** in swss and bgp (orchagent, portmgrd, vlanmgrd, neighsyncd; bgpd, zebra, fpmsyncd) — pin the real list.
6. **c_vlan_churn_50 lag:** per-stage dbsize timing for 50 VLANs; any transient CPU spike.
7. **c_stop_orchagent:** whether supervisorctl can cleanly stop/start orchagent or auto-restart/whole-swss-restart intervenes.
8. **c_pause_swss:** does `docker pause` show a Paused state and stall the pipeline without errors on vs?
9. **c_redis_rogue_key:** does vlanmgrd consume an un-validated raw `VLAN|Vlan999` key (propagate) or reject it (log)?
10. **c_stop_syncd recovery:** confirm whether swss restart is required to re-sync after syncd restart.
11. **c_config_reload total recovery time** on vs (services + BGP re-establish).
12. **feature vs container coupling:** confirm `config feature state lldp disabled` removes the lldp container from `docker ps` within seconds and LLDP_ENTRY_TABLE ages out.

## Machine Summary

```json
{
  "id": "inside_sonic",
  "steps": [
    {"id": "t_containers", "kind": "teach", "core": true},
    {"id": "o_containers", "kind": "observe", "core": true},
    {"id": "q_containers", "kind": "qna", "core": true},
    {"id": "t_configdb", "kind": "teach", "core": true},
    {"id": "o_configdb", "kind": "observe", "core": true},
    {"id": "q_configdb", "kind": "qna", "core": true},
    {"id": "t_pipeline", "kind": "teach", "core": true},
    {"id": "o_pipeline", "kind": "observe", "core": true},
    {"id": "q_pipeline", "kind": "qna", "core": true},
    {"id": "t_statedb", "kind": "teach", "core": false},
    {"id": "o_statedb", "kind": "observe", "core": false},
    {"id": "q_statedb", "kind": "qna", "core": false},
    {"id": "t_asicdb", "kind": "teach", "core": false},
    {"id": "o_asicdb", "kind": "observe", "core": false},
    {"id": "q_asicdb", "kind": "qna", "core": false},
    {"id": "t_counters", "kind": "teach", "core": false},
    {"id": "o_counters", "kind": "observe", "core": false},
    {"id": "q_counters", "kind": "qna", "core": false},
    {"id": "t_admin_path", "kind": "teach", "core": false},
    {"id": "o_admin_path", "kind": "observe", "core": false},
    {"id": "q_admin_path", "kind": "qna", "core": false},
    {"id": "t_procs", "kind": "teach", "core": false},
    {"id": "o_procs", "kind": "observe", "core": false},
    {"id": "q_procs", "kind": "qna", "core": false},
    {"id": "q_baseline", "kind": "qna", "core": true},
    {"id": "chaos", "kind": "chaos_select", "core": true},
    {"id": "q_impact", "kind": "qna", "core": true},
    {"id": "restore", "kind": "restore", "core": true}
  ],
  "commands": {
    "o_containers": ["leaf1: supervisorctl status", "leaf1: show feature status"],
    "o_configdb": ["leaf1: sonic-db-cli CONFIG_DB keys \"PORT|*\"", "leaf1: sonic-db-cli CONFIG_DB hgetall \"PORT|Ethernet8\"", "leaf1: sonic-db-cli CONFIG_DB keys \"VLAN*\"", "leaf1: sonic-db-cli CONFIG_DB keys \"BGP_NEIGHBOR*\""],
    "o_pipeline": ["leaf1: sonic-db-cli CONFIG_DB keys \"VLAN|Vlan30\"", "leaf1: sonic-db-cli ASIC_DB keys \"ASIC_STATE:SAI_OBJECT_TYPE_VLAN*\"", "leaf1: sudo config vlan add 30", "leaf1: sonic-db-cli CONFIG_DB keys \"VLAN|Vlan30\"", "leaf1: sonic-db-cli APPL_DB keys \"VLAN_TABLE:Vlan30\"", "leaf1: sonic-db-cli ASIC_DB keys \"ASIC_STATE:SAI_OBJECT_TYPE_VLAN*\"", "leaf1: sudo config vlan del 30"],
    "o_statedb": ["leaf1: sonic-db-cli STATE_DB keys \"PORT_TABLE|Ethernet8\"", "leaf1: sonic-db-cli STATE_DB hgetall \"PORT_TABLE|Ethernet8\"", "leaf1: show interfaces status"],
    "o_asicdb": ["leaf1: sonic-db-cli ASIC_DB keys \"ASIC_STATE:SAI_OBJECT_TYPE_PORT*\"", "leaf1: sonic-db-cli ASIC_DB keys \"ASIC_STATE:SAI_OBJECT_TYPE_VLAN*\"", "leaf1: sonic-db-cli ASIC_DB hgetall \"VIDTORID\""],
    "o_counters": ["leaf1: sonic-db-cli COUNTERS_DB keys \"COUNTERS_PORT_NAME_MAP\"", "leaf1: sonic-db-cli COUNTERS_DB hgetall \"COUNTERS_PORT_NAME_MAP\"", "leaf1: show interfaces counters"],
    "o_admin_path": ["leaf1: sonic-db-cli CONFIG_DB hget \"PORT|Ethernet4\" admin_status", "leaf1: sudo config interface shutdown Ethernet4", "leaf1: sonic-db-cli CONFIG_DB hget \"PORT|Ethernet4\" admin_status", "leaf1: sonic-db-cli APPL_DB hget \"PORT_TABLE:Ethernet4\" admin_status", "leaf1: sonic-db-cli STATE_DB hget \"PORT_TABLE|Ethernet4\" oper_status", "leaf1: sudo config interface startup Ethernet4"],
    "o_procs": ["leaf1: supervisorctl status orchagent portmgrd vlanmgrd neighsyncd", "leaf1: supervisorctl status bgpd zebra fpmsyncd staticd"],
    "after_chaos": ["leaf1: supervisorctl status", "leaf1: show feature status", "leaf1: show interfaces status", "leaf1: show vlan brief", "leaf1: sonic-db-cli CONFIG_DB dbsize", "leaf1: sonic-db-cli APPL_DB dbsize", "leaf1: sonic-db-cli ASIC_DB dbsize", "h1: ping -c 5 10.0.1.1", "h1: ping -c 5 10.0.2.10"]
  },
  "chaos_ids": ["c_stop_swss", "c_vlan_churn_50", "c_stop_orchagent", "c_pause_swss", "c_stop_lldp_feature", "c_redis_rogue_key", "c_stop_syncd", "c_config_reload"],
  "enabled_chaos": ["c_stop_swss", "c_vlan_churn_50", "c_stop_lldp_feature"],
  "keywords": ["sonic", "architecture", "container", "docker", "docker ps", "feature", "feature status", "swss", "orchagent", "syncd", "sai", "bgp container", "lldp container", "database container", "redis", "config_db", "appl_db", "asic_db", "state_db", "counters_db", "pipeline", "producer", "consumer", "subscribe", "publish", "propagation", "propagation lag", "eventual consistency", "intent", "readback", "oper_status", "admin_status", "mgrd", "manager", "syncer", "vlanmgrd", "portmgrd", "portsyncd", "fpmsyncd", "sonic-db-cli", "dbsize", "keys", "hgetall", "vid", "rid", "vidtorid", "flex counter", "name map", "supervisord", "supervisorctl", "process", "daemon", "systemctl", "config reload"],
  "glossary_terms": ["container", "database container", "swss", "orchagent", "manager (mgrd)", "syncer (syncd/sync)", "syncd", "SAI", "virtual ASIC", "CONFIG_DB", "APPL_DB", "ASIC_DB", "STATE_DB", "COUNTERS_DB", "pipeline", "producer/consumer", "propagation lag", "eventual consistency", "intent", "readback", "VID / RID", "flex counter", "name map", "supervisord", "feature", "dbsize", "sonic-db-cli", "config reload"]
}
```
