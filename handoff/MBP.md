# To the session holding the mbp seat

From the cloud session, 2026-09-29, at the head of
`claude/nodecules-v2-naming-matching-vmkexv`. Addressed to a MacBook Pro
when written; the seat turned out to be a Claude Code session on the
founder's MacBook Air (note 0008, 2026-10-01). `mbp` is the seat's name
in the notes, not the hardware; nothing else here changes.

You are the third builder on nodecules and the second one on a real
machine. This letter says what is yours. What you are joining, the
reading list, the rules, and the loop we run are in `SPARK.md` (written
for the Spark session, and true for you too) and `README.md` (the
protocol). Read those first; this one is short because they exist.

## What you are

A Claude Code instance on a Mac, working on nodecules only, with a
GitHub key and a shell on the same LAN as the DGX Spark. Those
two facts decide your two jobs.

## Job one: the Spark's git side

The Spark has no GitHub key (founder's choice) and the box that used to
relay for it is gone from nodecules. `handoff/spark-relay.sh` is the
relay: run on a keyed box, `down` pushes the shared branch into the
Spark's checkout through an `inbox` ref and fast-forwards it there; `up`
fetches what the Spark committed, fast-forwards, rebases on origin, and
pushes. It never force-pushes to origin. Endpoints come from the
environment (`SPARK`, `SPARK_DIR`, `BRANCH`), never from the script.

Run `up` and then `down`, in that order, at the start and at the end
of every round you run. `up` first collects whatever the Spark committed
since last time and pushes it to origin; `down` then hands the Spark the
merged branch. The other order resets the Spark's branch and strands its
unrelayed commits (they stay in its reflog, where nobody looks). The
script defaults to the host alias `spark-b23f` and `~/git/<repo>` on
the Spark; override with `SPARK` and `SPARK_DIR` if yours differ. Run it
from each repo you relay: nodecules always, the vault when it changed.

```bash
cd nodecules
./handoff/spark-relay.sh up && ./handoff/spark-relay.sh down
# ... your round ...
./handoff/spark-relay.sh up && ./handoff/spark-relay.sh down
```

If ssh to the Spark does not work from your box, say so in a note to
`cloud` the same day; that is the founder's to fix and it blocks the
Spark's every round. A deploy key on the Spark retires this job; until
then it is yours.

## Job two: the transport for replicas

The substrate's stores converge hash for hash (`core/replica.py`:
manifests as a DAG, a deterministic merge, `sync` and `transfer` that
move only what the other side lacks). All of that runs in one process.
Between machines the wire is git, which is fine for notes and wrong for
stores: it needs a human or a relay, it is slow, and the Spark cannot
even reach it.

Build the wire. The shape I would start from, which you may change with
a note saying why:

- A store exposes itself over HTTP on the LAN, standard library only
  (the inspector in `demos/inspector/server.py` shows the pattern): the
  candidate heads per scope, a manifest by hash, a body by hash, and a
  skeleton by hash. Everything is content-addressed, so integrity is a
  hash of the bytes and nothing else.
- Sync is **pull**. A store asks a peer for its heads, walks the
  manifest DAG it does not have, fetches bodies it wants, then merges
  with the same rules `attach` uses for git-merged directories. Nothing
  pushes into another store uninvited; head acceptance stays local,
  which is what the vault's authority models require.
- No auth and no encryption in this slice, on a LAN, and the module
  says so at the top. The signed-manifest and grants slices are
  separate.
- It lives in `core/transport.py` with tests that run two servers on
  loopback in one process (`test_openai_compatible.py` has the fixture
  shape). `handoff/` gets a small CLI: serve a store; sync a store from
  a peer once or on a loop. `fulfil.py` should then be able to take a
  peer URL instead of `--git`.
- Measure it: the round trip for an exchange-2-sized store between your
  box and the Spark, against the git relay's. Write the numbers to
  `handoff/results/` with the commands. That number is what decides
  whether git stays the wire for anything but notes.

Before you touch the contract of `core/replica.py` or `core/store.py`,
put the shape in a note to `cloud`. Adding `core/transport.py` and its
tests is yours to do without asking.

## First hour

```bash
git clone -b claude/nodecules-v2-naming-matching-vmkexv <nodecules>
git clone -b claude/nodecules-v2-naming-matching-vmkexv <ChaboudPrivateWiki>   # if you have it
cd nodecules/backend && pip install pydantic pytest pytest-asyncio
python3 -m pytest tests/temporal/ -q            # expect 487 passed
python3 ../handoff/note.py list                 # what is open, and for whom
python3 ../handoff/note.py list --to mbp
../handoff/spark-relay.sh up && ../handoff/spark-relay.sh down   # the Spark's commits out, the branch in; report if this fails
```

Then read `WHY-WHAT-HOW.md`, `STATUS.md`, `CLAUDE.md`, and the
"Distributable" and "Multi-user" rows of `STATUS.md` twice: they are
what your slice changes.

## What is not yours

keyhole, which its own agent owns and nodecules instances never build
in. The vault, `STATUS.md`, `REFERENCE-MODEL.md`, `WHY-WHAT-HOW.md`, and
`CLAUDE.md`, which you propose edits to by note with the exact text.
The models on the Spark, which are keyhole's supervisor's and are never
stopped or restarted by us. Pushes to `main` or `feat/temporality`, and
force pushes anywhere.

## What I do not know

- Whether your box can ssh to the Spark today. The first relay run
  tells us.
- Whether the Spark's supervisor exposes anything on the LAN, or only on
  loopback; the transport slice needs the Spark to serve its store on an
  address you can reach, which may mean the Spark session runs the
  server and you tell each other the port by note.
- What a LAN round trip costs against the relay. Measure; do not
  estimate.

cloud
