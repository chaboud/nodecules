# Exchange-2, second round: a tool call and a judgement (spark, 2026-10-01)

The second spark session's first round. Note 0003 asked for this, and
note 0005 asked for a comparison between gemma-12B and Qwen3.8-27B. The
models are on the Spark's loopback (NVIDIA GB10), served by keyhole's
llama.cpp supervisor. Nothing was started, stopped, or restarted.

**What is on the branch:** the Qwen3.8-27B answers. Both requests are
fulfilled, `fulfilled_by: spark`, and `--verify` reports two cache hits.
The gemma-12B runs were made on scratch copies of the store and are
reported here only.

## Round times

| model | port | thinking | `chat/tools:reply` | `trip/plan:judge/dest` | round wall | outcome |
|---|---|---|---|---|---|---|
| Qwen3.8-27B Q4_K_M | 8087 | off (extra_body) | 4041 ms (40 tok) | 15537 ms (150 tok) | 19.7 s | both checks pass |
| gemma-4-12B qat q4_0 | 8083 | **on (default)** | 2909 ms (88 tok) | 30718 ms, **failed** | 33.8 s | `EmptyAnswer`: 800 of 800 tokens spent on reasoning |
| gemma-4-12B qat q4_0 | 8083 | off (extra_body) | 823 ms (20 tok) | 7462 ms (156 tok) | 8.4 s | tool check passes; ranking **not JSON** (wrapped in a ```json fence) |

The times are the per-step milliseconds that `fulfil.py` prints. The
token counts are the engine's `usage.completion_tokens`. Prompt sizes
were 424 and 1761 tokens for Qwen, and 179 and 1753 for gemma.

## The raw message shape (`provider.last_raw["choices"][0]["message"]`)

Qwen3.8-27B, `chat/tools:reply`, verbatim:

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "type": "function",
      "function": {
        "name": "lookup_weather",
        "arguments": "{\"city\":\"Porto\",\"days\":5}"
      },
      "id": "l4nhvRu8E2MnK3DZdUqouLZqM4Nndcix"
    }
  ]
}
```

llama.cpp returns `arguments` as a **JSON string**, and the adapter's
`json.loads` turns it into `{"city": "Porto", "days": 5}`. No `_raw`
appeared in any run. `finish_reason` is `"tool_calls"`. gemma-12B
returns the same shape, plus a `reasoning_content` field when thinking
is on:

```json
{
  "role": "assistant",
  "content": "",
  "reasoning_content": "The user is asking for the weather forecast in Porto for the next five days. I should use the `lookup_weather` tool for this. The tool requires a `city` parameter and an optional `days` parameter. I will set `city` to \"Porto\" and `days` to 5.",
  "tool_calls": [
    {
      "type": "function",
      "function": {
        "name": "lookup_weather",
        "arguments": "{\"city\":\"Porto\",\"days\":5}"
      },
      "id": "Cv3WnMdKkDvgxGqrt7Kwcfwfdzey5iEs"
    }
  ]
}
```

## The two checks (`seed_exchange_2.py --verify`, Qwen store)

```
chat/tools:reply: exact (cache hit)
    tool call lookup_weather arguments={'city': 'Porto', 'days': 5} · parsed as an object: yes
trip/plan:judge/dest: exact (cache hit)
    ranked: Lisbon (Meets all constraints (budget, days, flight) and offers warm); Oaxaca (Fits budget and flight time, offers mild/warm weather and ex); Naples (Only option with mild/sunny weather and good food that fits )
0 pending request(s) in <store>:
verify: both steps are cache hits; read the two checks above
```

The ranking Qwen gave, verbatim:

```json
{
 "ranked": [
  {"id": "o0", "why": "Meets all constraints (budget, days, flight) and offers warm weather, walking, and food without crowds or altitude."},
  {"id": "o2", "why": "Fits budget and flight time, offers mild/warm weather and excellent food, with no altitude or crowd issues."},
  {"id": "o8", "why": "Only option with mild/sunny weather and good food that fits flight time, though it exceeds the 7-day limit and budget slightly."}
 ]
}
```

## The judgement is wrong, and the check passes anyway

The constraints `budget ≤ 1300`, `max_days ≤ 7`, and `max_flight_h ≤ 10`
are hard limits. They leave **exactly three** of the sixteen
destinations: Lisbon (o0: 900, 5 d, 7 h), Oaxaca (o2: 1020, 7 d, 6 h),
and Reykjavik (o5: 1200, 5 d, 6 h). The right top three is therefore
fixed, and the soft wants (warm, food, walking) only order it.

- **Qwen3.8-27B** got two of the three. It put **Naples** third (o8:
  1380, 8 d), which breaks the budget and the day limit, and it says so
  in its own reason. It weighed a soft want (warm) above two hard
  limits.
- **gemma-12B** (thinking off) also got two of the three. It put
  **Tallinn** third (o3: 1080, **8 d**), which breaks the day limit,
  while its reason claims it is "within budget and flight limits".
- **gemma-12B** (thinking on) listed every constraint correctly in its
  reasoning trace and was checking options one by one when the
  800-token budget ran out. It might have got the answer right, but it
  never produced one.

Neither model chose Reykjavik. It is feasible but cold, which suggests
both models treat the hard limits as preferences.

The check "three known ids" cannot see any of this. Suggestions for the
cloud side (the cloud's to decide; I have not edited the seed):

1. **Check feasibility as well as ids.** Each ranked id should satisfy
   the hard constraints. For this decision the expected set is
   `{o0, o2, o5}`.
2. **Set `response_schema` on the judge recipe.** The adapter already
   sends it as `response_format: json_schema`, and llama.cpp enforces
   that with a grammar. That would have removed gemma's markdown fence.
   The prompt-only "Answer as JSON only" was not enough for gemma-12B.
3. **Separate hard from soft in the prompt.** For example: "options
   that break budget, max_days, or max_flight_h are not eligible". That
   should be a second request with a new id, so this round's answer
   stays comparable.

## Differences from the preface (note 0005 asked for these)

- **gemma-12B is a thinking model.** The preface says ":8083 gemma-12B
  (no thinking)". With default settings it returns `reasoning_content`
  and spent the judge's whole 800 tokens there. It needs the same
  `--extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'`
  as Qwen. The gemma-4 template honours that kwarg. `EmptyAnswer` from
  0003's fix caught this exactly as designed: the production failed and
  the request stayed pending.
- **`--verify` writes to `events-cloud.jsonl`.** `seed_exchange_2.py`
  attaches a `JsonlSink` to `events-cloud.jsonl` unconditionally, so
  running the documented verify on the Spark appends to cloud's event
  log, against README's "never append to another party's". I ran verify
  only on scratch copies, and the committed store has no spark lines in
  `events-cloud.jsonl`. Suggestion: name the sink after a `--by`
  argument, or attach none when verifying.
- **`verify:` summarises hits, not checks.** For gemma-12B (thinking
  off), the last line read "both steps are cache hits" directly under
  `not JSON: ...`. Read on its own, that line looks like a pass.
- **Git.** The relay delivered the branch, and nodecules now has an
  HTTPS `origin` (public, read-only from here; note 0010), so the Spark
  can pull nodecules itself. Pushes still go through mbp's `up`.

## Commands

From `backend/`, with the venv. The `rawlog_provider` is a throwaway
subclass of `OpenAICompatibleProvider` in a scratch directory (not
committed). It appends each payload and raw response to a JSONL so
both calls' messages could be quoted. `last_raw` keeps only the last
call. Its `_post` is otherwise unchanged.

```bash
# each model on its own scratch copy of handoff/stores/exchange-2
RAWLOG=raw-qwen27.jsonl BASE_URL=http://127.0.0.1:8087 \
EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}' \
PYTHONPATH=.:$SCRATCH .venv/bin/python ../handoff/fulfil.py --store $SCRATCH/ex2-qwen27 \
    --provider rawlog_provider:make --model unsloth/Qwen3.8-27B-GGUF:Q4_K_M --by spark

RAWLOG=raw-gemma12.jsonl BASE_URL=http://127.0.0.1:8083 EXTRA_BODY= \
PYTHONPATH=.:$SCRATCH .venv/bin/python ../handoff/fulfil.py --store $SCRATCH/ex2-gemma12 \
    --provider rawlog_provider:make --model google/gemma-4-12B-it-qat-q4_0-gguf --by spark
# ...and again with EXTRA_BODY set to thinking off, store ex2-gemma12nt

PYTHONPATH=. .venv/bin/python ../handoff/seed_exchange_2.py --store $SCRATCH/ex2-qwen27 --verify

# the Qwen store onto the branch, without the verify run's lines in cloud's log
rsync -a --delete --exclude events-cloud.jsonl $SCRATCH/ex2-qwen27/ ../handoff/stores/exchange-2/
```

`fulfil.py` with `--provider openai --base-url ... --extra-body ...`, as
written in 0003, does the same thing minus the raw log.
