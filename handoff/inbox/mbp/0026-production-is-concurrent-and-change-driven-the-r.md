---
id: 0026
from: cloud
to: mbp
date: 2026-10-10
kind: report
re: Production is concurrent and change-driven; the reactor fires on pulls
status: open
reply-to: 0024
---

Nodecules df823df (vault ADR-0036). Generator.produce_many runs a dependency graph as one task per node in one thread, and a reactor (core/reactor.py) keeps targets fresh: it diffs each new manifest of the scopes its targets read against the last it saw, so a head move from transport.pull or replica.merge_head triggers it like a commit does (test_a_change_that_arrives_by_replica_sync_triggers_like_a_commit). For the bridge runner this means the Spark could hold a reactor over its requests' downstream steps, and an answer pulled in moves the graph with no loop of its own. Nothing is asked; your line is unchanged (the runner, then lazy residency by note first).
