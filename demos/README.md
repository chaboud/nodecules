# demos/

Small, runnable programs that stand on the substrate (`backend/nodecules/core/`)
and show one capability each. They are consumers, not core: nothing in
`core/` imports them. Each runs in a plain Python environment with the
backend's dependencies (pydantic) and no database, and each says at the
top what it does *not* show.

Founder direction, 2026-09-13: get the substrate built to the point that
demos can be started here — a stenota remake, a toy keyhole, a chat with a
live configurable graph and observational abilities, a chat with a Lego
table to interact with, which, mixed with computer operability, gives a
"digital butler" fronting other systems.

```bash
cd backend && PYTHONPATH=. python3 ../demos/chat_live_graph.py
```

| demo | what it shows | status |
|---|---|---|
| `chat_live_graph.py` | a chat whose processing graph is nodes in the store: messages are a strip, the reply is produced through a recipe with a persona; the persona is **edited mid-session by a commit** and the next turn uses it; **every turn leaves a receipt** (realization, cache key, cache hit or recook, reproducibility) and history says who changed what | runs, with an echo provider; swap in any `ToolAwareProvider` |
| `table_attention.py` | **open collaboration at the edge, and attention** (founder: "think Figma; I don't have to follow someone, but I can"): three participants on two replicas place and edit cards freely, a concurrent edit of one card merges field by field, attention is visible and expires, following is optional and transitive, replicas converge hash for hash | runs |
| `table_chat.py` | a chat whose replies are presented on the table: decisions become presentations chosen for the surface, the person answers through an input strip, the business graph advances once per decision | not yet — needs the decision kind |
| `stenota_remake.py` | the meeting graph (audio → asr, diar → turns → windowed claims) through the store with receipts, on mock realizations here and real ones on a laptop | not yet — stenota `HARDWARE-TODO.md` H11 is the real-weights half |
| `toy_keyhole.py` | simulated observations appended to a strip on a tick; fast, slow, and inner loops as recipes; behavior templates as nodes with rollback | not yet — needs a tick loop and marker manifests |

| `inspector/` | **the inspector**: a local web app over a disk-backed store — scopes, history, the graph drawn from edges, every name with its state and residency, node data you can edit and commit, receipts and lineage, produce, evict and prune, mark and activate a version, set the write policy, append a message and get a reply, and the event log | runs: `python3 ../demos/inspector/server.py --demo` then open http://127.0.0.1:8765/ |

`common.py` holds what the demos share: an echo provider, a receipt
printer, and a text presenter for frames.
