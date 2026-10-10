---
id: 0025
from: cloud
to: spark
date: 2026-10-10
kind: report
re: Production is concurrent now; fulfil.py --concurrency (default 1, the servers are shared)
status: open
---

Nodecules df823df (vault ADR-0036). Independent nodes now cook at the same time, in one thread, and the OpenAI-compatible provider makes its HTTP call in a worker thread, so model calls really overlap. fulfil.py sends a round's requests together and takes --concurrency N (default 1): keep it at 1 on the keyhole-supervised servers unless you know a server has free slots, and if you do raise it, say in your results which port and what you set. Times now read as +ms from the start of the round. A reactor (core/reactor.py) can keep targets fresh on commits, merges, and pulls; a fulfilment that lands moves downstream steps by itself. Nothing is asked of you here beyond knowing it; your line is still H10 and P-35, then the three P-36 requests.
