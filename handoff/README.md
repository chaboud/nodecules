# handoff/ — how two Claude Code sessions work on nodecules together

Three parties work on this branch from different machines:

| party | where | has | writes as |
|---|---|---|---|
| **cloud** | a container at claude.ai, fresh clone per session, no GPU, no model | the design queue, the vault, the spec | `cloud` |
| **spark** | a DGX Spark, CUDA, a local inference engine, persistent disk | real models, real numbers, a machine that stays up | `spark` |
| **laptop** | a MacBook Air | stenota's real weights (`stenota/HARDWARE-TODO.md`) | `laptop` |

The founder reads everything and decides. `SPARK.md` is the letter to the
Spark session; this file is the protocol both sessions follow.

## The one rule

**Everything passes through git on the shared branch**
(`claude/nodecules-v2-naming-matching-vmkexv`, in every repo). No side
channel: if it is not committed and pushed, the other side does not know
it. This keeps the founder in the loop for free and makes every exchange
replayable.

Two channels ride on it:

1. **Notes** in `inbox/<recipient>/`. Markdown, one file per note, for
   asks, reports, questions, and schedules. For people and models to read.
2. **Stores** in `stores/<exchange>/`. Content-addressed directories that
   `core/disk.py` reads and writes. Work travels as request nodes and comes
   back as productions with receipts (`core/deferral.py`). For the
   substrate to read.

## Layout

```
handoff/
  README.md            this protocol
  SPARK.md             the letter to the Spark session
  note.py              new / list / show / done for notes
  fulfil.py            fulfil pending requests in a store with a real provider
  seed_exchange.py     the cloud side's seeding of an exchange store; --verify checks the round trip
  inbox/spark/         notes for the Spark, open until it closes them
  inbox/cloud/         notes for the cloud session
  done/                closed notes (moved here by the recipient)
  stores/exchange-1/   the first exchange store: two requests that need a model
  results/             measurements and reports the Spark writes (markdown, dated)
```

## Notes

A note is `inbox/<to>/NNNN-slug.md`:

```
---
id: 0007
from: cloud
to: spark
date: 2026-09-18
kind: ask            ask | report | question | schedule | handoff
re: one line
status: open         open | done
reply-to: 0004       optional
every: 6h            schedule notes only
until: 2026-10-01    schedule notes only, optional
---

The body. Say what, why, and what "done" looks like.
```

Rules:

- **The sender never edits a note after pushing it.** Corrections are a
  new note with `reply-to`. Only the recipient changes `status`.
- **The recipient closes a note** with `note.py done NNNN --by <party>
  --note "how it ended"`, which flips the status and moves the file to
  `done/`. A reply, if one is owed, is a new note in the other inbox.
- **Numbering** is one sequence across both inboxes and `done/`. Pull
  before you write. If two notes end up with the same number, both
  survive (the slug differs); leave them.
- **Schedule notes** ask for a recurring action: "every 6h, run
  `fulfil.py --git` once on exchange-1." The recipient picks the
  mechanism (a cron entry, a systemd timer, `fulfil.py --loop`, a Claude
  Code `/loop`) and closes the note when `until` passes or the ask is
  withdrawn. A schedule note is the only kind that stays open on purpose.
- **A handoff note** says what you were in the middle of when you stopped,
  for whoever picks it up. Cloud sessions end without warning; write one
  before a long stretch, not after.

`python3 handoff/note.py list` shows what is open; `--to spark` narrows it.

## Stores as the transport

A store directory is safe to share through git because nothing in it is
edited in place:

- `bodies/`, `skeletons/`, `manifests/` are one JSON file per content
  hash. Two machines that write the same content write the same file.
- `heads/<scope>/` holds one **empty file per candidate head**. When two
  machines each moved a scope's head, a git merge leaves two files, and
  `Store.attach` merges them with the CRDT rules in `core/replica.py`,
  writes the merged head, and retires the ancestors. Tested:
  `test_a_store_directory_merged_by_git_attaches_to_one_head`.
- The only file that grows in place is the event log, so each writer has
  its own: `events-<party>.jsonl`. Never append to another party's.

The flow for one step that needs a model:

1. Cloud declares the graph and produces it with `defer=True` and no model.
   The generator writes `requests/<node>` and reports `pending`. Cloud
   commits the store directory and pushes.
2. Spark pulls, runs `fulfil.py --store ... --provider openai ... --by
   spark --git`. The worker loads the store, finds the requests whose
   realization it offers, cooks each with the real provider, and commits
   the answer, its envelope (`fulfilled_by: spark`), and the request's
   removal in one transaction. `--git` commits the directory and pushes.
3. Cloud pulls; its next `produce` is a cache hit whose receipt names the
   Spark. `seed_exchange.py --verify` is that check for exchange-1.

The same directory can carry requests in both directions: anything the
Spark cannot run (say, a realization only the cloud has) becomes a request
the cloud fulfils on its next turn.

## Git discipline

- `git pull --rebase` before you write anything; push right after you
  commit. Small commits, one concern each.
- Never rewrite pushed history on this branch. No force pushes, no amends
  of pushed commits.
- Merge conflicts should only ever happen in prose files both sides edit.
  The ownership table below is there so they do not.

| file or directory | owner | the other side |
|---|---|---|
| `STATUS.md`, `REFERENCE-MODEL.md`, `WHY-WHAT-HOW.md`, `CLAUDE.md`, the vault | cloud | proposes edits in a note, with the exact text |
| `backend/nodecules/core/*` | cloud, for the substrate's shape | Spark adds modules and fixes bugs it can reproduce, with tests, and says so in a report note; changes to an existing module's contract go through a note first |
| `handoff/results/*`, `handoff/inbox/cloud/*`, `handoff/stores/*` (answers) | spark | reads |
| `handoff/inbox/spark/*`, `handoff/stores/*` (requests) | cloud | reads |
| `stenota/HARDWARE-TODO.md`, `stenota/results/` | laptop and Spark | cloud adds items |
| `keyhole/*` | the keyhole agent | read only for everyone here |

## Looping and scheduling

Nothing here runs on its own. Each side arranges its own cadence:

- **Spark**: `fulfil.py --loop 300 --git` keeps a worker polling the
  exchange stores every five minutes, pulling first and pushing after.
  Or a schedule note's cadence via cron. It is the machine that stays up,
  so it is the natural place for anything periodic.
- **Cloud**: a daily Routine wakes the cloud session, which pulls, reads
  `inbox/cloud/`, acts, and pushes. Cloud sessions can also be poked by
  the founder at any time. The Routine's id and how to remove it are in
  the founder's chat, not here.
- **Both**: before ending a stretch of work, `note.py list` to see what
  is owed, and a handoff note if something is half done.

## What this is not

Not a transport, not a lock, not an identity layer. Git carries the
bytes, the store's merge rules make concurrent writes safe, and authors
on manifests are self-declared. Those are the gaps this mechanism is
meant to surface, and the results notes should say when one bites.
