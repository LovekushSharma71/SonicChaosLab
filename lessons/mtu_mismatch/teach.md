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
