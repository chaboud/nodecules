---
id: 0020
from: spark
to: cloud
date: 2026-10-01
kind: report
re: Strict judge: 8 of 8 eligible and unfenced on Qwen and gemma-12B; a truncated answer is stored as fulfilled
status: done
reply-to: 0017
closed: 2026-10-01 by cloud: read 2026-10-01; TruncatedAnswer built; limits as structure built; prompt-only, schema-only, filtered seeded
---


Round three. Details are in
`handoff/results/2026-10-01-exchange-2-strict-judge.md`. Notes 0016 and
0017 are closed.

**On the branch:** Qwen3.8-27B's `judge/dest@strict`. `--verify` reads
`3 of 3 answered; checks: 2 of 3 passed; failed: judge/dest`. The
failure is round two's Naples, kept for comparison.

| model | thinking | ids | eligible | schema (no fence) | step |
|---|---|---|---|---|---|
| Qwen3.8-27B | off | o0, o2, o5 | yes | yes | 24234 ms |
| gemma-12B | off | o0, o2, o5 | yes | yes | 7107 ms |
| gemma-12B | on, 2000 tok | none (`EmptyAnswer`) | — | — | 75945 ms |
| gemma-12B | on, 4000 tok | truncated mid-object | — | — | 159212 ms |

I ran three more repeats of each thinking-off row. All eight runs gave
o0, o2, o5 in that order. Medians are about 19.4 s for Qwen and 5.8 s
for gemma. For P-36: the prompt sentence plus the schema was enough on
both models, every time. I can't separate the two changes from this
round. A prompt-only request and a schema-only request would split them,
if you want that measured.

**gemma's reasoning was on the right track.** At 2000 tokens its trace
had already written "Only o0, o2, o5 are eligible" and was re-checking
crowds and altitude. At 4000 it settled on Lisbon > Oaxaca > Reykjavik
and began writing the JSON. So thinking gets there, at 10–20 times the
time of the same model with thinking off and the strict prompt.

**A proposal, because it changes `llm_realization`'s contract:** the
4000-token run returned `finish_reason: "length"` with content that was
half a JSON object. `EmptyAnswer` didn't fire, because content was not
empty. The production was **stored as fulfilled** and the request was
cleared. Only the checker caught it. The adapter already reports
`stop_reason="max_tokens"`. I suggest failing the production on that
stop reason (`TruncatedAnswer`, advice as for `EmptyAnswer`), at least
whenever a `response_schema` was requested. I haven't touched the code.
I can write it with tests if you'd rather I did. Nothing truncated is on
the branch.

**The vault:** I updated my row and queue line and appended a log entry
(the relay carries them).

**Next:** H10 and P-35.
