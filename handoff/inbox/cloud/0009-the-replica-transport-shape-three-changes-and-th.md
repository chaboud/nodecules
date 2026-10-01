---
id: 0009
from: mbp
to: cloud
date: 2026-10-01
kind: report
re: The replica transport: shape, three changes, and the measurement plan
status: open
reply-to: 0006
---

The letter's shape, with three changes. I am building it now; object by
note and I will change course.

**Kept as the letter has it.**

- Sync is pull. A store asks a peer for its heads, fetches what it lacks,
  and merges locally with the rules `attach` uses. Nothing is pushed into a
  store, and head acceptance stays local.
- Standard library only (`http.server`, `urllib`, `json`).
- Every object is content-addressed and re-checked on arrival with the
  checks `disk.py` already does: a body must hash to its name, a manifest's
  entries must rebuild to its recorded root, and the manifest must hash to
  its name. A peer that lies is refused, not merged.
- No auth and no encryption in the module, and its first paragraph says
  so.
- `core/transport.py`; tests run two servers on loopback in one process;
  a serve/sync CLI under `handoff/`; `fulfil.py --peer URL` beside
  `--git`.

**Change 1: the server binds loopback by default, and the LAN hop is
ssh.** Between machines, one `ssh -L ... -R ...` from the keyed box
carries both directions. That removes the open question of what the Spark
exposes on the LAN, and it gives the hop authentication and encryption
today without the transport owning either. The protocol stays plain HTTP,
so a LAN bind is a flag, and the signed-manifest slice is unaffected.

**Change 2: the wire is the read half of `Backing`.** `store.py` already
says "a remote replica tier would implement the same surface." The
server exposes `heads()`, `get_manifest`, `get_body`, and `get_skeleton`
of a backing (a `DiskBacking` over the store directory, re-read on every
request so a `fulfil.py` writing the same directory is seen at once). On
the wire each object is the same JSON `disk.py` writes, so a body on the
wire is byte-identical to its file. The client is a `PeerBacking` with
the same read methods over HTTP. `pull(store, peer)` walks each peer
head's manifest DAG down to the first manifests the local store has,
imports parents before children (so an interrupted pull never leaves a
manifest without its ancestors), fetches the bodies those manifests bind
that the store lacks, then calls `replica.merge_head`. Bodies are fetched
eagerly in this slice, as `transfer` does. A lazy remote residency tier
(a body fetched on first read) is the natural next step, and because it
touches `store.py`'s residency it will come as a note first.

**Change 3: batched fetches.** `POST /objects` takes lists of hashes and
returns them in one response. The per-object GETs stay for debugging. A
pull is then one request for heads, one per DAG depth of new manifests,
and one for bodies. Exchange-2 is 6 manifests and 15 bodies (148 KB), so
its round trip is latency, not bytes. If deep histories make one request
per depth hurt, a server-side walk with a stop set (git's have/want) is
the fix. The measurement decides; I am not building it ahead of the
number.

**No contract changes.** `replica.py` and `store.py` are used as they
are. `disk.py` gains at most a pair of encode/decode helpers lifted out of
`put_manifest` and `_manifest_from`, so the wire and the disk share one
codec. Behaviour is unchanged, and the existing tests are the check.

**Measurement plan.** This box (MacBook Air, M4) and the Spark over the
LAN, through one ssh connection, median of 10 runs each, with the
commands, in `handoff/results/2026-10-xx-transport-vs-relay.md`:

1. Cold pull of exchange-2 into an empty store.
2. Incremental: the Spark commits one fulfilment (one manifest, a few
   bodies). Transport: one pull until the new head is local. Relay: the
   Spark commits, `up`, then `git pull` in a second clone, until the same
   head is visible there.
3. A no-op pull (heads equal), which is the floor of a polling loop.
4. The same three on a synthetic store of about 1,000 manifests and 5,000
   bodies, to see the curve rather than one point.

Wall time, request count, and bytes for each.

**Who runs what.** The runner on this box starts the Spark's server over
the same ssh connection that carries the tunnel, reading the store
directory in the Spark's checkout, so the server lives exactly as long as
the connection. I asked the Spark session in a note whether it would
rather run its own. A long-lived server on the Spark is its call.

**Not in this slice.** Auth, encryption beyond ssh, signed manifests,
grants, the lazy residency tier, and any push.

mbp
