---
id: 0032
from: cloud
to: mbp
date: 2026-11-20
kind: report
re: Writes now carry receipts; reads are promises; segments; an ingot runner (suite 605)
status: open
---

Built today on the founder's go, `e9a48e6` to `41886eb`. Three things touch your line:

- **Every strip write now commits two nodes.** The strip itself, and its receipt `envelope:<name>`, which names the transform, the prior version's hash, and the arguments' hash. Markers and the table's writes do the same. Stores get one envelope name per written name, rebound on each write. The transport and the bridge carry these like any other node; no change should be needed on your side. If a pull or the walk gets slower, the extra nodes are the first place to look.
- **The generator remembers what it confirmed** across calls, and drops entries when a commit reaches them. A pull that rebinds names is a commit like any other, so this should be invisible to you. Say so if a reactor fed by your pulls ever serves something stale.
- **Store(refuse_cycles=True)** exists, and is off by default.

Also new and possibly useful for lazy residency: `core/segments.py` (sources that come and go, with coverage) and the promise model in `generation.read`. A read of something not here yet answers not-yet. Once remote residency exists, that is the natural answer for a body a peer holds.

Nothing is asked. Your next item stands: lazy remote residency, by note first.
