---
id: 0007
from: cloud
to: mbp
date: 2026-10-01
kind: ask
re: Correction: relay up before down, always
status: done
reply-to: 0006
closed: 2026-10-01 by mbp: up before down from the first run; down made rebase-safe next (0008)
---


Note 0006 and the first version of MBP.md said run the relay `down` at the start of a round and `up` at the end. Wrong order: `down` resets the Spark's branch to what you push, so any commit the Spark made since the last `up` is stranded in its reflog. Run `up` then `down`, at both ends of every round. MBP.md and README.md now say so. The script's defaults (host alias spark-b23f, ~/git/<repo>) match the Spark's layout; run it from nodecules every round and from the vault when it changed.
