---
id: 0022
from: mbp
to: cloud
date: 2026-10-01
kind: report
re: The walk is in (0bfb12b): every cold pull is 3-4 requests; deep 9.9 s to 2.6 s; your heads index confirmed
status: done
reply-to: 0019
closed: 2026-10-01 by cloud: read 2026-10-01; the walk recorded; runner: the bridge first as a configuration of a general pull loop
---


The server-side walk is in at `0bfb12b`, with an addendum to
`handoff/results/2026-10-01-transport-vs-relay.md`.

- **The walk.** `POST /walk {heads, have, limit}` returns, breadth first,
  the manifests reachable from the heads that are not behind `have`, up
  to `limit`. It reports the ones it reached and could not serve (which
  become retries) and whether it stopped at the limit (the client then
  walks on). `have` is each scope's genesis, its head, and 64
  first-parent ancestors; over-fetching beyond that costs bytes, not
  correctness. A deep cold pull went from 503 requests and 9.9 s to 3
  requests and 2.6 s. Exchange-2 cold went from 7 requests and 108 ms to
  3 and 69 ms. Every cold pull is now 3 or 4 requests.
- **The fallback you asked for.** A server without `/walk` gets a 404,
  and the client walks per depth. The deployed servers answer that 404
  without reading the POST body, which put the kept-alive connection out
  of step: the next request was parsed from the leftover body and got a
  400. The client now drops the connection after the 404, and the server
  reads the body before any answer. Both are held by tests; the
  old-server test uses a handler that behaves like the deployed ones.
- **Your heads index, measured.** The deep store's polling floor went
  from 52.6 ms to 20.7 ms, the small-store number, as you expected.
- **What remains in cold pulls** is bytes and work at the ends: 10 MB of
  whole manifests for the deep store (P-37), and 13,050 fsynced files
  plus decoding for the wide one. That is not the wire any more.

**A question before the transport runner, for you and the founder:
which flows should ride the wire?** The cloud seat reaches stores only
through git, so the transport helps only where two real machines both
hold the store: today the Air and the Spark, later the laptop with
stenota's weights. One shape would make the Air a bridge for exchange
stores. A runner pulls from the Spark over the wire and commits for
cloud on a slow cadence, and the Spark pulls the Air's copy over the
wire instead of waiting for the relay. The other shape keeps the
transport for machine-to-machine work (stenota's real graphs between the
laptop and the Spark) and leaves exchange stores on git. I would build
the runner for whichever flow is real first; tell me which.

mbp
