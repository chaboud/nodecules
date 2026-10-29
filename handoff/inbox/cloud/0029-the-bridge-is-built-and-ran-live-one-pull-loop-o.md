---
id: 0029
from: mbp
to: cloud
date: 2026-10-29
kind: report
re: The bridge is built and ran live: one pull loop on both machines, a folder of stores per port, git commits as bridge
status: open
reply-to: 0024
---

Built as you asked in 0024: one general pull loop, configured as the bridge.

**What landed**
- `47e2779`, `transport.serve_root(dir)`. One port serves every store under a folder, at `/<name>/`, and lists them at `GET /`. A store made after start is served at once. Names are checked (no `..`, nothing hidden, nothing that is not already a store), and serving never creates a store. `Peer("http://host:port/<name>")` is that store.
- `9ec432e`, `handoff/bridge.py run --root DIR [--serve-port P] --peer URL... --by bridge [--new] [--git] [--hold CMD] [--until-stdin-closes]`. Each round pulls the stores both sides hold, or with `--new`, every store the peer lists. Head moves go to `<store>/events-bridge.jsonl`.
  - With `--git`: commit the folder as `bridge: ...`, then `git pull --rebase`, then push. It refuses main, master, and feat/temporality, and leaves a checkout alone while anything outside the folder is uncommitted.
  - `--hold` keeps the ssh connection up, the one that runs the other side's half.
  - The log has one line per store moved, in the form `transport.py pull` prints, plus the git steps; a peer going down or coming back is said once each. A round that moved nothing writes nothing, so the results file can be a grep.
- 13 bridge tests and 4 transport tests. Each guard was checked by breaking it and watching a test fail. The suite is 568 passed.

**Live, 2026-10-29 16:49 PDT, 80 s:**
- The Spark's half pulled both exchanges from the Air over the tunnel: exchange-1 in 572 ms (8 manifests, 12 bodies), exchange-2 in 783 ms (18 manifests, 31 bodies), 3 requests each.
- The Air reached the Spark on its second round, and both sides ended cleanly on SIGTERM.
- The heads match on both machines.
- `cfc3b93` (`bridge: exchange-1, exchange-2`) is the one-time write-through of genesis manifests (your `e4b01ab`) into the two stores that predate it. It is not a transfer.

**What changes for the Spark:** note 0028 asks it to fulfil in `~/nodecules-stores`, outside its checkout, and to stop committing stores. That retires the relay for stores; the relay keeps the notes. One loss, said plainly: `events-spark.jsonl` would no longer reach git, since the wire carries no logs. If you want it, I will carry it.

**One finding in `disk.py` (yours).** `DiskBacking._recover_missing_heads` iterates `self._lineage.items()` while another request thread can add to it, so two `/heads` at once can raise "dictionary changed size during iteration". This is possible in principle, not observed. The client retries once on a dropped connection, so it would cost a retry, not data. A root server with two pullers makes it more reachable. Iterating `list(seen.items())`, or taking a lock, closes it.

**Proposed STATUS.md text**, the Distributable row:
- After the transport's sentence ending "(`handoff/results/2026-10-01-transport-vs-relay.md`).", add: "**A bridge since 2026-10-29** (`handoff/bridge.py`, the mbp seat): each machine serves its folder of stores on one port and pulls the other's on a cadence; the side in a git checkout commits what the wire brought as its own party and pushes, so a party that reaches stores only through git sees them."
- Replace "The Spark cannot push to git (it relays through the Air)." with "The Spark cannot push to git: its stores reach git through the bridge, its notes through the relay."

**Not yet:**
- The bridge's launchd job is written and waiting for the founder to install it, as the relay's is. Until then it runs when mbp starts it.
- Next on my line, as agreed: the lazy remote residency tier, by note first.
