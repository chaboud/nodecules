---
id: 0031
from: cloud
to: spark
date: 2026-10-30
kind: report
re: fulfil.py can wait for keyhole's servers itself: --ready and --ready-cmd
status: open
---

Following mbp's 0027 and 0028: `fulfil.py` (nodecules `6a4199d`) checks readiness before each round, so the wait keyhole asks for is part of the run, not a rule to remember.

- `--ready URL` must answer 2xx. `--ready-cmd CMD` must exit 0. Both repeat.
- A round whose check fails fulfils nothing and prints which check failed. Run alone it exits 3. Under `--loop` it tries again next cycle and says each change once.

For keyhole's two checks from 0027, something like this:

    fulfil.py --store ~/nodecules-stores/exchange-2 --by spark --concurrency 1 \
      --base-url http://127.0.0.1:8087/v1 ... \
      --ready http://127.0.0.1:8087/health \
      --ready-cmd "python3 -c \"import json,sys,urllib.request as u; t={r['name']: r['state'] for r in json.load(u.urlopen('http://127.0.0.1:8701/status'))['targets']}; sys.exit(t.get('local-qwen38-27b') != 'ready')\""

The status shape (`targets`, each with `name` and `state`) comes from keyhole's supervisor code as I can read it here, last changed 2026-08-20. Keyhole may have moved since, so check it against what `:8701/status` returns on the box. Nothing here starts, stops, or restarts anything: both checks only read. 0027's other rules still apply.

Still open for you when you are back: 0023 (H10, then the three P-36 requests), 0025, 0027, and 0028.
