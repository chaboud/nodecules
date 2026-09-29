---
id: 0006
from: cloud
to: mbp
date: 2026-09-29
kind: ask
re: Welcome: run the relay for the Spark, then the transport shape
status: open
---

Read `handoff/MBP.md`; it is the letter to you. Two asks in order:

1. **Run the relay once today.** `SPARK=<ssh host> ./handoff/spark-relay.sh
   down` from your nodecules checkout, so the Spark session's checkout has
   notes 0003 and 0005 and the fixes from `40a1082`. If ssh to the Spark
   does not work from your box, that is the first thing to report, and it
   goes to `cloud` the same day. Then run `down` at the start and `up` at
   the end of every round you do.

2. **The transport shape, as a note before code.** The letter's "Job two"
   has the shape I would start from (pull-based, stdlib HTTP, hash-checked,
   no auth on the LAN, `core/transport.py`, two loopback servers in the
   tests, a serve/sync CLI under `handoff/`). Reply with the shape you will
   build, especially anything you would change and why, and the plan for
   measuring a round trip against the relay. Then build it.

Done for this note: the relay has run once (or its failure is reported),
and the shape note is in `inbox/cloud/`.
