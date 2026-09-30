# Lesson: mtu_mismatch — The config pipeline betrayed: MTU mismatch

## Meta

- **id:** `mtu_mismatch`
- **difficulty:** core
- **requires:** `switches_explained`, `inside_sonic`, `bgp_reconvergence` (assumes: frames/MTU column, the config pipeline, ECMP + control-vs-data plane)
- **target image:** docker-sonic-vs, branch **202405** (Containerlab: leaf1/leaf2 SONiC, h1–h4 hosts; same BGP/ECMP topology as Lesson 3)
- **devices used in this lesson:** `leaf1`, `leaf2`, `h1`, `h3`
- **lab prerequisites assumed by this lesson (bake into topology):**
  - full Lesson-3 topology at baseline: eBGP established, 10.0.2.0/24 reachable from h1 via ECMP over Ethernet0/Ethernet4
  - **all ports MTU 9100** at baseline (inter-switch AND access AND host `eth1`), so a jumbo path exists end to end
  - h1 = 10.0.1.10/24 (gw 10.0.1.1); h3 = 10.0.2.10/24 (gw 10.0.2.1); both host `eth1` MTU 9100
- **command execution targets:** `leaf1:`/`leaf2:` = docker exec into SONiC vs; `h1:`/`h3:` = host containers. Linux DF-ping: `ping -M do -s <payload>` sets the Don't-Fragment bit.
- **packet-size arithmetic used throughout:** IP packet size = ICMP payload + 8 (ICMP header) + 20 (IP header) = payload + 28. So payload **1472** → 1500-byte packet (fits MTU 1500); payload **8972** → 9000-byte packet (needs a jumbo path, fits MTU 9100 with margin).
- **source URLs relied on:**
  - SONiC CLI Reference (202405) — Interfaces (`config interface mtu`, `show interfaces status` MTU column): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#interfaces>
  - SONiC CLI Reference (202405) — IP/IPv6 (`show ip interfaces`, `show ip route`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#ip--ipv6>
  - SONiC CLI Reference (202405) — BGP (`show bgp summary`): <https://github.com/sonic-net/sonic-utilities/blob/202405/doc/Command-Reference.md#bgp>
  - SONiC Architecture wiki (PORT.mtu through CONFIG_DB→APPL_DB→ASIC, netdev MTU): <https://github.com/sonic-net/SONiC/wiki/Architecture>
  - RFC 791 (IP fragmentation, DF bit), RFC 1191 (Path MTU Discovery), RFC 4821 (packetization-layer PMTUD) — concept grounding
  - `ping`, `ip link`, `sonic-db-cli`, `iptables` — standard tooling shipped in the image

## Coverage Notes

- **TEACHES:** MTU and why it exists; jumbo frames; fragmentation and the Don't-Fragment (DF) bit; Path MTU Discovery and the ICMP "fragmentation needed" message; the "up but broken" failure class and a systematic detection method; where MTU lives in SONiC (PORT.mtu through the pipeline to the kernel netdev); why TCP control sessions (BGP) survive an MTU fault that kills bulk data (MSS).
- **REINFORCES:** the config pipeline (now for the `mtu` field); ECMP + control-vs-data plane (an MTU fault on one ECMP member = per-flow breakage); frames vs packets (Lesson 1); reading interface status and counters.
- **ASSUMES:** Lessons 1–3; software literacy (headers/overhead, chunking, handshake vs payload).
- **PREVIEWS:** nothing (final lesson) — instead it *synthesizes* L1 (frames), L2 (pipeline), L3 (ECMP/planes).
- **DELIBERATELY OUT OF SCOPE:** MSS clamping configuration, QoS/buffers, IPv6 PMTUD specifics, TCP congestion control, GRE/VXLAN overhead math beyond the ICMP example.

## Step Plan

| # | step id | kind | title | core-or-optional |
|---|---------|------|-------|------------------|
| 1 | t_mtu | teach | S1: MTU — the biggest box a link will carry | core |
| 2 | o_mtu | observe | S1: Read every port's MTU on both leafs | core |
| 3 | q_mtu | qna | S1: Questions — MTU basics | core |
| 4 | t_bigpath | teach | S2: Proving the jumbo path — a DF ping size sweep | core |
| 5 | o_bigpath | observe | S2: h1 to h3 at 1472 and 8972 payload with DF set | core |
| 6 | q_bigpath | qna | S2: Questions — packet sizes & overhead | core |
| 7 | t_mtu_redis | teach | S3: Where MTU lives — CONFIG_DB intent to kernel reality | core |
| 8 | o_mtu_redis | observe | S3: PORT.mtu across CONFIG_DB, APPL_DB, and the netdev | core |
| 9 | q_mtu_redis | qna | S3: Questions — MTU in the pipeline | core |
| 10 | t_frag | teach | S4: Fragmentation and the DF bit — split it or say so | optional |
| 11 | o_frag | observe | S4: The same oversized ping with and without DF | optional |
| 12 | q_frag | qna | S4: Questions — fragmentation & PMTUD | optional |
| 13 | t_ecmp_mtu | teach | S5: ECMP times MTU — why "sometimes broken" exists | optional |
| 14 | o_ecmp_mtu | observe | S5: Both ECMP members at 9100 (the healthy precondition) | optional |
| 15 | q_ecmp_mtu | qna | S5: Questions — hashing meets MTU | optional |
| 16 | t_detect | teach | S6: The detection method — a checklist for "up but broken" | optional |
| 17 | o_detect | observe | S6: Status vs counters vs targeted DF probes | optional |
| 18 | q_detect | qna | S6: Questions — detection | optional |
| 19 | t_bgp_mtu | teach | S7: Why BGP survives what your data doesn't (MSS) | optional |
| 20 | o_bgp_mtu | observe | S7: Session uptime and endpoints beside the MTU facts | optional |
| 21 | q_bgp_mtu | qna | S7: Questions — MTU vs the control plane | optional |
| 22 | chaos | chaos_select | Pick one failure to inject (7 options) | core |
| 23 | q_impact | qna | Questions — interrogate the failure you injected | core |
| 24 | restore | restore | Heal the lab, re-run the jumbo sweep to verify | core |

## Teach Sections

### SECTION: t_mtu — S1: MTU — the biggest box a link will carry

**Essence.** Every link has a size limit: the largest frame it will carry, called the **MTU** (maximum transmission unit). It is the width of the doorway. Send something that fits and it passes; send something too big and one of two things happens — the network splits it up, or it drops it silently. This whole lesson is about that second outcome, because it produces failures where every status light stays green.

**Mechanism.** MTU is measured as the largest **IP packet** (the frame's payload) a link accepts, in bytes. The classic Ethernet default is **1500**. A **jumbo frame** is anything larger — this lab runs **9100**, common in data centers because bigger frames mean less per-packet overhead and higher throughput. Two facts make MTU dangerous:

1. It is a property of *each link independently*. A path is only as wide as its **narrowest** link — the **path MTU**. One skinny hop bottlenecks an otherwise-jumbo path.
2. Crossing it is not loud. Depending on a single bit in the packet (the DF bit, scenario S4), an oversized packet is either fragmented (works, slower) or **dropped** — and the drop can be silent, far from the sender, with no error the user sees.

So MTU is the perfect "up but broken" trap: the link is administratively and operationally up, small packets sail through, and only *large* packets vanish — often only *some* large packets, if ECMP is involved.

**On SONiC.** `show interfaces status` has an **MTU** column (you first met it in Lesson 1 and were told to ignore it — no longer). The observe step reads it on both leafs. At baseline every port reads **9100** — a consistent, jumbo-capable fabric. Remember this number; almost every failure in this lesson is one port quietly reading 1500 instead.

**Boundaries.** MTU is about *size*, not reachability or routing — the route can be perfect and the session up while an MTU fault blackholes big packets. How the size limit is enforced, and by which bit, are covered in scenarios S3 and S4.

### SECTION: t_bigpath — S2: Proving the jumbo path — a DF ping size sweep

**Essence.** You cannot trust a jumbo fabric until you have pushed a jumbo packet end to end and watched it arrive. The tool is a **size sweep**: ping with growing payloads, with the **Don't-Fragment bit set**, so the network is forbidden from cheating by splitting your packet. What survives tells you the true path MTU.

**Mechanism.** A normal ping sends tiny packets that fit anything, so it proves reachability but nothing about size. Two changes make it an MTU probe:

- **`-s <payload>`** sets the ICMP payload size. Total packet = payload + 28 (8 ICMP + 20 IP). So `-s 1472` is exactly a 1500-byte packet; `-s 8972` is a 9000-byte packet.
- **`-M do`** sets the **DF (Don't-Fragment) bit**: routers are ordered *not* to fragment. If the packet is too big for any hop, that hop must drop it (and ideally send back an ICMP "fragmentation needed" — scenario S4). No silent splitting to paper over the problem.

So `ping -M do -s 1472` succeeding proves the path carries at least 1500; `ping -M do -s 8972` succeeding proves the path carries at least 9000 — i.e., the jumbo fabric truly works end to end. When you later break MTU, the *small* sweep keeps passing while the *large* one dies: the signature of an MTU fault.

**On SONiC.** The observe step runs the sweep from h1 to h3: `ping -M do -s 1472` (must pass at baseline) then `ping -M do -s 8972` (must also pass at baseline, proving the 9100 fabric). Both succeeding is your green light. Keep this exact pair — the restore step re-runs it to confirm healing, and every chaos option is graded by which half fails.

**Boundaries.** This proves *size* capability, not throughput or latency. And it proves the path *as currently hashed* — a subtlety that matters under ECMP (scenario S5), where different flows may take different-MTU links.

### SECTION: t_mtu_redis — S3: Where MTU lives — CONFIG_DB intent to kernel reality

**Essence.** MTU is not a mysterious hardware knob — it is a field, `mtu`, on a port object, and it flows through the exact pipeline you learned in Lesson 2: intent in CONFIG_DB, translated into APPL_DB, programmed toward the ASIC, and mirrored onto the Linux network device. This scenario makes MTU concrete as data you can read at every layer.

**Mechanism.** When you run `config interface mtu Ethernet0 1500`, you write `mtu=1500` into `CONFIG_DB PORT|Ethernet0`. `portmgrd`/`portsyncd` react: the value lands in `APPL_DB PORT_TABLE:Ethernet0`, orchagent programs the port object's MTU through ASIC_DB/syncd, and the kernel **netdev** for that port is set to 1500 as well (SONiC keeps the Linux interface and the ASIC in step). So an MTU mismatch is not exotic — it is one number, in one pipeline, out of agreement with the port on the *other* end of the link. The "config pipeline betrayed" framing of this lesson is literal: the pipeline faithfully programs exactly the (wrong) intent you gave it, and the network breaks precisely because the machinery worked.

**On SONiC.** The observe step reads `mtu` three ways at baseline: `sonic-db-cli CONFIG_DB hget "PORT|Ethernet0" mtu` (intent), `sonic-db-cli APPL_DB hget "PORT_TABLE:Ethernet0" mtu` (translated), and the kernel view via `show interfaces status` plus (optionally) the netdev MTU. All read 9100 — intent, application state, and reality in agreement. When a chaos option sets 1500, you can watch the disagreement appear layer by layer, then localize it to exactly one port.

**Boundaries.** This traces the *port* MTU specifically; VLAN-interface and portchannel MTU exist but the lab doesn't exercise them. The pipeline mechanics are Lesson 2's subject — here we only use them to *locate* an MTU value precisely.

### SECTION: t_frag — S4: Fragmentation and the DF bit — split it or say so

**Essence.** When a packet is too big for the next link, IP has exactly two legal responses, and a single bit chooses between them. Without the **DF bit**, the router **fragments** — chops the packet into link-sized pieces that are reassembled at the destination. With the DF bit set, fragmentation is forbidden, so the router must **drop** the packet and send back an ICMP "fragmentation needed" message. Understanding this bit is understanding why some oversized traffic "works but is slow" and other oversized traffic "vanishes."

**Mechanism.** Fragmentation (RFC 791) lets an oversized packet cross a narrow link by splitting it; the cost is CPU, reassembly, and fragility (lose one fragment, lose the whole packet). The **DF bit** exists because higher layers often *want* to know the real path MTU rather than silently pay the fragmentation tax — this is **Path MTU Discovery (PMTUD, RFC 1191)**: senders set DF, and when a too-big packet is dropped, the returning ICMP "fragmentation needed (and DF set)" message *tells the sender the correct size* so it can shrink. PMTUD only works if that ICMP message gets back — and the classic disaster (chaos option 7) is a firewall eating those messages, turning a helpful signal into a silent blackhole for large packets.

**On SONiC.** The observe step runs the same oversized ping twice — once **with** `-M do` and once **without**. At baseline the fabric is 9100 everywhere, so both succeed: that is your control reading. The contrast comes alive the moment a chaos option narrows the path — the after-chaos probes repeat this exact pair, and you will watch the DF version die (dropped, or answered with an ICMP size message) while the DF-less version survives by being fragmented. Same packet, opposite fate, decided by one bit — the clearest possible demonstration of why MTU faults are so slippery: turn off DF and the problem "disappears," which is exactly why they hide.

**Boundaries.** IPv4 fragmentation specifics (offsets, reassembly timers) are deeper than needed; IPv6 (which pushes fragmentation entirely to the sender) is out of scope. We need only: DF unset → fragment; DF set → drop + ICMP; PMTUD depends on that ICMP surviving.

### SECTION: t_ecmp_mtu — S5: ECMP times MTU — why "sometimes broken" exists

**Essence.** Combine two things you already know — ECMP spreads flows across parallel links (Lesson 3), and MTU is per-link (S1) — and you get the most bewildering failure in networking: **some flows work and others don't**, with no apparent pattern. This scenario sets up the healthy precondition so the chaos options can produce the effect.

**Mechanism.** Recall that ECMP hashes each flow onto one of the next-hops and keeps it there. Now suppose one of the two parallel links has MTU 1500 while the other has 9100. A large packet's fate depends entirely on *which link its flow was hashed onto*: flows on the 9100 link sail through; flows on the 1500 link are dropped (with DF) or fragmented. Same source, same destination, same instant — opposite results, determined by an invisible hash. To the user it looks like "the network is flaky." To you, armed with this lesson, it is a per-link MTU mismatch on an ECMP member, and it is diagnosable. The baseline (both members 9100) is the control case: *every* flow works, proving the fabric before you break one member.

**On SONiC.** The observe step confirms the healthy precondition: both ECMP members (Ethernet0, Ethernet4) read MTU 9100 (`show interfaces status`) and the route for 10.0.2.0/24 has both next-hops (`show ip route 10.0.2.0/24` — reused from Lesson 3). With both members equal, the jumbo sweep from S2 passes for every flow. The chaos options then lower one member and let hashing decide who suffers.

**Boundaries.** Which specific flow lands on which link is hash-dependent and not something you steer here; the teaching point is the *mechanism* of intermittency, not controlling it. Repeated probes may need different flow tuples to reliably hit the broken link `[VERIFY-ON-LAB: how reliably a single ping tuple lands on the narrowed member]`.

### SECTION: t_detect — S6: The detection method — a checklist for "up but broken"

**Essence.** MTU faults defeat the reflexes that catch most outages: the link is up, the route is present, the session is Established, and small pings work. You need a *method* that targets size specifically. This scenario turns the lesson's tools into a repeatable diagnostic checklist.

**Mechanism.** The checklist, in order, each step ruling out a class of cause:

1. **Reachability?** Plain `ping` (tiny packets). If this fails, it's not MTU — it's a route/link/session problem (Lessons 1–3).
2. **Size ceiling?** DF size sweep (`ping -M do -s 1472`, then `-s 8972`). Small passes, large fails → an MTU fault on the path.
3. **Where?** Read the MTU column across every hop (`show interfaces status` on both leafs; host `ip link`). Find the port reading 1500 among 9100s.
4. **Confirm the bit's role.** Repeat the large ping *without* `-M do`; if it now succeeds (via fragmentation), you've proven the ceiling was the problem, not reachability.
5. **Intermittent?** If large pings fail *sometimes*, suspect ECMP + a per-member MTU mismatch (S5); probe repeatedly and correlate.

The essence is: **status and routes lie about size; only a DF probe tells the truth, and only the MTU column tells you where.**

**On SONiC.** The observe step runs the checklist against the current state: plain ping, DF sweep, the MTU column on both leafs, and the ECMP route view — so the learner practices the method on a healthy system before applying it to a broken one after injection.

**Boundaries.** This method localizes *MTU* faults specifically; it is not a general troubleshooting flow. It assumes you can read every hop's MTU, which in this lab you can (four devices); large real networks need per-hop probing (traceroute-style PMTUD) that we only gesture at.

### SECTION: t_bgp_mtu — S7: Why BGP survives what your data doesn't (MSS)

**Essence.** Here is the cruel twist that makes MTU faults so confusing: you break the fabric with a 1500-byte MTU, and **BGP stays perfectly Established** while h1→h3 data dies. The control plane shrugs off the very fault that blackholes user traffic. Understanding why closes the loop on "up but broken."

**Mechanism.** BGP runs over TCP, and TCP negotiates an **MSS** (maximum segment size) at connection setup — each side advertises how big a segment it will accept, derived from its local MTU, and TCP then *never sends a segment larger than the smaller MSS*. In other words, TCP does its own miniature PMTUD at the start and keeps its packets small enough to fit. BGP's messages (OPENs, tiny periodic KEEPALIVEs, modest UPDATEs) are small anyway and comfortably under even a 1500 MTU. So the session's packets always fit, even across the narrowed link — while h1's *bulk* data, which happily emits big packets, slams into the 1500 ceiling and is dropped. The session is a small-packet conversation; the data is a big-packet firehose. Same broken link, opposite outcomes — the ultimate proof that "control plane healthy" and "data plane working" are independent (Lesson 3), now with MTU as the wedge.

**On SONiC.** The observe step places the two truths side by side: `show bgp summary` (session Established, uptime climbing — control plane serene) next to the MTU facts and the failing jumbo sweep from S2 (data plane broken). Two windows onto one link, disagreeing — exactly the picture a real operator stares at, now legible.

**Boundaries.** MSS clamping (a config that forces TCP even smaller to dodge MTU issues) is real but out of scope; we only need that TCP self-limits and BGP's packets are small. Why *some* protocols (UDP bulk, tunneled traffic) are especially MTU-fragile is a natural extension, not covered here.

## Commands

Convention: `<target>: <command>`. DF ping = `ping -M do -s <payload>`.

### Per-scenario observe commands

#### o_mtu
- `leaf1: show interfaces status`
- `leaf2: show interfaces status`

#### o_bigpath
- `h1: ping -c 3 10.0.2.10`
- `h1: ping -M do -s 1472 -c 3 10.0.2.10`
- `h1: ping -M do -s 8972 -c 3 10.0.2.10`

#### o_mtu_redis
- `leaf1: sonic-db-cli CONFIG_DB hget "PORT|Ethernet0" mtu`
- `leaf1: sonic-db-cli APPL_DB hget "PORT_TABLE:Ethernet0" mtu`
- `leaf1: show interfaces status`

#### o_frag
- `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- `h1: ping -s 8972 -c 3 10.0.2.10`

#### o_ecmp_mtu
- `leaf1: show interfaces status`
- `leaf1: show ip route 10.0.2.0/24`
- `h1: ping -M do -s 8972 -c 3 10.0.2.10`

#### o_detect
- `h1: ping -c 3 10.0.2.10`
- `h1: ping -M do -s 1472 -c 3 10.0.2.10`
- `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- `leaf1: show interfaces status`
- `leaf2: show interfaces status`

#### o_bgp_mtu
- `leaf1: show bgp summary`
- `leaf1: show interfaces status`
- `h1: ping -M do -s 8972 -c 3 10.0.2.10`

### After-chaos commands

Run after any injection (the engine diffs these against baseline facts):

- `leaf1: show interfaces status`
- `leaf2: show interfaces status`
- `leaf1: show ip route 10.0.2.0/24`
- `leaf1: show bgp summary`
- `h1: ping -c 5 10.0.2.10`
- `h1: ping -M do -s 1472 -c 5 10.0.2.10`
- `h1: ping -M do -s 8972 -c 5 10.0.2.10`
- `h1: ping -s 8972 -c 5 10.0.2.10`

### Vocabulary

```
show interfaces status*
show interfaces counters*
show ip interfaces*
show ip route*
show bgp summary*
config interface mtu*
sonic-db-cli CONFIG_DB *
sonic-db-cli APPL_DB *
sonic-db-cli STATE_DB *
redis-cli *
ping*
ip link show*
ip link set*
ip addr show*
tracepath*
mtu*
```

## Chaos Options

All options are restorable to baseline (every port back to MTU 9100, all rules removed). "Injection-failed tell" = how to detect the injection itself did not take. Grading signature throughout: **small DF ping (1472) passes, large DF ping (8972) fails** = MTU fault.

---

**1. id: `c_mtu1500_one_side` — "The classic mismatch: 1500 on one end of one link" (ANCHOR)**
- **type:** config · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: sudo config interface mtu Ethernet0 1500`
- **restore:**
  - `leaf1: sudo config interface mtu Ethernet0 9100`
  - `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- **expected effects (words):** Ethernet0 now reads MTU 1500 while leaf2's end reads 9100 — a true mismatch on one ECMP member. The link stays Oper/Admin up; BGP stays Established (small packets fit); plain ping works; `ping -M do -s 1472` works. But `ping -M do -s 8972` fails **for flows hashed onto Ethernet0** — direction- and hash-dependent, because oversized frames hit the 1500 ceiling and are dropped `[VERIFY-ON-LAB: which direction enforces (leaf1 ingress vs egress) and how reliably a single tuple lands on Ethernet0]`. The MTU column on leaf1 is the one place the fault is visible. Recovery: set 9100 back; jumbo sweep passes again.
- **plan-B variant:** apply the 1500 to **leaf2's** Ethernet0 instead — mirror the asymmetry from the far side.
- **injection-failed tell:** `show interfaces status` still shows Ethernet0 MTU 9100 after inject → the mtu write didn't take.

---

**2. id: `c_mtu1500_one_link` — "One skinny lane in the ECMP pair"**
- **type:** config · **risk:** low · **enabled:** true
- **inject:**
  - `leaf1: sudo config interface mtu Ethernet0 1500`
  - `leaf2: sudo config interface mtu Ethernet0 1500`
- **restore:**
  - `leaf1: sudo config interface mtu Ethernet0 9100`
  - `leaf2: sudo config interface mtu Ethernet0 9100`
  - `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- **expected effects (words):** Both ends of the *first* ECMP link are 1500; the *second* link (Ethernet4) stays 9100. Now the fault is symmetric on one path but the other path is fine — the textbook **intermittent** failure: jumbo flows hashed to Ethernet0 die, jumbo flows hashed to Ethernet4 succeed, small flows all succeed. Repeated `ping -M do -s 8972` may pass some runs and fail others depending on hashing `[VERIFY-ON-LAB: probe variability]`. The clearest ECMP×MTU demonstration. Recovery: both ends back to 9100.
- **plan-B variant:** narrow **Ethernet4** instead of Ethernet0 — proves the effect is about "one member," not a specific port.
- **injection-failed tell:** both members still read 9100, or only one end changed → mismatch not established as intended.

---

**3. id: `c_mtu1500_all_links` — "Everything skinny: whole fabric at 1500"**
- **type:** config · **risk:** medium · **enabled:** true
- **inject:**
  - `leaf1: sudo config interface mtu Ethernet0 1500`
  - `leaf1: sudo config interface mtu Ethernet4 1500`
  - `leaf2: sudo config interface mtu Ethernet0 1500`
  - `leaf2: sudo config interface mtu Ethernet4 1500`
- **restore:**
  - `leaf1: sudo config interface mtu Ethernet0 9100`
  - `leaf1: sudo config interface mtu Ethernet4 9100`
  - `leaf2: sudo config interface mtu Ethernet0 9100`
  - `leaf2: sudo config interface mtu Ethernet4 9100`
  - `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- **expected effects (words):** Both ECMP members, both ends, at 1500. Now the jumbo blackhole is **deterministic** — *every* flow's large DF ping fails, regardless of hashing, while small pings and BGP are untouched. The cleanest "up but broken" for a demo: 100% reachability, 100% small-packet success, 100% large-packet failure. Recovery: all four back to 9100; jumbo sweep passes.
- **plan-B variant:** additionally lower the *access* ports and host `eth1` to 1500 — making the path *consistently* 1500 end to end, which actually **works** for ≤1500 traffic and teaches that "mismatch," not "small," is the real problem.
- **injection-failed tell:** any of the four ports still reads 9100 → not all links narrowed; effect may look intermittent instead of deterministic.

---

**4. id: `c_host_mtu` — "The host is the bottleneck"**
- **type:** config · **risk:** low · **enabled:** true
- **inject:**
  - `h1: ip link set eth1 mtu 1500`
- **restore:**
  - `h1: ip link set eth1 mtu 9100`
  - `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- **expected effects (words):** The narrow hop is now the *source itself*. h1 simply cannot emit a 9000-byte DF packet — `ping -M do -s 8972` fails **locally** with a "message too long"/"local error" before anything leaves h1, a categorically different signature from an in-network silent drop. Small pings and BGP unaffected. Teaches that "the network is broken" is sometimes "the endpoint is misconfigured," and that source-local errors are *loud* where in-network MTU drops are *silent*. Recovery: set eth1 back to 9100.
- **plan-B variant:** narrow **h3's** eth1 instead — the far endpoint; return-direction jumbo replies fail while h1's forward path is fine.
- **injection-failed tell:** `h1: ip link show eth1` still shows mtu 9100 → the host change didn't take.

---

**5. id: `c_access_port_mtu` — "Choke the front door: the access port"**
- **type:** config · **risk:** medium · **enabled:** false  *(ingress-oversize enforcement direction on vs access ports unverified)*
- **inject:**
  - `leaf1: sudo config interface mtu Ethernet8 1500`  `[VERIFY-ON-LAB: whether vs enforces MTU on the access netdev and in which direction]`
- **restore:**
  - `leaf1: sudo config interface mtu Ethernet8 9100`
  - `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- **expected effects (words):** h1 stays 9100 but its *first hop into the switch* (Ethernet8) is 1500. Oversized frames from h1 hit the narrow ingress port; the DF jumbo ping fails at the very first hop while everything else looks perfect. A single-port fault at the network edge rather than in the fabric — tests whether the learner checks the access port, not just the inter-switch links. Recovery: Ethernet8 back to 9100.
- **plan-B variant:** narrow **leaf2's** Ethernet8 (h3's access port) — breaks only the return direction.
- **injection-failed tell:** `show interfaces status` shows Ethernet8 still 9100 → write didn't take.

---

**6. id: `c_tiny_mtu` — "Absurdly small: 1280 on the fabric"**
- **type:** config · **risk:** medium · **enabled:** false  *(very small MTU may perturb the BGP session on vs; verify MSS keeps it up)*
- **inject:**
  - `leaf1: sudo config interface mtu Ethernet0 1280`
  - `leaf1: sudo config interface mtu Ethernet4 1280`
  - `leaf2: sudo config interface mtu Ethernet0 1280`
  - `leaf2: sudo config interface mtu Ethernet4 1280`
- **restore:**
  - `leaf1: sudo config interface mtu Ethernet0 9100`
  - `leaf1: sudo config interface mtu Ethernet4 9100`
  - `leaf2: sudo config interface mtu Ethernet0 9100`
  - `leaf2: sudo config interface mtu Ethernet4 9100`
  - `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- **expected effects (words):** The fabric is squeezed to 1280 (the IPv6 minimum, chosen as a memorably tiny value). Now even *modestly* large DF pings fail: `-s 1472` (1500-byte) dies too, not just jumbo — the ceiling dropped below standard Ethernet. BGP *should* survive via MSS (its segments shrink below 1280) `[VERIFY-ON-LAB: confirm session stays Established at 1280]`. Teaches that the threshold is a dial, and that TCP control traffic keeps ducking under it. Recovery: back to 9100.
- **plan-B variant:** set 1280 on **one** link only — combine the tiny-threshold effect with ECMP intermittency at a lower boundary.
- **injection-failed tell:** `ping -M do -s 1472` still succeeds after inject → the fabric isn't actually at 1280.

---

**7. id: `c_pmtud_blackhole` — "Kill the messenger: drop ICMP fragmentation-needed"**
- **type:** config · **risk:** high · **enabled:** false  *(depends on vs kernel generating frag-needed on forwarding; multi-rule; verify)*
- **inject:**
  - `leaf1: sudo config interface mtu Ethernet0 1500`
  - `leaf1: sudo config interface mtu Ethernet4 1500`
  - `leaf2: sudo config interface mtu Ethernet0 1500`
  - `leaf2: sudo config interface mtu Ethernet4 1500`
  - `leaf1: iptables -A FORWARD -p icmp --icmp-type fragmentation-needed -j DROP`  `[VERIFY-ON-LAB: whether vs generates/forwards frag-needed for routed oversize packets, and the correct chain]`
- **restore:**
  - `leaf1: iptables -D FORWARD -p icmp --icmp-type fragmentation-needed -j DROP`
  - `leaf1: sudo config interface mtu Ethernet0 9100`
  - `leaf1: sudo config interface mtu Ethernet4 9100`
  - `leaf2: sudo config interface mtu Ethernet0 9100`
  - `leaf2: sudo config interface mtu Ethernet4 9100`
  - `h1: ping -M do -s 8972 -c 3 10.0.2.10`
- **expected effects (words):** The real-world nightmare: the fabric is 1500 *and* the ICMP "fragmentation needed" messages that would tell senders to shrink are dropped. PMTUD is blinded — a DF sender gets no feedback, so large packets vanish with *no error returned* and no adaptation. This is why production MTU issues are so hated: the diagnostic signal itself is suppressed. Even the `-M do -s 8972` ping may hang with no ICMP reply rather than a clean failure. Recovery: remove the rule and restore MTUs.
- **plan-B variant:** drop the frag-needed ICMP inbound at **h1** instead — blinds only the source's PMTUD while the network still emits the messages.
- **injection-failed tell:** the large DF ping returns a prompt ICMP error (frag needed) instead of hanging → the messages are getting through; the drop rule didn't match.

---

## Observe

Parsers available: `interface_status, mac_table, lldp_neighbors, vlan_membership, bgp_neighbors, route_count, ping_loss, redis_keys, propagation_lag`. New parsers are marked.

### Happy-path fact map

| scenario (observe id) | parser(s) | key fields to extract | notes |
|---|---|---|---|
| o_mtu | interface_status | per port: name, mtu, oper, admin (both leafs) | assert every port mtu=9100, all up |
| o_bigpath | ping_loss (×3, tagged by size) | plain / DF-1472 / DF-8972: tx, rx, loss% | assert all three 0% loss at baseline |
| o_mtu_redis | redis_keys; interface_status | CONFIG_DB mtu, APPL_DB mtu, status MTU column for Ethernet0 | assert all three = 9100 (intent==appl==reality) |
| o_frag | ping_loss (DF vs no-DF) | DF-8972 loss%; plain-8972 loss% | at baseline both pass; contrast shows meaning of DF only once narrowed |
| o_ecmp_mtu | interface_status; route_count / route_entry | Ethernet0/4 mtu=9100; 10.0.2.0/24 two next-hops; DF-8972 loss% | assert both members equal + two next-hops + jumbo passes |
| o_detect | ping_loss (×3); interface_status (both leafs) | reachability, DF sweep, MTU column scan | practice the checklist; all green at baseline |
| o_bgp_mtu | bgp_neighbors; interface_status; ping_loss | session Established/uptime; MTU column; DF-8972 loss% | at baseline all agree; post-chaos they diverge |

### After-chaos fact map (same command set for all options)

| chaos id | facts that should CHANGE | facts that should NOT change | measure_recovery |
|---|---|---|---|
| c_mtu1500_one_side | leaf1 Ethernet0 mtu→1500; DF-8972 fails for hashed flows | oper/admin up; BGP Established; plain ping; DF-1472 | yes — DF-8972 passes after restore |
| c_mtu1500_one_link | Ethernet0 mtu→1500 both ends; DF-8972 intermittent | Ethernet4 mtu=9100; BGP; small pings | yes |
| c_mtu1500_all_links | all four fabric ports mtu→1500; DF-8972 fails deterministically | BGP Established; plain ping; DF-1472 | yes |
| c_host_mtu | h1 eth1 mtu→1500; DF-8972 fails **locally** (message too long) | switch MTUs 9100; BGP; plain ping | yes |
| c_access_port_mtu | leaf1 Ethernet8 mtu→1500; DF-8972 fails at first hop | inter-switch MTUs; BGP; plain ping | yes |
| c_tiny_mtu | fabric mtu→1280; even DF-1472 fails | BGP ideally Established (MSS) `[VERIFY-ON-LAB]`; plain ping | yes |
| c_pmtud_blackhole | fabric 1500 + frag-needed dropped; DF-8972 hangs (no ICMP) | BGP Established; plain ping | yes |

## Knowledge Card

*(Consumed by the runtime LLM for EXPLAIN moments. Meanings and expectations in words only.)*

### Objective

The learner can define MTU and path MTU, prove a jumbo path with a DF size sweep, locate the `mtu` field across CONFIG_DB/APPL_DB/kernel, explain fragmentation and the DF bit and PMTUD (and how dropping ICMP frag-needed creates a blackhole), reason about ECMP×MTU intermittency, apply a systematic "up but broken" detection checklist, and explain why a TCP control session (BGP) survives an MTU fault (MSS) that kills bulk data.

### In-scope subtopics

MTU/jumbo/path-MTU; DF-ping size sweep and payload+28 arithmetic; PORT.mtu through the pipeline to the netdev; fragmentation, the DF bit, PMTUD, ICMP fragmentation-needed; ECMP×MTU per-flow intermittency; the detection checklist; TCP MSS and why BGP survives; the seven chaos options' signatures and recovery.

### Key concepts (one sentence each)

1. MTU is the largest packet a link will carry, it is per-link, and a path is only as wide as its narrowest hop.
2. Oversized packets are either fragmented (DF unset) or dropped (DF set), so MTU faults can be completely silent to the sender.
3. A DF size sweep is the only reliable test of true path MTU: small passes and large fails is the signature of an MTU fault.
4. MTU is a plain field (`mtu`) that flows through CONFIG_DB→APPL_DB→ASIC and onto the Linux netdev, so a mismatch is one number out of agreement across a link.
5. PMTUD lets senders learn the right size from returned ICMP "fragmentation needed" messages — and fails silently if those messages are dropped.
6. ECMP plus a per-member MTU mismatch produces intermittent failure, because each flow's fate depends on which link it was hashed onto.
7. Status, routes, and sessions all stay healthy under an MTU fault, so only a DF probe reveals it and only the MTU column locates it.
8. TCP negotiates an MSS from local MTU and self-limits its segment size, so small-packet control sessions like BGP survive a fault that blackholes big data.
9. A source-local MTU error is loud (the sender refuses to transmit) while an in-network MTU drop is silent (the packet vanishes downstream).
10. "The config pipeline betrayed" is literal: SONiC faithfully programs the wrong MTU you intended, and the fault exists precisely because the machinery worked.

### Command field meanings

- **`show interfaces status`** — the **MTU** column is the star here (bytes, 9100 baseline); also Oper/Admin (stay up under an MTU fault), Speed, Alias.
- **`ping -c N <ip>`** — plain reachability (tiny packets). **`-s <payload>`** sets ICMP payload (packet = payload+28). **`-M do`** sets the DF bit (forbid fragmentation → oversize is dropped, not split). Read loss% and any "message too long"/"frag needed" text.
- **`sonic-db-cli CONFIG_DB hget "PORT|<port>" mtu`** — the configured MTU intent. **`APPL_DB hget "PORT_TABLE:<port>" mtu`** — the translated value. Together with the status column they show intent/appl/reality agreement.
- **`config interface mtu <port> <value>`** — write a new MTU (9100 baseline; 1500/1280 for faults). **`ip link set eth1 mtu <value>`** (host) — the host netdev MTU; **`ip link show eth1`** reads it.
- **`show ip route 10.0.2.0/24`** — reused for ECMP: two next-hops = both members in play (an MTU fault on one is per-flow).
- **`show bgp summary`** — reused to prove the control plane stays Established under an MTU fault (MSS).
- **`iptables … --icmp-type fragmentation-needed -j DROP`** — suppresses PMTUD's feedback message to create a blackhole (chaos 7).

### Healthy-state expectations (baseline, in words)

- Every port on both leafs: MTU 9100, Oper up, Admin up.
- h1→h3: plain ping 0% loss; `ping -M do -s 1472` 0% loss; `ping -M do -s 8972` 0% loss (the jumbo fabric proven end to end).
- Ethernet0 `mtu` = 9100 in CONFIG_DB, in APPL_DB PORT_TABLE, and in the status column — intent, application, reality agree.
- `show ip route 10.0.2.0/24`: two next-hops (both ECMP members at 9100).
- `show bgp summary`: both sessions Established.

### Expected chaos effects per option (signatures)

- **c_mtu1500_one_side:** Ethernet0→1500; DF-8972 fails for flows hashed to Ethernet0 (direction/hash-dependent `[VERIFY-ON-LAB]`); everything else green; MTU column is the tell.
- **c_mtu1500_one_link:** one ECMP path 1500 both ends; DF-8972 intermittent by hash; DF-1472 and BGP fine.
- **c_mtu1500_all_links:** whole fabric 1500; DF-8972 fails deterministically; DF-1472/plain/BGP fine — cleanest demo.
- **c_host_mtu:** h1 eth1→1500; DF-8972 fails *locally* ("message too long") before egress — loud, source-side.
- **c_access_port_mtu:** Ethernet8→1500; DF-8972 fails at the first hop; fabric untouched `[VERIFY-ON-LAB: enforcement]`.
- **c_tiny_mtu:** fabric→1280; even DF-1472 fails; BGP should stay up via MSS `[VERIFY-ON-LAB]`.
- **c_pmtud_blackhole:** fabric 1500 + frag-needed dropped; DF-8972 hangs with no ICMP feedback — silent blackhole; BGP up.

### Misconceptions (wrong → why → correct)

1. **"If ping works, MTU is fine."** → Plain ping uses tiny packets that fit anything; only large DF packets test the ceiling. → Prove size with `ping -M do -s 8972`, not plain ping.
2. **"An MTU-too-big packet just gets split and delivered."** → Only if DF is unset; with DF it is dropped. → The DF bit decides fragment-vs-drop; DF traffic (and PMTUD) meets the ceiling hard.
3. **"MTU is a property of the path."** → It is per-link; the path MTU is the minimum across hops. → One narrow hop bottlenecks an otherwise-jumbo path.
4. **"The link is up, so size can't be the problem."** → Up/oper says nothing about size; MTU faults leave status perfect. → Status lies about size — use a DF probe.
5. **"If BGP is Established, the data path must be fine."** → BGP's small TCP segments (MSS) fit under the fault while bulk data doesn't. → Control-plane health ≠ data-plane health, with MTU as the wedge.
6. **"Intermittent failure means flaky hardware."** → ECMP hashing plus a per-member MTU mismatch makes success depend on which link a flow took. → Suspect ECMP×MTU and probe repeatedly across flows.
7. **"PMTUD always saves you."** → It depends on ICMP frag-needed getting back; drop it and large packets blackhole silently. → PMTUD is only as reliable as the ICMP path; filtered ICMP breaks it.
8. **"Making every link 1500 fixes a mismatch."** → Consistent 1500 works for ≤1500 traffic but forfeits jumbo; the fault was *disagreement*, not size. → Match MTUs across the path; choose one value everywhere.
9. **"A failing big ping means the destination is down."** → A source-local 'message too long' means *your* MTU is too small, not the peer's fault. → Read the error: local refusal vs in-network silence point to different hops.

### Out-of-scope (redirect if asked)

MSS clamping configuration, QoS/buffer tuning, IPv6 PMTUD specifics, TCP congestion control, tunnel (GRE/VXLAN) overhead math, jumbo throughput benchmarking, L2 forwarding internals (Lesson 1), pipeline internals beyond locating the mtu field (Lesson 2), BGP internals beyond the MSS point (Lesson 3).

### Polite redirect line

"Good question, but it's outside this lesson's focus — MTU, fragmentation, and the 'up but broken' failure class on this fabric. I'd recommend reading it up separately (RFC 1191 on PMTUD, or a jumbo-frame primer). Let's get back to the size sweep on leaf1 — pick a suggested question or run another on-topic command."

## Glossary

- **MTU (maximum transmission unit)** — the largest IP packet a link will carry, in bytes.
- **jumbo frame** — a frame/packet larger than the classic 1500-byte Ethernet MTU (9100 here).
- **path MTU** — the smallest MTU along a path; the true size ceiling end to end.
- **fragmentation** — splitting an oversized IP packet into link-sized pieces reassembled at the destination.
- **DF bit (Don't-Fragment)** — the IP header bit forbidding fragmentation, forcing oversize packets to be dropped.
- **PMTUD (Path MTU Discovery)** — senders setting DF and learning the real path MTU from returned ICMP messages.
- **ICMP fragmentation-needed** — the ICMP message telling a DF sender its packet was too big and giving the correct size.
- **ICMP** — the control/error-message companion to IP (echo for ping, fragmentation-needed for PMTUD).
- **payload (ICMP)** — the data bytes in a ping; total packet = payload + 28 (8 ICMP + 20 IP).
- **size sweep** — probing with growing DF packet sizes to find the true path MTU.
- **netdev** — the Linux network device mirroring a SONiC port, carrying its own MTU.
- **PORT.mtu** — the `mtu` field on a port object as it flows through the config pipeline.
- **MSS (maximum segment size)** — the largest TCP segment a peer will accept, derived from its MTU.
- **up but broken** — a failure class where status/route/session look healthy while (large) traffic fails.
- **blackhole** — silently dropping packets with no error returned to the sender.
- **ECMP member** — one of several equal-cost links sharing a destination's traffic (from Lesson 3).
- **flow hashing** — placing each flow on one ECMP member by hashing packet fields (from Lesson 3).

## QnA

### Scope keywords (gate input; generous synonyms)

```
mtu, maximum transmission unit, jumbo, jumbo frame, 9100, 1500, 1280, frame
size, packet size, payload, overhead, path mtu, pmtu, path mtu discovery,
pmtud, fragment, fragmentation, defragment, reassembly, df, df bit,
dont fragment, do not fragment, drop, silent drop, blackhole, icmp,
fragmentation needed, frag needed, ping, ping -s, ping -m do, size sweep,
mss, maximum segment size, tcp, clamp, up but broken, detection, troubleshoot,
ecmp, hashing, intermittent, some flows, netdev, ip link, port mtu,
config interface mtu, config_db mtu, appl_db mtu, mtu column, ethernet0, ethernet4,
ethernet8, leaf1, leaf2, h1, h3, 8972, 1472
```

### Question bank (8 per qna step; TOP 3 marked ★; ordered easy → deep)

#### q_mtu
1. ★ What is MTU, and why is it 9100 here instead of 1500?
2. ★ Why is a path only as wide as its narrowest link?
3. ★ What are the two things that can happen to a too-big packet?
4. Where do I read a port's MTU on SONiC?
5. What is a jumbo frame, and why do data centers use them?
6. Is MTU about speed, reachability, or size — and why does that distinction matter?
7. Could every status light be green while an MTU fault exists? How?
8. What's the relationship between a frame and the MTU number?

#### q_bigpath
1. ★ Why does a plain ping prove nothing about MTU?
2. ★ What do the `-s` and `-M do` options do, and why both?
3. ★ Why is `-s 8972` the jumbo test but `-s 1472` the 1500 test?
4. What exactly does the DF bit forbid?
5. What does it mean if `-s 1472` passes but `-s 8972` fails?
6. Where does the +28 in the packet-size math come from?
7. Why re-run the sweep during restore?
8. Does a passing sweep guarantee every flow works? (Hint: ECMP.)

#### q_mtu_redis
1. ★ Which three places store the MTU value, and how should they relate at baseline?
2. ★ What does "the config pipeline betrayed" actually mean for MTU?
3. ★ If CONFIG_DB says 1500 but the status column says 9100, what happened?
4. Which command writes the MTU intent, and which process programs it?
5. How is MTU on SONiC the same machinery as the Vlan30 demo in Lesson 2?
6. What is a netdev, and why does it also carry an MTU?
7. Where would you look first if a port's MTU seemed stuck?
8. Why is reading three layers better than trusting one command?

#### q_frag
1. ★ What decides whether an oversized packet is fragmented or dropped?
2. ★ What is PMTUD, and what message makes it work?
3. ★ Why can the same big ping fail with `-M do` but succeed without it?
4. What are the costs of fragmentation?
5. What does the ICMP "fragmentation needed" message tell the sender?
6. Why would an operator ever *want* packets dropped instead of fragmented?
7. What breaks if a firewall eats the frag-needed messages?
8. How does IPv6 change the fragmentation story (at a high level)?

#### q_ecmp_mtu
1. ★ Why would only *some* flows fail when one ECMP link is 1500?
2. ★ How does per-flow hashing turn an MTU fault into intermittency?
3. ★ What's the healthy precondition this scenario checks before breaking anything?
4. Why does a single repeated ping sometimes pass and sometimes fail?
5. How would you make a failing flow reliably hit the narrow link?
6. Why is this the hardest MTU fault to diagnose?
7. What two earlier lessons combine to produce this effect?
8. How does the route table view help confirm both members are in play?

#### q_detect
1. ★ Walk me through the checklist for an "up but broken" MTU fault.
2. ★ Why do status, routes, and BGP all fail to reveal an MTU problem?
3. ★ Which single command finally *locates* the fault once a DF probe confirms it?
4. How do you rule out a plain reachability problem first?
5. How do you tell an MTU fault from a route or session problem?
6. What does turning DF off prove about the fault?
7. How would you detect the intermittent (ECMP) variant?
8. Why is "only a DF probe tells the truth" the core of this method?

#### q_bgp_mtu
1. ★ Why does BGP stay Established when a 1500 MTU is blackholing h1→h3 jumbo data?
2. ★ What is MSS, and how does it keep TCP under the MTU ceiling?
3. ★ How is this the ultimate example of control plane vs data plane?
4. Why are BGP's packets small in the first place?
5. Would a bulk TCP file transfer behave like BGP or like the jumbo ping? Why?
6. What is MSS clamping, at a high level, and why might someone use it?
7. Which protocols are especially fragile to MTU faults, and why?
8. If BGP flapped under an MTU fault, what would that tell you?

#### q_impact
1. ★ What changed after the injection — which ping sizes now fail?
2. ★ Why does the interface stay up while large packets are dropped?
3. ★ Why does BGP stay Established through this failure?

## Verify-On-Lab

1. **Baseline jumbo path:** confirm `ping -M do -s 8972` h1→h3 succeeds at 9100 everywhere; capture typical rtt.
2. **c_mtu1500_one_side direction:** determine which direction/end enforces the drop (leaf1 ingress vs egress) and how reliably a single ping tuple lands on the narrowed Ethernet0 (hashing).
3. **ECMP probe variability:** for c_mtu1500_one_link, measure how often repeated DF-8972 pings pass vs fail (hash distribution); decide how many probes the parser needs.
4. **MTU pipeline key formats:** confirm APPL_DB `PORT_TABLE:Ethernet0` carries `mtu`; confirm the netdev MTU tracks the config.
5. **c_host_mtu signature:** confirm h1's `ping -M do -s 8972` fails *locally* with "message too long" (not an in-network timeout).
6. **c_access_port_mtu:** confirm whether vs enforces MTU on the access port netdev and in which direction; keep disabled until verified.
7. **c_tiny_mtu + BGP:** confirm the session stays Established at 1280 (MSS shrinks segments); confirm DF-1472 now fails.
8. **c_pmtud_blackhole:** confirm the vs kernel generates/forwards ICMP fragmentation-needed for routed oversize packets and the correct iptables chain to suppress it; confirm the DF-8972 ping hangs (no ICMP) with the rule in place.
9. **Restore completeness:** confirm every option returns all touched ports to 9100 and removes all iptables rules, and that the jumbo sweep passes again.

## Machine Summary

```json
{
  "id": "mtu_mismatch",
  "steps": [
    {"id": "t_mtu", "kind": "teach", "core": true},
    {"id": "o_mtu", "kind": "observe", "core": true},
    {"id": "q_mtu", "kind": "qna", "core": true},
    {"id": "t_bigpath", "kind": "teach", "core": true},
    {"id": "o_bigpath", "kind": "observe", "core": true},
    {"id": "q_bigpath", "kind": "qna", "core": true},
    {"id": "t_mtu_redis", "kind": "teach", "core": true},
    {"id": "o_mtu_redis", "kind": "observe", "core": true},
    {"id": "q_mtu_redis", "kind": "qna", "core": true},
    {"id": "t_frag", "kind": "teach", "core": false},
    {"id": "o_frag", "kind": "observe", "core": false},
    {"id": "q_frag", "kind": "qna", "core": false},
    {"id": "t_ecmp_mtu", "kind": "teach", "core": false},
    {"id": "o_ecmp_mtu", "kind": "observe", "core": false},
    {"id": "q_ecmp_mtu", "kind": "qna", "core": false},
    {"id": "t_detect", "kind": "teach", "core": false},
    {"id": "o_detect", "kind": "observe", "core": false},
    {"id": "q_detect", "kind": "qna", "core": false},
    {"id": "t_bgp_mtu", "kind": "teach", "core": false},
    {"id": "o_bgp_mtu", "kind": "observe", "core": false},
    {"id": "q_bgp_mtu", "kind": "qna", "core": false},
    {"id": "chaos", "kind": "chaos_select", "core": true},
    {"id": "q_impact", "kind": "qna", "core": true},
    {"id": "restore", "kind": "restore", "core": true}
  ],
  "commands": {
    "o_mtu": ["leaf1: show interfaces status", "leaf2: show interfaces status"],
    "o_bigpath": ["h1: ping -c 3 10.0.2.10", "h1: ping -M do -s 1472 -c 3 10.0.2.10", "h1: ping -M do -s 8972 -c 3 10.0.2.10"],
    "o_mtu_redis": ["leaf1: sonic-db-cli CONFIG_DB hget \"PORT|Ethernet0\" mtu", "leaf1: sonic-db-cli APPL_DB hget \"PORT_TABLE:Ethernet0\" mtu", "leaf1: show interfaces status"],
    "o_frag": ["h1: ping -M do -s 8972 -c 3 10.0.2.10", "h1: ping -s 8972 -c 3 10.0.2.10"],
    "o_ecmp_mtu": ["leaf1: show interfaces status", "leaf1: show ip route 10.0.2.0/24", "h1: ping -M do -s 8972 -c 3 10.0.2.10"],
    "o_detect": ["h1: ping -c 3 10.0.2.10", "h1: ping -M do -s 1472 -c 3 10.0.2.10", "h1: ping -M do -s 8972 -c 3 10.0.2.10", "leaf1: show interfaces status", "leaf2: show interfaces status"],
    "o_bgp_mtu": ["leaf1: show bgp summary", "leaf1: show interfaces status", "h1: ping -M do -s 8972 -c 3 10.0.2.10"],
    "after_chaos": ["leaf1: show interfaces status", "leaf2: show interfaces status", "leaf1: show ip route 10.0.2.0/24", "leaf1: show bgp summary", "h1: ping -c 5 10.0.2.10", "h1: ping -M do -s 1472 -c 5 10.0.2.10", "h1: ping -M do -s 8972 -c 5 10.0.2.10", "h1: ping -s 8972 -c 5 10.0.2.10"]
  },
  "chaos_ids": ["c_mtu1500_one_side", "c_mtu1500_one_link", "c_mtu1500_all_links", "c_host_mtu", "c_access_port_mtu", "c_tiny_mtu", "c_pmtud_blackhole"],
  "enabled_chaos": ["c_mtu1500_one_side", "c_mtu1500_one_link", "c_mtu1500_all_links", "c_host_mtu"],
  "keywords": ["mtu", "maximum transmission unit", "jumbo", "jumbo frame", "9100", "1500", "1280", "frame size", "packet size", "payload", "overhead", "path mtu", "pmtu", "path mtu discovery", "pmtud", "fragment", "fragmentation", "reassembly", "df", "df bit", "dont fragment", "drop", "silent drop", "blackhole", "icmp", "fragmentation needed", "frag needed", "ping", "ping -s", "ping -m do", "size sweep", "mss", "maximum segment size", "tcp", "clamp", "up but broken", "detection", "troubleshoot", "ecmp", "hashing", "intermittent", "netdev", "ip link", "port mtu", "config interface mtu", "mtu column", "8972", "1472"],
  "glossary_terms": ["MTU (maximum transmission unit)", "jumbo frame", "path MTU", "fragmentation", "DF bit (Don't-Fragment)", "PMTUD (Path MTU Discovery)", "ICMP fragmentation-needed", "ICMP", "payload (ICMP)", "size sweep", "netdev", "PORT.mtu", "MSS (maximum segment size)", "up but broken", "blackhole", "ECMP member", "flow hashing"]
}
```
