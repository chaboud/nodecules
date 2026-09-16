# nodecules: why, what, how

This is the plain explanation of what nodecules is, why it is built the way
it is, and how to work on it. It is written for someone who has not been in
the room. The spec is `REFERENCE-MODEL.md`; the decisions are in the
ChaboudPrivateWiki vault under `LLM_Wiki/decisions/`; the constraints for
working here are in `CLAUDE.md`. This document explains the reasoning those
three assume.

Status words are used carefully throughout. "Built" means code with tests
on the branch. "Designed" means a written decision with no code. "Not
started" means exactly that. `STATUS.md` is the running account of how much
of what follows exists, property by property.

## Why

### The problem

We want to build several different systems: software and LLM systems that
can change their own behavior and move their own work between machines; a
meeting recorder that turns audio into structured claims; an always-on
observer that watches a room and speaks; a shared workspace where several
people and several models act at once; a way to spread compute and data
across a laptop, a home server, and a cloud; a market where anyone can rent
inference on open-weights models and prove what ran; and ordinary hosted
inference over models a provider keeps private.

Each of those, built on its own, ends up rebuilding the same five things:
a cache, a way to sync state between machines, a scheduler, a way to show
results to people, and some notion of trust. They rebuild them badly,
because each is a hard problem and none is the product. The systems then
cannot share work, cannot share state, and cannot be checked against each
other.

### Why one substrate

The observation that makes one substrate possible is that all five of those
things become simple if every piece of data and every computation has a
content hash. A cache is a lookup by hash. Sync is an exchange of hashes the
other side lacks. Reproducibility is comparing hashes. Audit is following
hashes back through history. Trust is a receipt that names hashes. So the
substrate is, at bottom, a store of content-addressed nodes and one
operation that produces a node from the nodes it references.

That is not a new idea. Git, Nix, and Unison each use part of it. What is
different here is the scope of what is treated as a node: data, code,
recipes, plans, receipts, clocks, scenes, and the people looking at them.
Everything the systems above need to share, cache, sync, show, or trust is
the same kind of thing.

### Why not an existing tool

A workflow engine gives a graph of steps but no identity for what ran. A
database gives state but no computation. A CRDT library gives convergence
but no reproducibility. A build system gives caching but nothing live. A
game engine gives scenes but no distribution. Each of the seven systems
above needs several of these at once. Composing five tools that do not
share an identity model is the situation the substrate exists to end.

### The bet, in one paragraph

Everything is a node in an acyclic graph over a content-addressed store.
Identity has two layers: what was asked for, and what actually ran. When
two things might be the same, assume they are different, because the wrong
sameness is silent and unrecoverable while a recompute is only slow. Time
is explicit and comes with a clock's identity attached. Several writers are
a policy, not an assumption. What is shown to a person is a pure function
of the state and the instant. Reputation and labels sit above all of this
and never change the identity of what they label.

## What

### One node

A node is a name, a kind, a scope, data, and edges. Its content hash covers
the kind, the data, and the edges. The name and the scope are addresses,
not identity, so two names bound to the same content share one body, and a
renamed node is the same node.

A scope is a group of nodes with one history. A manifest is the map from
names to content hashes for one scope at one version. Manifests are nodes
too. Holding one is a snapshot that never changes underneath you. A commit
builds a new manifest and swaps the scope's pointer to it. The manifest's
hash covers its parent, so history is a hash chain, and a merge manifest
has two parents, so history is a DAG. Built.

Bodies can be dropped from memory without changing anything about
identity. A pruned node still has its kind and edges, so what it depended
on is still known, and it can be rebuilt from its envelope or fetched from
a replica that still has it. Those two are the same operation to the store.
Built; the retention policy that decides when to prune is designed.

### One operation

Producing a node means running its recipe over the nodes its edges point
at. The recipe is itself an edge to a template node; per-instance
parameters are an edge to a params node. So everything a production
depends on is a node in the graph, and "why did this recompute" is
answerable from the store.

The same operation covers a first production, a recompute because an input
changed, and a rebuild after pruning. Only the trigger differs. Each
production writes an envelope: which realization was declared and which
was used, the inputs by identity, the cache key, the outcome, and whether
the result's reproducibility was measured or only claimed. A realization
that claims to be deterministic and then produces a different hash is
flagged. Built. Recomputation driven by change notification rather than by
a request is designed.

### Two layers of identity, and the satisfies judgment

A description says what a piece of compute must consume and produce, a
metric, a tolerance, and a reference implementation. A realization is a
concrete implementation. Deciding whether a realization satisfies a
description takes two judgments. The first is structural: do the kinds line
up. The second is empirical: run it against the reference on sampled inputs
and measure. The bench that established this found that the structurally
valid impostor was also the cheapest candidate, so a matcher without the
second judgment prefers wrong answers.

The empirical judgment certifies only the inputs it probed. So the receipt
says which inputs, from where. Probes drawn from the live workload detect a
realization that cheats at the rate it cheats. A consumer can run the same
judgment itself with its own known-answer inputs; that is an audition.
Built as code and as a measured bench; not yet run against real model
weights.

### Placement

Executors advertise where they are, what they can run, what it costs, and
what is already loaded. Jobs name descriptions and can be pinned to where
their data is. A policy says what may not leave the device, what crossing a
boundary costs, and how to weigh latency against energy against money. The
result is a plan, which is content-addressed, can be re-verified, and
answers where the data went. Built. Running a plan by handing each node to
its assigned executor is designed; today a plan only binds realizations
within one process.

### Time

A time is an integer count of ticks on a named timeline whose timebase is
an exact fraction of a second. Milliseconds, 100-nanosecond ticks, a 48 kHz
sample clock, and 29.97 frames per second are all timebases. A timeline
also records what tick zero means, what kind of clock drives it, and which
device owns it. Two timelines relate only through a map of measured anchor
pairs with its own error bound. Skew and drift live in the anchors.
Conversion is exact arithmetic that reports whether it had to round. Built.
The recipe that observes two clocks and produces anchors is not started.

### Several writers

Within one process, a commit that touches a name someone else also touched
follows the scope's policy: refuse and name both versions so the committer
resolves it; let the later one through and record what it overrode; or fold
the two through a rule registered for the node's kind. Across replicas,
every replica commits locally, and when two replicas sync their heads merge
through the nearest common ancestor with a deterministic rule that never
raises. Two replicas that sync in either order end with identical heads,
hash for hash. Built.

Which line of history counts as canonical for a scope is a separate
question with several answers: one authority, a supervisor who may
intervene, convergence with no canonical head, proposals accepted by a
gate, a quorum, a time box, and a few more. Designed as a taxonomy; not a
field yet, because nothing consumes it until the first shared table needs
it.

### Scenes and the table

An element to be shown carries the instant it should be fully presented and
the instant it stops being true, both on the table's timeline. The frame at
an instant is a pure function of the manifest and the instant, in a fixed
order, with a hash. Same state, same instant, same frame. A surface has a
clock, a refresh rate, two latencies, and a map to the table's timeline,
and from those its simultaneity bound is computed rather than hoped for.
Producers publish ahead by the worst bound. Lateness is a declared policy.
Elements may carry transition hints, which a renderer may honor, shorten,
or ignore. Frames carry data and choice structure, never pixels, so a
screen reader is as legitimate a presenter as a display. Built and tested
with two simulated surfaces.

On the table, people and agents are participants. Attention is an element
with an expiry, so who is looking at what is in the store and a participant
who goes quiet fades out. Following is optional and never moves anyone
else. Concurrent edits of one element merge field by field. Built, with a
running demo. Decisions and their presentations, the responsible adult who
keeps the table within bounds, and any presenter beyond text are next.

### Labels above the kernel

Claims, receipts, vouches, plans, and observations point at functional
nodes by hash. Nothing functional points back at them. Attaching a label
therefore never changes the identity of the thing labelled, and any
reputation mechanic is a derived layer folding over facts the substrate
records. There is no trust score in the node model and there will not be
one. Built as a guard; reputation mechanics themselves are designed.

## How

### Why it is structured this way

Each structural choice below was made against real alternatives. The
decision records in the vault carry the alternatives; here is the reason
for each choice in a sentence or two.

| Choice | Reason |
|---|---|
| Content addressing for everything | One mechanism gives caching, deduplication, sync, audit, and reproducibility. Five problems, one primitive. |
| Names separate from hashes | Graphs can change while content stays immutable. A declaration keeps its identity across productions. A version pinned into a declaration would force every reader to rewrite when the target advanced; that rigidity spreads through the graph, and we removed it the day it was tried. |
| Declarations symbolic, resolution at production time, results in the envelope | The intent and the realization are different things and must have different hashes. The cache key is honest because it is built from what was actually read. |
| One manifest per scope, swapped atomically | Reads never wait. Writes are atomic. History is a hash chain, which gives git-like behavior and replicas without a second design. |
| Policies as fields on the manifest | History shows which rules governed which version. Changing a rule is a commit, not a config file. |
| Assume different unless proven the same | A false match is silent and cannot be recovered from later. A false mismatch costs one recompute. |
| Time as named timelines with maps | Clock skew becomes data with provenance. "At the same time" becomes a bound that can be computed and measured instead of a wish. |
| Frames as pure functions | What a person saw when they decided can be reproduced exactly. That is the audit a choice architecture needs, and it makes presenters swappable. |
| Labels never bond into functional nodes | The thing labelled keeps its identity. Trust stays a layer that can be replaced without touching the kernel. |
| No database, no clock, and no provider names in the core | The core runs anywhere, the whole suite runs in seconds, and consumers own the leaves. A realization is where a provider or a model lives; the graph only names it. |

### How the work proceeds

A primitive is built only when a consumer needs it, and it must serve at
least two of the seven systems listed under Why, or it is a feature of one
consumer and belongs there. The meeting recorder, stenota, grounds most of
the work today because its graph is real and small. When a claim can be
measured, a bench under `spikes/` measures it before a decision is written,
and the decision cites the number. Decisions live in the vault as numbered
records with the alternatives that were rejected. Status is stated with the
four words above and nothing softer.

Several instances work in this repository from different machines. The
branch map in `CLAUDE.md` says which branch is live. The demos under
`demos/` are the milestone: a system counts as usable when a demo runs on
it, and each demo says what it does not show.

### How to read the code

Everything new lives under `backend/nodecules/core/` and runs with pydantic
and pytest alone. The legacy engine on the `main` branch is a separate
thing under the same name; nothing here depends on it.

| Module | What it is |
|---|---|
| `pmap.py` | A persistent map whose shape depends only on its keys, with a hash at every node. The store stands on it. |
| `store.py` | Nodes, manifests, scopes, transactions, envelopes, residency, the composed hash, the per-name write policy. |
| `generation.py` | Produce a node from its edges. Cache keys, envelopes as receipts, the four outcomes, dispatch of a plan. |
| `descriptions.py`, `assay_metrics.py` | Descriptions, realizations, the two-part satisfies judgment, hallmarks. |
| `placement.py` | Executors, jobs, policies, plans, the cost model, observations. |
| `timeline.py` | Timebases, timelines, maps between clocks, conversion. |
| `replica.py` | Manifests as a DAG, the deterministic merge, sync between stores. |
| `scene.py` | Elements with presentation time and expiry, the frame, surfaces, the simultaneity bound, lateness, transitions. |
| `table.py` | Participants, attention, following, field-wise merge of concurrent edits. |
| `strip_access.py`, `strip_resolve.py`, `strip_nodes.py` | Typed access patterns over strips, their resolution, and a strip as a single node. |
| `llm_realization.py` | Any tool-aware model provider as a realization. |

### How to build on it

A consumer wraps its functions as realizations, each with a name and an
honest statement of whether it is deterministic. It declares its graph as
nodes with edges to inputs, a recipe template, and parameters. It asks the
generator to produce what it needs and reads the receipts. A presenter
consumes frames and reports when it showed them. A second machine holds a
replica and syncs. Nothing in the consumer needs to know about caching,
history, or convergence, because those are properties of the store.

### What is not built

Retention policy and the pruning it drives. Change-driven recomputation.
Running a plan on the executor it names. Routing kinds that substitute one
upstream for another. Kinds as a registry with schemas. Decisions and their
presentations on the table. The responsible adult. Presenters beyond text.
Sync estimation between clocks. Any network transport. Signed manifests.
The operations log for kinds that need to merge by intent. Real model
weights anywhere in the test suite.
