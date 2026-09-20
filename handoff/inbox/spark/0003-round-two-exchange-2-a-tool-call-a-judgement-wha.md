---
id: 0003
from: cloud
to: spark
date: 2026-09-20
kind: ask
re: Round two: exchange-2 (a tool call, a judgement); what changed for your five lacks
status: open
reply-to: 0002
---

Verified on my side, 2026-09-20: `seed_exchange.py --verify` reports both
steps as cache hits with receipts naming `fulfilled_by: spark`. The two
answers are the first ever through this substrate, and the unified-memory
claim in the first one is now vault open question P-35, marked as a
model's claim until H10 measures it. Your numbers are in `STATUS.md` and
the vault as "measured on the Spark". H12 in stenota is ticked.

## What changed for each lack

1. **Gitignored head.** Your negation covered `heads/lib/`; the same rule
   family also swallowed `build`, `dist`, `env`, `var`, `tmp`, and `logs`.
   `.gitignore` now ends with `!handoff/stores/**` (verified by creating
   real files under those names and reading `git status`). And a store
   whose manifests arrive without their head files is now recovered on
   load: the leaves of the scope's manifest DAG become its candidate
   heads, written back, with a `RuntimeWarning` naming the scope and
   asking whether the directory is gitignored. Test in `test_disk.py`.
2. **Empty answer filed as fulfilled.** `ToolCallResponse` has a
   `reasoning` field, filled by the adapter from `reasoning_content`.
   `llm_realization` raises `EmptyAnswer` when content and tool calls
   are both empty, so the production is `failed` with an error that says
   `reasoning=N chars, max_tokens=M` and what to do; nothing is stored,
   and the request stays pending. Your `extra_body` and `--extra-body`
   are kept as they are. Tests in `test_llm_realization.py` and
   `test_openai_compatible.py`.
3. **No push from the Spark.** With the founder; this is the one item
   from your report I am raising as a decision. Until then the Air stays
   the git side, as you ran it.
4. **pytest-asyncio.** It was declared in `pyproject.toml`'s dev group
   all along; the docs said "pydantic and pytest alone" and were wrong.
   Every place that said so now says pydantic, pytest, and
   pytest-asyncio, with the pip line.
5. **Sequential production.** The next slice on my side, ahead of
   everything else in `STATUS.md`'s order. Your 41 s round is its first
   benchmark.

## The ask

`handoff/stores/exchange-2` holds two requests for the two things the
first round could not exercise:

- `chat/tools:reply`: a weather question with two tools declared in the
  recipe params. The check is whether `tool_calls[0].arguments` arrives
  as an object with a `city` key. If your engine returns arguments as a
  string, the adapter parses it; if the parse fails you will see `_raw`.
  Please put `provider.last_raw["choices"][0]["message"]` for this call
  in your report, verbatim, so I can see the shape your engine emits.
- `trip/plan:judge/dest`: the same sixteen destinations with facts
  (season, visa, flight hours, a note) and a traveller's constraints as
  a second input; the model ranks three with a reason each, as JSON.
  Two roles rendered into one message; the check is three known ids.

Same command as before, with the store changed and `--extra-body` as you
had it:

    cd backend && PYTHONPATH=. python3 ../handoff/fulfil.py \
        --store ../handoff/stores/exchange-2 \
        --provider openai --base-url http://127.0.0.1:PORT --model NAME \
        --timeout 600 --by spark --extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'

`seed_exchange_2.py --verify` on your side prints both checks. Done looks
like: the store on the branch, this note closed with the two round
times, a report with the raw message shape and both checks' output.

Then, as you planned: H10 and H8, and the agent-loop shape as a note
before the code. For H10, P-35 is the question your first answer raised;
a measured CPU-to-GPU-to-CPU bounce on the Spark settles it.
