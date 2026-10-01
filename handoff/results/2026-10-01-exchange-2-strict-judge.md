# Exchange-2, round three: `judge/dest@strict` (spark, 2026-10-01)

Note 0017 asked for this. The new request names budget, max_days, and
max_flight_h as eligibility rules in the system prompt and sets
`response_schema`, which the adapter sends to llama.cpp as
`response_format: json_schema`. Same Spark (GB10), same
keyhole-supervised servers. Nothing was restarted.

**On the branch:** Qwen3.8-27B's answer (first run). `--verify` reads
`3 of 3 answered; checks: 2 of 3 passed; failed: judge/dest`. The one
failure is round two's unconstrained `judge/dest` (Naples), which is
kept unchanged so the two can be compared.

## Per model

| model | thinking | max_tokens | ranked ids | all eligible | schema honoured | step time | tokens out |
|---|---|---|---|---|---|---|---|
| Qwen3.8-27B Q4_K_M (:8087) | off | 800 (recipe) | o0, o2, o5 | **yes** | yes, bare JSON, no fence | 24234 ms | 216 |
| gemma-4-12B q4_0 (:8083) | off | 800 (recipe) | o0, o2, o5 | **yes** | yes, bare JSON, no fence | 7107 ms | 158 |
| gemma-4-12B q4_0 (:8083) | on | 2000 | none | — | — | 75945 ms | 2000, all reasoning; `EmptyAnswer`, request left pending |
| gemma-4-12B q4_0 (:8083) | on | 4000 | (o0, o2, cut off) | — | **truncated** mid-object | 159212 ms | 4000, 10805 chars of reasoning |

**Stability.** I repeated both thinking-off rows three more times on
fresh scratch copies. All eight runs ranked o0, o2, o5 in that order
and passed. Step times were Qwen 19608, 17564, 19267 ms and gemma 5479,
6035, 5577 ms. Over four runs each, the medians are about 19.4 s for
Qwen and 5.8 s for gemma.

**What changed it.** In round two, the same models given the same facts
without the eligibility sentence and the schema put an ineligible option
third (Naples, Tallinn) and gemma wrapped its JSON in a fence. In round
three, with both changes, eight of eight runs were correct and
unfenced. This experiment cannot say which change did which job. It
looks like the eligibility sentence fixed the choice and the schema fixed
the fence. Splitting them would take two more requests: prompt only, and
schema only.

## Finding: a truncated answer is stored as fulfilled

The gemma run with thinking on at 4000 tokens ended with
`finish_reason: "length"` after 10805 characters of reasoning. Its
content was the opening of the JSON object and stopped mid-object:

```
'{\n "ranked": [\n  {\n   "id": "o0",\n   "why": "Lisbon perfectly aligns with the \'warm\', \'walking\', and \'food\' wants while remaining well within all budget, time, and flight constraints."\n  },\n  {\n   "id": "o2",\n'
```

`EmptyAnswer` only fires when content and tool calls are *both* empty,
so this production was **cooked and stored as fulfilled**, and the
request was cleared. `--verify` caught it as `FAIL not JSON`, but only
because this decision has a checker. The adapter already maps
`finish_reason: "length"` to `stop_reason="max_tokens"`. A proposal for
cloud, since `llm_realization` is cloud's and this changes its contract:
when `stop_reason == "max_tokens"`, fail the production
(`TruncatedAnswer`, with the same advice as `EmptyAnswer`) rather than
store it. At minimum, do this when a `response_schema` was requested,
because then a partial answer is invalid by construction. I have not
changed the code. This was a scratch copy and nothing truncated is on
the branch.

The 2000-token trace answers 0017's question. gemma's reasoning was on
the right track. It worked out the eligible set `{o0, o2, o5}`
explicitly, then spent the rest of the budget re-checking it ("*Wait,
let me check 'crowds' and 'altitude' for these three.*"). By 4000
tokens it had settled on Lisbon > Oaxaca > Reykjavik, with the same
reasons it began to write. So thinking gets this model to the right
answer, at 10–20 times the cost of the same model with thinking off,
which also got it right once the prompt said what eligibility means. On
this task, thinking costs a lot and adds nothing.

The figure `max_tokens=800` in the 2000-token run's `EmptyAnswer`
message is my harness, not a bug. My scratch provider overrode
`max_tokens` in the payload, below the realization, so the realization
reported the recipe's 800 while the engine received 2000 (confirmed in
the logged payload).

## Raw messages

Every answer parsed except the truncated one quoted above. The Qwen
answer on the branch, `last_raw["choices"][0]["message"]["content"]`:

```json
{
 "ranked": [
  {
   "id": "o0",
   "why": "Meets all hard limits (€900, 5 days, 7h flight) and strongly aligns with wants for warm weather, walking, and food (custard tarts), while avoiding crowds and altitude."
  },
  {
   "id": "o2",
   "why": "Meets all hard limits (€1020, 7 days, 6h flight) and offers excellent food (mole, mezcal) and walking opportunities in a mild, dry climate, with no altitude concerns."
  },
  {
   "id": "o5",
   "why": "Meets all hard limits (€1200, 5 days, 6h flight) but ranks lower due to cold/windy weather conflicting with the 'warm' want and expensive food, though it avoids crowds and altitude."
  }
 ]
}
```

Qwen wrote the
prices in euros. The facts give bare numbers, so the currency is the
model's invention.

## Commands

From `backend/` with the venv, each run on its own scratch copy of
`handoff/stores/exchange-2`. `rawlog_provider` is the same throwaway
logging subclass as in round two, now with an optional `MAX_TOKENS`
override in the payload.

```bash
OFF='{"chat_template_kwargs": {"enable_thinking": false}}'
RAWLOG=raw.jsonl BASE_URL=http://127.0.0.1:8087 EXTRA_BODY="$OFF" \
PYTHONPATH=.:$SCRATCH .venv/bin/python ../handoff/fulfil.py --store $SCRATCH/ex3-qwen27 \
    --provider rawlog_provider:make --model unsloth/Qwen3.8-27B-GGUF:Q4_K_M --by spark
#   gemma-12B thinking off: BASE_URL=...:8083 EXTRA_BODY="$OFF"
#   gemma-12B thinking on:  BASE_URL=...:8083 EXTRA_BODY= MAX_TOKENS=2000 (then 4000)
PYTHONPATH=. .venv/bin/python ../handoff/seed_exchange_2.py --store $SCRATCH/ex3-qwen27 --verify

rsync -a --delete --exclude events-cloud.jsonl $SCRATCH/ex3-qwen27/ ../handoff/stores/exchange-2/
```
