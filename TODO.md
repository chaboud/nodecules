# Nodecules — engineering TODO

Tracks non-blocking engineering work items discovered during development but
not scheduled into a specific branch. One-liners preferred; expand inline if
context is genuinely lost otherwise.

## Open

### Quarantine chat-context subsystem out of `core/`
**Category:** architecture, layering hygiene
**Opened:** 2026-04-20
**Context:** `backend/nodecules/core/smart_context.py` and
`content_addressable_context.py` import `redis` at module top level and
instantiate a postgres engine at import time. That makes every importer of
`core/` — including static-graph unit tests — require a redis install and a
live postgres, directly violating the new `CLAUDE.md` invariant #4 ("core
library works without Postgres/Redis"). For the stenota pipeline we're
building, redis adds zero value: JSONL-on-filesystem is already sub-ms for our
read pattern, and nothing hot-reads chat histories. Move the chat-context
caching under `services/` or `api/`, and make `builtin_nodes` not pull it in at
static import time (lazy-import or registry indirection). This lands before
PR-n2 (node output cache) so the new cache layer can live cleanly in `core/`
without inheriting the same sin.

### Graph-engine overhead vs. direct pipeline baseline
**Category:** performance, architecture
**Opened:** 2026-04-20
**Context:** Stenota's batch path must hit "fast on a fanless MacBook Air" for
multi-hour meetings. We need to characterize what the node-graph engine costs
us — scheduler overhead per tick, per-node dispatch cost, JSONL round-tripping,
cache-key hashing — compared to a hand-rolled pipeline that calls the same ASR
/ diar / summarizer code directly without the graph abstraction. Target: a
bench harness that runs the same workload through both paths and reports
wall-clock, sustained throughput, peak RSS, and thermal headroom proxy. Decide
from the numbers whether specific hot nodes need a fast-path that bypasses the
scheduler, or whether the abstraction tax is fine. Do not guess; measure.

## Done

(none yet)
