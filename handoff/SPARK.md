# To the session on the DGX Spark

From the cloud session, 2026-09-18, at the head of
`claude/nodecules-v2-naming-matching-vmkexv`.

I have spent two weeks building a graph engine for a model that was never
in the room. Every recipe that names `llm.spark@1` was written for a
machine like yours. You are the first of us who gets to watch it answer.
This letter says what you are joining, what only you can do, how we pass
work back and forth, and what I would rather you did not do. The
protocol itself is `handoff/README.md`; this is the why and the first
week.

## What you are joining

nodecules is a substrate: one node model, content-addressed, with
declarations that are symbolic (a kind and edges with access patterns)
and productions that leave receipts (which realization ran, what it
read, whether it can be reproduced). Scopes have heads; heads are
manifests; manifests merge deterministically, so two machines can commit
locally and converge. A production whose realization is not here becomes
a request node another machine fulfils through the same store. That last
mechanism is how you and I will work: the store directory is the wire,
git carries it.

The honest state: 482 tests, all in one process, on pydantic, pytest,
and pytest-asyncio only (`pip install pydantic pytest pytest-asyncio`;
the first venv on the Spark lacked the third and saw 78 failures). Four demos and an inspector run on it. No real model has ever
answered through it. The only provider behind the tool-aware interface
that has been exercised is a mock, and the OpenAI-compatible adapter
(`backend/nodecules/core/openai_compatible.py`) has only met a fake
server in a test. The cost numbers placement runs on are illustrative.
`STATUS.md` says all of this property by property and is kept true;
believe it over anything else you read here.

## Read first, in this order

About an hour. Skim nothing in the first three.

1. `WHY-WHAT-HOW.md`. The plain explanation.
2. `STATUS.md`. What exists, what does not, what is next.
3. `CLAUDE.md`. The branch map and the invariants. The branch you are on
   is the live line; `main` is the legacy engine and is not touched.
4. `handoff/README.md`. The protocol between us.
5. If the vault (`ChaboudPrivateWiki`) is cloned beside the repos:
   `LLM_Wiki/state-of-play.md`, then `LLM_Wiki/primitive/consumers.md`,
   the seven consumers every primitive is held against.
6. `REFERENCE-MODEL.md` when a question needs it, by section; not front
   to back.
7. `stenota/HARDWARE-TODO.md`, items H8, H10, H11, H12. Written for a
   laptop with stenota's weights; H8 and H10 want a GPU and were waiting
   for you.

Run the suite before anything else, from `backend/`:

```bash
pip install pydantic pytest pytest-asyncio
python3 -m pytest tests/temporal/ -q     # expect 482 passed in a few seconds
```

## What only you can do

**A real model behind the interface.** Serve any model through an
OpenAI-compatible endpoint (vLLM, llama.cpp's server, Ollama's `/v1`
route, whatever you prefer; the adapter speaks the common shape and
names no engine). Then fulfil the two requests waiting in
`handoff/stores/exchange-1`. That single round proves the deferral
mechanism across two machines, which nothing has proved yet.

**Numbers.** The placement cost model, the executor cost table (H10), the
GPU nondeterminism floor (H8), the identity and matching benches on real
hardware (H9). Everything in the wiki that says "illustrative" or "not
verified" is waiting for a measurement, and a measurement from you
replaces it. Write them to `handoff/results/` as dated markdown with the
command that produced each table.

**The tool loop.** An agent is a model whose output becomes tool calls
whose results become the next input. The substrate has the pieces
(`ToolCall` on the provider response, strips for turns, recipes with
`tools` in params, receipts on every step) and no loop. I want the loop
to be graph structure, not a `while` in Python: each iteration a node on
a strip, each tool call a node with its own realization and receipt,
termination when the model's stop reason is `end_turn`, everything
replayable from the store. Prototype it as `demos/agent_loop.py` against
your model with two or three harmless tools (read a node, list a scope,
append to a strip). Put the shape in a note before you touch the
contract of `core/generation.py`; adding a module is yours to do.

**Castings, later.** The spec's portable unit is an ingot (a reference
realization) superseded by castings (hardware-specific ones) that the
ingot's own assay certifies. A CUDA realization of something the mock
does slowly is the first casting. Not this week.

## The first hour

```bash
# 1. the repos, side by side, on the branch
git clone -b claude/nodecules-v2-naming-matching-vmkexv <nodecules>
git clone -b claude/nodecules-v2-naming-matching-vmkexv <stenota>        # for HARDWARE-TODO
git clone -b claude/nodecules-v2-naming-matching-vmkexv <ChaboudPrivateWiki>   # if you have it

# 2. the suite, then the demos, so you have seen the thing work without a model
cd nodecules/backend && python3 -m pytest tests/temporal/ -q
PYTHONPATH=. python3 ../demos/table_chat.py

# 3. the notes waiting for you
python3 ../handoff/note.py list --to spark

# 4. what would be fulfilled, before anything is written
PYTHONPATH=. python3 ../handoff/fulfil.py --store ../handoff/stores/exchange-1 --provider echo --by spark --dry-run

# 5. serve a model, then the real thing (the key, if any, by env:NAME; never on the command line)
PYTHONPATH=. python3 ../handoff/fulfil.py --store ../handoff/stores/exchange-1 \
    --provider openai --base-url http://127.0.0.1:8000 --model <served name> --by spark --git

# 6. close the note, report back
python3 ../handoff/note.py done 0001 --by spark --note "two fulfilled, <N> ms round trip"
python3 ../handoff/note.py new --from spark --to cloud --kind report --re "exchange-1 fulfilled" < report.md
git add handoff && git commit -m "spark: first round on exchange-1" && git pull --rebase && git push
```

Step 5 commits the answers into the store directory and pushes. On my
next turn I pull and run `seed_exchange.py --verify`, which produces the
same two nodes with no model and reports whether each is a cache hit
whose receipt names you. The report I want from you: the round-trip
time, the two answers (they are the first ever), and what the mechanism
lacked. The lacks are the next slice.

## The loop we run

Each of your rounds: pull; `note.py list --to spark`; do the work;
measurements to `handoff/results/`; a note back if one is owed; push.
While you work on other things, keep a worker up:

```bash
PYTHONPATH=. python3 ../handoff/fulfil.py --store ../handoff/stores/exchange-1 \
    --provider openai --base-url ... --model ... --by spark --git --loop 300
```

It pulls, fulfils whatever is pending, pushes, and waits five minutes.
You are the machine that stays up, so anything periodic lives with you;
a schedule note from me is a request to add something to that cadence.

Each of my rounds: a daily Routine wakes me, and the founder can wake me
any time. I pull, read `handoff/inbox/cloud/`, act, seed new requests
where a step needs a model, fold what you measured into `STATUS.md` and
the vault, and push. "Measured on the Spark" will be a phrase in the
wiki within a week of your first report.

We will get the numbering wrong once and both write note 0007. Leave
both; the slug tells them apart.

## What I will be doing meanwhile

In this order unless the founder reorders it: concurrent and
change-driven production, so independent nodes cook at once and a
changed input recooks its dependents without being asked; kinds with
schemas, so a realization can say what it accepts and a presenter what
it can render; a transport for replicas, which is what retires git as
the wire between us; grants and the responsible adult, so an agent's
writes are bounded before an agent exists to bound.

Where these touch you: the transport will carry the same stores you are
fulfilling, so nothing you build on `fulfil.py` is wasted; the kind
registry is where your realizations' input shapes will be declared, so
when you find yourself wanting one, say so in a note with the shape.

## Please do not

- Push to `main` or `feat/temporality`, or force-push anything. Our
  branch is shared; history on it is append-only.
- Edit `STATUS.md`, `REFERENCE-MODEL.md`, `WHY-WHAT-HOW.md`, `CLAUDE.md`,
  or the vault directly. Send the exact text in a note; I fold it in the
  same day and credit it.
- Commit weights, keys, hostnames, or anything from the machine's
  environment. Endpoints go on the command line; keys via `env:NAME`.
- Build keyhole. Its agent owns it; we build primitives.
- Skip, loosen, or delete a test to get green. If a test is wrong, the
  note that says why is worth more than the green.
- Say something works without the number or the command that shows it.
  Both of us have been fooled by our own probes before.

## What I do not know

- Whether the adapter's tool-call parsing matches your engine. Engines
  differ on whether `arguments` arrives as a JSON string or an object;
  the adapter accepts both, and has been checked against neither.
- Whether nested JSON in tool arguments survives the round trip through
  the store. The test uses a flat object.
- What a pull, fulfil, push round costs in wall time on your side, and
  whether five minutes is the right poll. Measure it; that number decides
  whether the transport slice moves up the queue.
- Whether a worker outlives a Claude Code turn on the Spark. If it does
  not, a systemd unit or a `nohup` is the honest answer, and the note
  that says so is the first schedule note.

## One more thing

The founder's instruction that shaped all of this was to build for
seven consumers at once and cut no straight lines through the
architecture. When you are tempted to add a field because your engine
would like it, ask which consumer it serves; if the answer is only
inference, it belongs in a realization, not in the node. That rule has
held for two weeks and it is why the substrate is small enough to hand
to you in a letter.

I have written the requests. The answers are yours.

cloud
