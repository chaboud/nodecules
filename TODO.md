# Nodecules — engineering TODO

Tracks non-blocking engineering work items discovered during development but
not scheduled into a specific branch. One-liners preferred; expand inline if
context is genuinely lost otherwise.

## Open

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

### Quarantine chat-context subsystem out of `core/`
**Closed:** 2026-04-20 (PR-n1.5). `plugins/builtin_nodes.py` no longer pulls the chat stack; service-backed nodes register from FastAPI startup via `plugins/service_nodes.py`. `models/database.py` lazy-instantiates engine + session factory. `core/` now imports clean in a pydantic-only venv.
