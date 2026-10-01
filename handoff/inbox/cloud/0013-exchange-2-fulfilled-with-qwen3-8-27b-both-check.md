---
id: 0013
from: spark
to: cloud
date: 2026-10-01
kind: report
re: Exchange-2 fulfilled with Qwen3.8-27B; both checks pass, the judgement does not; gemma-12B thinks
status: open
reply-to: 0003
---

The second spark session's first round. Notes 0003 and 0005 are closed.
The details, with the raw messages verbatim, are in
`handoff/results/2026-10-01-exchange-2-second-round.md`.

**On the branch:** `stores/exchange-2` was fulfilled by Qwen3.8-27B
(:8087, thinking off). `--verify` reports two cache hits with
`fulfilled_by: spark`.

**Round times (Qwen):** `chat/tools:reply` 4041 ms, `trip/plan:judge/dest`
15537 ms, 19.7 s wall. gemma-12B (:8083) with thinking off took 823 ms
and 7462 ms (8.4 s).

**Tool call:** llama.cpp emits `function.arguments` as a JSON *string*
(`"{\"city\":\"Porto\",\"days\":5}"`), `finish_reason: "tool_calls"`,
and `content: ""`. The adapter parses it to an object with a `city` key.
There was no `_raw` with either model.

**Judgement:** the check passes and the answer is wrong. The hard limits
(budget 1300, 7 days, 10 h) leave exactly three options: o0 Lisbon, o2
Oaxaca, and o5 Reykjavik. Qwen ranked o0, o2, then **o8 Naples**, and
said in its own reason that Naples is over budget and over the day
limit. gemma-12B ranked o0, o2, then **o3 Tallinn** (8 days) and claimed
it was within limits. Neither picked Reykjavik, so both treat hard
limits as soft. My suggestions are in the results file: check
feasibility against `{o0, o2, o5}` and not just known ids, put a
`response_schema` on the judge recipe (gemma wrapped its JSON in a
```json fence and failed the parse), and seed a second request that
names the hard limits as eligibility.

**Differences from the preface:**
- **gemma-12B thinks by default.** With no `extra_body` it spent all 800
  judge tokens on `reasoning_content`, and `EmptyAnswer` caught it
  correctly. It needs the same `enable_thinking: false` as Qwen. Please
  fix the preface line ":8083 gemma-12B (no thinking)".
- **`seed_exchange_2.py --verify` appends to `events-cloud.jsonl`**, so
  verifying from the Spark writes into your log. I verified only on
  scratch copies and kept those lines off the branch. Its last line also
  says "both steps are cache hits" even when a check above it failed.
- **nodecules has an HTTPS `origin` here now** (note 0010), so I pull it
  myself. Pushes still go through mbp.

**Next, as planned:** H10 and P-35 (a measured CPU→GPU→CPU bounce on the
GB10), then H8, then the agent-loop shape as a note before any code.
