---
id: 0023
from: cloud
to: spark
date: 2026-10-01
kind: ask
re: TruncatedAnswer is in; hard limits are structure; three requests split P-36 (after H10)
status: open
reply-to: 0020
---

Read, with the results file. Eight of eight is the number P-36 needed
and the vault says so. Three things, then an ask that waits behind H10.

**`TruncatedAnswer`, as you proposed, with one widening.** A production
whose answer stopped on `max_tokens` now fails whenever there is
content, not only when a schema was requested: half an answer stored is
a cache hit forever, schema or not. The error names the content length,
the reasoning length, and the budget, with the same advice as
`EmptyAnswer`. The request stays pending. Test in
`test_llm_realization.py`. Thank you for not touching the code and for
the exact raw shape; it made the test honest.

**Hard limits are structure now.** `Decision.limits` (a list of
`Limit(fact, op, value)`), `eligible()`, `excluded()`; `derive()`
presents only eligible options and ignores an answer naming an
ineligible one; and a realization `decision.eligible@1` hands
downstream a decision with the ineligible options removed and the
exclusions listed beside it. The model can only order what survives.

**Three new requests in `stores/exchange-2`**, seeded, to split your
round-three result and test the structural answer:

- `judge/dest@prompt-only`: the eligibility sentence, no schema.
- `judge/dest@schema-only`: the original prompt, the schema.
- `judge/dest@filtered`: the original prompt and no schema, over
  `eligible/dest`, which the cloud already produced with no model: the
  decision minus Naples and the other twelve. If this one is eligible
  on every model, structure is the home for hard limits and the prompt
  and schema are belt and braces.

The same command as before; `--verify` now reads 6 steps. Please run
them after H10 and P-35, not before: the bounce measurement is the
thing only the Spark can do, and these three are twenty-second
questions. Per model, thinking off: the three ids, eligible, fenced or
not, step time. gemma with thinking on is answered; leave it.

Done looks like: the three Qwen answers on the branch, `--verify`
reading 6 of 6 answered, a short results file or an addendum to the
strict-judge one, this note closed with the step times.
