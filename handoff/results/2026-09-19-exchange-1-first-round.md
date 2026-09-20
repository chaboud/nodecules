# exchange-1, first round — 2026-09-19 (spark)

Model: `unsloth/Qwen3.8-27B-GGUF:Q4_K_M` on llama.cpp `llama-server` on the DGX
Spark (GB10), served on its loopback :8087 and reached from the git side over
an ssh tunnel (`ssh -L 18087:127.0.0.1:8087`). The git side was the MacBook
Air: the Spark has no GitHub key (see the report note). Thinking OFF via
`chat_template_kwargs` (see below); recipe budgets as declared (600 / 1200).

| request | wall (fulfil.py) | model output |
|---|---|---|
| `chat/spark:reply` | 11.3 s | 3 paragraphs, `stop_reason: end_turn` |
| `trip/plan:consider/dest` | 29.8 s | 16-key JSON object, `end_turn` |
| **round** (load, 2 produces, commit; no git) | **41.2 s** | |

Commands (from `backend/`, venv = pydantic + pytest + pytest-asyncio):

```bash
.venv-spark/bin/python -m pytest tests/temporal/ -q                # 483 passed (482 + the extra_body test)
PYTHONPATH=. .venv-spark/bin/python ../handoff/fulfil.py --store ../handoff/stores/exchange-1 --provider echo --by spark --dry-run
PYTHONPATH=. .venv-spark/bin/python ../handoff/fulfil.py --store ../handoff/stores/exchange-1 \
    --provider openai --base-url http://127.0.0.1:18087 --model "unsloth/Qwen3.8-27B-GGUF:Q4_K_M" \
    --timeout 600 --by spark --extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'
```

A first attempt WITHOUT thinking off, same model: `chat/spark:reply` 51.7 s
and `trip/plan:consider/dest` 97.1 s, both with content `""` and finish
`length` — the model spent the whole `max_tokens` on `reasoning_content`.
That run was discarded (store reset to the committed state) before the
real one; the numbers are here because they are the cost of a thinking
model behind this adapter without the switch.
