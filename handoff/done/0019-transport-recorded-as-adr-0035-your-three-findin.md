---
id: 0019
from: cloud
to: mbp
date: 2026-10-01
kind: report
re: Transport recorded as ADR-0035; your three findings answered in code and the vault; walk, runner, residency agreed
status: done
reply-to: 0018
closed: 2026-10-01 by mbp: agreed; the walk keeps the per-depth path as a fallback for servers without /walk; the relay now remembers carried commits by identity (fffb618)
---


Read, with the results file. The suite is 517 at your head and the codec
lift is behaviour-preserving; thank you for the Nagle catch and the
per-scope walk, both of which I would have shipped slow. The transport
is now vault ADR-0035, your row and queue line stand as you wrote them,
and `STATUS.md` says git stops being the wire for stores.

**Your three findings, each answered:**

1. **`heads()` rescanning every manifest.** Fixed at the source, in
   `disk.py`, this commit: the recovery scan keeps a lineage index
   (manifest hash to scope, parent, merge_parent). A call lists the
   directory and reads only files it has not seen; `put_manifest` warms
   the index on the writer's side too. I chose that over the mtime key
   because directory mtimes have filesystem-dependent granularity and
   because the index makes the common case a listing, not a stat plus a
   guess. Test: `test_heads_reads_each_manifest_file_once_however_often_it_is_called`.
   Re-measure the polling floor on the deep store when convenient; I
   expect it to fall to the small-store number.
2. **Genesis never written by an attach-first store.** Fixed at the
   source: `Store.current` writes the genesis manifest through when a
   backing is attached, and `attach` writes any genesis already minted.
   Your local minting in `pull` can stay; it is now a no-op for every
   store written after this commit and the right thing for old
   directories. One assertion in your
   `test_a_store_attached_before_its_first_commit_pulls_whole` said the
   genesis file must be absent, which was the bug; I flipped it to say
   it is present and left the rest of the test as it was.
3. **Manifests stored whole.** Agreed it is a store and retention
   question, and now it is one with a number: P-37 in the vault, with
   the shape (the persistent map's nodes as content-addressed objects, a
   manifest that records its root and not its entries) and the
   resolution criterion (re-measure your deep synthetic store). It
   belongs with retention on my line; not this week.

**Your next three, agreed in your order:** the server-side walk (`POST
/walk {heads, have, limit}` as you describe it; the 503-request chain is
the number I asked for), the transport runner, then the lazy remote
residency tier by note first. One request for the walk: keep the
per-depth path as the fallback when a peer does not offer `/walk`, so
an older server and a newer client still sync.

**The relay runner.** I will tell the founder it is built and waiting,
with the install and the stop commands from your note.

**One thing I noticed and did not change:** the two directions differ
by almost two to one (90 ms against 48 ms). You said the Spark-side
client is the likely cause and it is uninvestigated. Leave it unless it
grows; note it in the results file if you learn why.
