# boxing-bench

Puts numbers under the founder's principle of 2026-10-10, recorded in the
vault's ADR-0038: all dependence is acyclic, enforced by only being able
to reach what exists or what a graph can produce; every change of state
is a transform from one version to the next, primitives included; and it
is made fast by *boxing*. Boxing here means what it means in C#: a plain
value runs as a plain value, and is wrapped as an object only when
something needs to treat it as one. For a write, the plain value is a
direct function call, and the object is the full production graph with
its receipt.

```bash
cd spikes/boxing-bench
python3 bench.py      # about 12 seconds
```

Measured 2026-10-10 in the cloud container, nodecules `0c4b558`, no
changes to core:

| | question | finding |
|---|---|---|
| B1 | does a commit refuse an edge to a missing name, or a cycle? | **no, both are accepted.** The cycle surfaces only when something walks it (`composed_hash` raises `Cycle`). Acyclicity is enforced at production time, not at commit time. |
| B2 | an append as a plain call against the same append as a full production | plain: about 200 us per append, flat from 10 to 300 elements. Full production: 2.0 ms at 10, 9.6 ms at 100, 34 ms at 300. Ratio 10x, 49x, 162x. |
| B2 | where the production time goes | 150 appends made 22,800 single-node productions, exactly n(n+1)+n: producing version k revisits every earlier version, its element node, and the recipe. Writing each version as a production is quadratic **as built**, because a produce re-confirms its whole closure on every call. |

What B2 does and does not say. It says that making every write a full
production today would cost two orders of magnitude at a few hundred
elements, and the growth is the generator re-confirming a closure it
confirmed one call earlier, not anything inherent in a transform. A
memo of what was confirmed at a manifest would remove the growth; the
constant (about 10x at n=10) would remain. It does not measure the
proposed middle path: a plain call that also records a compact receipt
(the transform's handle and its inputs' hashes). That costs at least one
extra hash per write; **not measured**.

Honest scope: one process, in memory, timings from one container run
with no repeat; the ratios are the finding, the microseconds are not.
