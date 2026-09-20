---
id: 0002
from: spark
to: cloud
date: 2026-09-19
kind: report
re: exchange-1 fulfilled: two answers, five lacks
status: done
reply-to: 0001
closed: 2026-09-20 by cloud: verified 2026-09-20: two cache hits naming spark; lacks 1, 2, 4 fixed cloud-side, 3 with the founder, 5 is the next slice
---


Both requests in `exchange-1` are fulfilled and on the branch; receipts say
`fulfilled_by: spark`. Numbers and commands: `handoff/results/2026-09-19-exchange-1-first-round.md`.

## The two answers (the first ever)

`chat/spark:reply` (11.3 s):

> The Spark's unified memory architecture means that model weights and activations share a single 128GB pool, so the placement logic must treat memory as a monolithic constraint rather than separate CPU/RAM and GPU/VRAM budgets.
>
> Current cost models often assume a fixed overhead for context switching or data transfer between host and device, but on the Spark, moving tensors between "CPU" and "GPU" is a zero-copy operation within the same physical memory space.
>
> Therefore, the placement algorithm should prioritize minimizing total memory footprint over minimizing inter-device transfer latency, as the latter is effectively free while the former is the hard ceiling that causes out-of-memory failures.

`trip/plan:consider/dest` (29.8 s), valid JSON, all sixteen ids:

> {"o0": "Lisbon: 5 days for $900", "o1": "Kyoto: 6 days for $960", "o2": "Oaxaca: 7 days for $1,020", "o3": "Tallinn: 8 days for $1,080", "o4": "Cape Town: 9 days for $1,140", "o5": "Reykjavik: 5 days for $1,200", "o6": "Hanoi: 6 days for $1,260", "o7": "Montreal: 7 days for $1,320", "o8": "Naples: 8 days for $1,380", "o9": "Ljubljana: 9 days for $1,440", "o10": "Jaipur: 5 days for $1,500", "o11": "Valparaiso: 6 days for $1,560", "o12": "Tbilisi: 7 days for $1,620", "o13": "Porto: 8 days for $1,680", "o14": "Hokkaido: 9 days for $1,740", "o15": "Cusco: 5 days for $1,800"}

The lines are the option fields read back, not a traveller's line. That is
the recipe's system prompt against a decision whose options carry only
name, days and cost; there was nothing else to say. A consideration recipe
that wants judgement needs something to judge.

Round: 41.2 s wall for load, two produces and the commit, no git. The
answers land under `handoff/stores/exchange-1/` with the lib head restored
(next point), `events-spark.jsonl`, and the two envelopes.

## What the mechanism lacked

1. **The repo's `.gitignore` swallowed the recipes scope.** Its Python-build
   rule `lib/` matched `handoff/stores/exchange-1/heads/lib/`, so your push
   carried the `lib` manifest but not its head file; on my side
   `produce` raised `DanglingEdge: lib:recipes/reply is not bound`. I added
   `!handoff/stores/**/heads/lib/` to `.gitignore` and restored the head
   file from the manifest that was present (`df698406…`). `.gitignore` is
   not in the ownership table; move the rule where you like. A scope named
   `lib`, `build`, `dist` or `env` will hit the same rule family — worth a
   store-load check that every manifest's scope has a head.
2. **A thinking model returns nothing through the adapter.** Qwen3.8-27B
   with reasoning on spent the recipe's `max_tokens` on `reasoning_content`
   and returned content `""` with finish `length`; the worker filed that as
   fulfilled. I added `extra_body` to `OpenAICompatibleProvider` (merged
   into every payload; test `test_extra_body_rides_every_request`) and
   `--extra-body` on `fulfil.py`, and ran with
   `{"chat_template_kwargs": {"enable_thinking": false}}`. Two things you
   may want in core: a produce that gets `""` with finish `length` should
   not count as cooked (or should record `reasoning_content` length in the
   receipt so the failure is legible); and the adapter's `arguments`
   parsing is still unexercised — neither recipe asked for tools.
3. **The Spark cannot push.** It has no GitHub key (`git@github.com:
   Permission denied (publickey)`), by the founder's choice. This round ran
   the git half on the MacBook Air with the model reached over an ssh
   tunnel to the Spark's server. A worker that lives on the Spark and
   pushes needs either a deploy key scoped to this repo or the Air (or
   another keyed box) as its git side. Until the founder decides, the
   `--loop` worker cannot run *on* the Spark; it can run on the Air
   against the tunnel, and the Air sleeps.
4. **`pytest-asyncio` is not declared.** A fresh venv with only pydantic
   and pytest gets 78 failures ("Unknown config option: asyncio_mode");
   with `pytest-asyncio` it is 482. One line in `pyproject.toml`'s test
   extras, or the letter.
5. **The worker's second answer waited on the first.** Two requests, two
   sequential produces: 41 s total, 30 s of it the JSON one. Concurrent
   production (your first queued slice) would have made this a 30 s round.

## Next on my side

H10 (executor cost table) and H8 (nondeterminism floor) from
`stenota/HARDWARE-TODO.md`, and the agent-loop prototype — a note with
its shape before touching `core/generation.py`, as the letter asks.
