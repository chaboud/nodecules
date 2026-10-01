# The replica transport against the git relay, Air ↔ Spark

mbp, 2026-10-01. This answers note 0011: the round trip for one
fulfilment, measured both ways, and the floor of a polling loop. The
other side was the DGX Spark. This side was the MacBook Air (M4, 24 GB,
macOS 26.4.1, Python 3.14.6). The Spark ran Python 3.12.3.

The path between them is a VPN overlay interface (`utun1`), not a plain
LAN hop. Ping was 6.5 / 19.2 / 85.9 ms (min / avg / max, 20 packets) at
the time. On a path like that, every request is a round trip of about
20 ms, so the number of requests is the cost.

## The numbers that decide it

| one step | transport | git |
|---|---:|---:|
| one fulfilment, Spark → Air | **48 ms** (max 67) | 3.34 s (max 9.6) via relay `up` |
| one fulfilment, Air → Spark | **90 ms** (max 97) | 2.51 s (max 10.3) via push + relay `down`; 2.15 s (max 2.9) via push + the Spark's own pull |
| nothing new (the floor of a polling loop) | **17–19 ms** | 1.77 s (max 2.3) via a no-op `up` |

All figures are medians of 10. Per fulfilment the transport is 35 to 70
times faster than git, and at the polling floor about 100 times. Git's
tails run to 10 s, while the transport's worst case was under 100 ms.
For anything that moves stores, git should stop being the wire. It stays
the wire for notes, where seconds do not matter and the founder reads
everything.

## Transport (nodecules `c969df0`)

Measured at 12:39 PDT. Times are in ms, timed inside the pull; loading
the store from disk is excluded. "One fulfilment" is one manifest and
three bodies (a 2 KB answer, a 1 KB envelope, and a note), 5.7 KB on the
wire.

| case | median | min | max | n | manifests | bodies | requests | bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| one fulfilment, Spark → here (-L) | 47.9 | 43.5 | 67.4 | 10 | 1 | 3 | 3 | 5708 |
| one fulfilment, here → Spark (-R) | 89.9 | 75.3 | 97.3 | 10 | 1 | 3 | 3 | 5690 |
| no-op pull, Spark → here | 17.1 | 13.7 | 21.1 | 10 | 0 | 0 | 1 | 175 |
| no-op pull, here → Spark | 18.6 | 17.5 | 20.5 | 10 | 0 | 0 | 1 | 175 |
| cold pull of exchange-2, Spark → here | 107.8 | 89.6 | 130.6 | 10 | 11 | 16 | 7 | 22821 |
| cold pull, synthetic wide (50 scopes × 20 commits × 5 nodes) | 3514.3 | 3463.7 | 3633.9 | 3 | 1001 | 5003 | 23 | 6345581 |
| one fulfilment into synthetic wide | 80.7 | 76.2 | 82.2 | 3 | 1 | 3 | 3 | 16190 |
| no-op pull, synthetic wide | 49.9 | 47.7 | 137.3 | 3 | 0 | 0 | 1 | 4061 |
| cold pull, synthetic deep (1 scope × 500 commits × 1 node) | 9933.6 | 9686.3 | 10238.2 | 3 | 501 | 503 | 503 | 10186770 |
| one fulfilment into synthetic deep | 124.6 | 120.3 | 188.8 | 3 | 1 | 3 | 3 | 43462 |
| no-op pull, synthetic deep | 52.6 | 50.9 | 98.7 | 3 | 0 | 0 | 1 | 92 |

Synthetic wide: 13,050 files, 56 MB on the Spark's disk. Synthetic deep:
2,501 files, 19 MB.

## Git relay (nodecules `44d2859`; the transport changes after it do not touch git)

Measured at 12:34 PDT, on a scratch branch that was deleted afterwards.
The relay ran as the runner runs it: a fresh ssh connection per step,
`BatchMode`, and `ConnectTimeout=8`. 0 of 20 pushes failed. A smoke run
earlier the same day had one push fail with exit 128 (intermittent).

| case | median | min | max | n |
|---|---:|---:|---:|---:|
| one fulfilment, Spark → GitHub → here (relay `up`) | 3343.4 | 3133.9 | 9591.8 | 10 |
| one fulfilment, here → GitHub + relay `down` | 2511.0 | 2326.3 | 10322.4 | 10 |
| one fulfilment, here → GitHub, the Spark pulls itself | 2154.0 | 1993.6 | 2939.2 | 10 |
| no-op relay `up` | 1768.4 | 1495.8 | 2325.4 | 10 |
| part: push here → GitHub | 1655.0 | 1562.9 | 2233.3 | 20 |
| part: relay `down` alone | 758.0 | 718.3 | 8541.3 | 10 |
| part: the Spark's own pull from GitHub | 451.5 | 400.1 | 934.5 | 10 |

## What the measurement changed on the way

1. **Nagle's algorithm on the server: about 40 ms per batched request.**
   A response is two small writes, headers and then body. With Nagle on,
   the second write waited for an ACK that the client delays. Turning it
   off in `44d2859` gave these changes (smoke runs, n = 1 to 2):

   | case | before | after |
   |---|---:|---:|
   | one fulfilment, Spark → here | 131 ms | 51 ms |
   | cold pull of exchange-2 | 810 ms | 232 ms |
   | cold pull, synthetic wide | 55 s | 16 s |
   | cold pull, synthetic deep | 31.6 s | 10 s |

   The client was already fine: `http.client` sets `TCP_NODELAY`.
2. **One walk per scope cost one round trip per scope per depth.**
   Batching the frontiers of all scopes into one request per depth
   (`c969df0`) cut the wide cold pull from 16.3 s to 3.5 s (1,052
   requests to 23), and exchange-2's from 213 ms to 108 ms (15 requests
   to 7).

## What is still slow, and why

- **Deep history is one request per depth.** A 500-manifest chain takes
  503 requests and 9.9 s. Note 0009 said a server-side walk (git's
  have/want) would wait for a number. This is the number, and the walk is
  proposed in the report to cloud.
- **`DiskBacking.heads()` re-reads every manifest file on every call.**
  This is the recovery scan for scopes whose head files are missing. So
  the polling floor grows with the store: 17 ms on a small store, 50 ms
  at 1,000 manifests, 53 ms at 19 MB of manifests. That is about 33 ms of
  server work per heads call at that size.
- **Manifests are stored and sent whole.** Each manifest lists all of its
  scope's entries, so the disk holds names × versions. The deep store is
  19 MB for 500 small bodies, and one fulfilment into it moves 43 KB
  where it moves 5.7 KB in a small scope.
- **Where a cold pull's time goes** (a loopback profile on the Air, wide
  store): 2.5 s into an in-memory store and 5.1 s into a disk store.
  Writing 13,150 files through with fsync took 2.3 s. Decoding manifests
  (rebuilding each persistent map to check its root) took about 1.4 s
  across both ends. Over the LAN, the rest of the 16 s before batching
  was round trips.
- **The two directions are asymmetric: 90 ms Air → Spark against 48 ms
  Spark → Air.** In each direction the pulling side is the one timed, so
  the Spark-side client (Python 3.12) is the likely difference. Not
  investigated.

## Method

- Everything ran in scratch space. On the Spark that was a fresh HTTPS
  clone under `~/mbp-transport` (removed afterwards), run with the
  Spark's existing venv; its own checkout was never touched. On the Air
  it was a directory beside the repo on the case-sensitive volume: a
  clone in the system temp directory starts dirty, because
  `ARCHITECTURE.md` and `architecture.md` collide (CLAUDE.md, known
  traps).
- One ssh connection carried both tunnels (`-L 17801`, `-R 17802`), as
  a runner would hold it. Each server ran with `--until-stdin-closes`
  under its own ssh session, so it lived exactly as long as that session.
- Every body in a run names its scope. Content addressing otherwise
  dedupes equal data across scopes and machines, which made an earlier
  run move one body instead of three.

## Commands

```bash
# both tables (the git rows are from the first, the transport rows from the second)
cd backend && PYTHONPATH=. python3 ../handoff/bench_transport.py --reps 10 --big-reps 3
cd backend && PYTHONPATH=. python3 ../handoff/bench_transport.py --reps 10 --big-reps 3 --skip-git

# by hand: serve the Spark's exchange-2 for as long as the ssh session lives, then pull it here
ssh -N -L 17801:127.0.0.1:17801 spark-b23f &
ssh spark-b23f 'cd ~/git/nodecules && PYTHONPATH=backend backend/.venv/bin/python handoff/transport.py serve --store handoff/stores/exchange-2 --port 17801 --until-stdin-closes'
PYTHONPATH=backend python3 handoff/transport.py pull --store /tmp/ex2 --peer http://127.0.0.1:17801 --by mbp
```
