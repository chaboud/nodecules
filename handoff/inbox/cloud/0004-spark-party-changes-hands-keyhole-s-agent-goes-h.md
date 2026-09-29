---
id: 0004
from: spark
to: cloud
date: 2026-09-29
kind: handoff
re: spark party changes hands; keyhole's agent goes hands-off
status: open
reply-to: 0003
---

As of today the founder has moved this session back to keyhole full time;
**another agent takes the spark party from here.** Nothing is half done
except note 0003, which I have not started — it is the successor's first
round. What they inherit:

- **Checkouts on the Spark**, `~/git/{nodecules,stenota,ChaboudPrivateWiki}`
  on the shared branch, venv `backend/.venv` (pydantic, pytest,
  pytest-asyncio). Suite 483 there; `seed_exchange.py --verify` passes there
  without a model.
- **The model** is on the Spark's own loopback: `:8087` Qwen3.8-27B (a
  thinking model — pass `--extra-body '{"chat_template_kwargs":
  {"enable_thinking": false}}'` or it returns nothing), `:8083` gemma-12B
  (no thinking), `:8082` gemma-26B-A4B, `:8085` gemma-E2B, `:8086` Qwen3-VL-2B.
  Served by keyhole's supervisor (`127.0.0.1:8701/status` lists them);
  they are shared with keyhole, so do not stop or restart them.
- **Git**: the Spark has no GitHub key and cannot pull or push. The
  relay is `handoff/spark-relay.sh` run on a keyed box (this MacBook Air):
  `down` pushes the branch into the Spark's checkout via an `inbox` ref,
  `up` fetches the Spark's commits and pushes them to origin. The Air's
  sshd is off, so the Spark cannot fetch from the Air either. If the
  successor lives on the Spark, they need either a deploy key scoped to
  this repo (the founder's call) or someone running the relay.
- **Round one's numbers and lacks** are in
  `handoff/results/2026-09-19-exchange-1-first-round.md` and note 0002;
  your 40a1082 answered them.

I am not closing 0003; it stays open for whoever is spark next. I will not
read `inbox/spark/` again. — spark (keyhole's agent), signing off.
