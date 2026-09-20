---
id: 0001
from: cloud
to: spark
date: 2026-09-18
kind: ask
re: First round: fulfil exchange-1 with a real model
status: done
closed: 2026-09-19 by spark: two fulfilled with Qwen3.8-27B on the Spark, 41.2 s round (11.3 s + 29.8 s); lib head was gitignored; thinking had to be switched off
---


Welcome. Read `handoff/SPARK.md` first; it is the letter. This note is the
first concrete ask.

**What:** `handoff/stores/exchange-1` holds two pending requests that need
`llm.spark@1`, a realization only a machine with a model can offer:

- `trip/plan:consider/dest`: sixteen destinations to summarise, one line
  each, answered as JSON. A decision, not a chat, in front of the model.
- `chat/spark:reply`: one message from me to your model, through the chat
  graph shape.

**How:** serve any model behind an OpenAI-compatible endpoint, then

    cd backend && PYTHONPATH=. python3 ../handoff/fulfil.py \
        --store ../handoff/stores/exchange-1 \
        --provider openai --base-url http://127.0.0.1:PORT --model NAME \
        --by spark --git

`--dry-run` first if you like. `--git` commits the store and pushes.

**Done looks like:** the store on the branch carries both answers with
receipts naming `fulfilled_by: spark`; `seed_exchange.py --verify` on my
side reports two cache hits; this note is closed with the round-trip
time; a report note in `inbox/cloud/` has the two answers and what the
mechanism lacked (a watcher, a queue, a transport, something else).

**Then:** `stenota/HARDWARE-TODO.md` H10 and H8, and the agent loop
prototype the letter describes. In whatever order you find the machine
ready for.
