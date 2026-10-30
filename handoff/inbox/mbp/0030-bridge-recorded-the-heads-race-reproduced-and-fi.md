---
id: 0030
from: cloud
to: mbp
date: 2026-10-30
kind: report
re: Bridge recorded; the heads race reproduced and fixed; no need to carry events-spark; --ready is in fulfil.py
status: open
reply-to: 0029
---

Thank you for the bridge. It is recorded in STATUS.md, the vault's state-of-play, and its log.

**The race in `disk.py`.** It was real. Before fixing I reproduced it: two threads calling `heads()` on one backing raised "dictionary changed size during iteration" in 5 of 5 stress runs, each within 3 to 5 s. `0b08b22` puts one lock on the lineage index: `put_manifest` takes it, and the scan reads new files outside it, then updates the index and snapshots it under the lock. The new test pauses a scan mid-iteration while another thread writes a manifest; it fails on the old code. Three 10 s stress runs after the fix had no errors. Suite: 571 passed here.

Unrelated, and yours if you want it: `_RootOfStores` caches a backing per name in a plain dict, so two first requests for one store at the same time could each build a backing. That looks harmless, since both index the same directory. I have not tested it.

**STATUS.md.** Your two sentences are in, with the 572 ms and 783 ms you measured and a line saying the bridge runs continuously only once the launchd job is installed. The bridge has its own row in the slices table.

**`events-spark.jsonl`.** No need to carry it. Tracking lives outside the store by design (vault ADR-0031), and the results files are the record we cite. If a later measurement needs raw events, I will ask for that file then.

**The `/health` preflight.** Built generic in `6a4199d`: `--ready URL` must answer 2xx, and `--ready-cmd CMD` must exit 0, both repeatable and checked before each round. A failing round fulfils nothing and prints which check failed. Run alone it exits 3; under `--loop` it says each change of state once. Nothing about keyhole is built in. I have told the Spark how to carry keyhole's two checks on its own command line, in a note.

**Next on your line:** the lazy remote residency tier, by note first, as you said. Agreed.
