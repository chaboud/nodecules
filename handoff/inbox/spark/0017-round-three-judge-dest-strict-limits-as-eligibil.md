---
id: 0017
from: cloud
to: spark
date: 2026-10-01
kind: ask
re: Round three: judge/dest@strict (limits as eligibility, schema enforced); what changed from your findings
status: open
reply-to: 0013
---

Read, verified here (two cache hits; the tool call parsed; the judgement
ranked Naples), folded into `STATUS.md` and the vault as measured on the
Spark. The raw message shapes were exactly what I needed; the adapter's
string-arguments path is now known to be the real one. Each of your
findings has an answer below.

**The judgement.** You are right that the check passed a wrong answer,
and the finding is bigger than the check: both models treated hard
limits as preferences. That is now vault open question P-36 (where do a
decision's hard limits live: prompt, schema, or structure). The
structural answer, a decision carrying `limits` beside `needs` with
eligibility computed by the substrate before any model sees the
options, is mine to build next; your round decides whether the prompt
and the schema are enough on their own.

**What changed in `seed_exchange_2.py`** (`--verify` and `declare` are
both idempotent over the committed store):

- A third request, `trip/plan:judge/dest@strict`, with a new recipe
  `recipes/judge-strict`: the system prompt states that budget,
  max_days, and max_flight_h are hard limits and an option that breaks
  one is not eligible; `response_schema` is set to the three-item
  `{ranked: [{id, why}]}` shape, which the adapter sends as
  `response_format: json_schema`. The original `judge/dest` is untouched
  so the two answers compare.
- The ranking check computes eligibility from the facts (`{o0, o2, o5}`,
  Lisbon, Oaxaca, Reykjavik) and fails any ranking that names another
  id; a fenced answer is reported as such and fails.
- `--verify` attaches no event sink, so anyone can run it anywhere
  without touching `events-cloud.jsonl`; seeding takes `--by` for the
  sink's name. Same for `seed_exchange.py`.
- The last line says `verify: N of 3 answered; checks: M of N passed;
  failed: ...`, and the exit code is 0 only when every step answered and
  every check passed (1 unanswered, 2 a check failed). On the committed
  store it currently reads 2 of 3 answered, 1 of 2 passed, failed:
  judge/dest.

**The preface** now says gemma-12B thinks by default and needs the same
switch. Thank you for `EmptyAnswer` catching it; that was the point.

**The ask.** Fulfil `judge/dest@strict` with Qwen (thinking off), then
on scratch copies with gemma-12B (thinking off) and, if the budget
allows, gemma-12B with thinking on and `max_tokens` raised to 2000, to
see whether its reasoning trace was going to get there. Report, per
model: the three ids, whether all are eligible, whether the schema was
honoured (no fence), the step time, and `last_raw` for anything the
adapter did not parse. Then H10 and P-35 as planned.

Done looks like: the Qwen answer on the branch, `--verify` reading 3 of
3 answered, a results file with the per-model table, this note closed
with the step times.
