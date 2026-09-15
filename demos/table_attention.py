"""A table with open collaboration at the edge and attention.

Three participants — Alice on her laptop, Bob on his phone, and an agent
called Butler — each on their own replica of one table. Everyone places
and edits cards freely; nobody waits for anybody. Attention is visible:
each participant's focus is an element with an expiry, so a presenter
can draw cursors, and someone who goes quiet fades out. Bob follows
Alice; the Butler follows Bob; nobody has to. Replicas sync and converge
hash for hash, including a concurrent edit of the same card, which merges
field by field.

Does not show: a real screen (frames are rendered as text), decisions
and presentations, the responsible adult, or a network (sync is a
function call between two stores in one process).

    cd backend && PYTHONPATH=. python3 ../demos/table_attention.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from common import text_frame  # noqa: E402

from nodecules.core.replica import sync, transfer  # noqa: E402
from nodecules.core.scene import Element  # noqa: E402
from nodecules.core.store import Node, Store  # noqa: E402
from nodecules.core.table import Focus, Participant, attend, follow, join, open_collaboration, present, renew, view_for  # noqa: E402

T = "table/kitchen"
MS = 10_000  # 100 ns ticks per millisecond on the table's timeline
TTL = 5_000 * MS


def card(name: str, region: str, text: str, t: int, **payload) -> Node:
    el = Element(present_at=t, expires_at=t + 3_600_000 * MS, region=region, payload={"text": text, **payload})
    return Node(id=name, kind="ui.card", scope=T, data=el.model_dump())


def put(store: Store, who: str, node: Node) -> None:
    tx = store.transaction(T, author=who)
    tx.put(node)
    tx.commit()


def show(store: Store, who: str, t: int) -> None:
    v = view_for(store, store.current(T), who, t, timeline="table")
    via = f" (following {v.following}, focus from {v.via})" if v.via else (f" (following {v.following}, who is away)" if v.following else "")
    print(f"--- {who}'s view at {t // MS} ms{via}")
    print(f"    focus: {v.focus.model_dump(exclude_none=True) if v.focus else None}")
    print("    " + text_frame(v.frame).replace("\n", "\n    "))
    print(f"    present: {present(store, store.current(T), t)}")


def main() -> None:
    open_collaboration()
    laptop = Store()
    laptop.set_resolution(T, "merge")
    for p in (Participant(id="alice", display="Alice"), Participant(id="bob", display="Bob"), Participant(id="butler", kind="agent", display="Butler")):
        join(laptop, T, p)
    put(laptop, "alice", card("card/plan", "main", "Plan the trip", 0))
    phone = Store()
    transfer(laptop, phone, T)
    phone.set_head(T, laptop.current(T))
    print("two replicas of one table, three participants\n")

    t = 1_000 * MS
    attend(laptop, T, "alice", Focus(region="main", element="card/plan"), now=t, ttl=TTL)
    attend(phone, T, "bob", Focus(region="side"), now=t, ttl=TTL)
    attend(phone, T, "butler", None, now=t, ttl=TTL)
    follow(phone, T, "bob", "alice", now=t, ttl=TTL)
    follow(phone, T, "butler", "bob", now=t, ttl=TTL)

    # concurrent edits of the same card on two replicas: alice moves it, bob retitles it
    put(laptop, "alice", card("card/plan", "main", "Plan the trip", 0, x=120, y=40))
    put(phone, "bob", card("card/plan", "main", "Plan the summer trip", 0))
    put(phone, "bob", card("card/list", "side", "Packing list", t))

    print("before sync: each replica has its own edits")
    show(laptop, "alice", 2_000 * MS)
    show(phone, "bob", 2_000 * MS)

    a, b = sync(laptop, phone, T)
    print(f"\nafter sync: heads equal = {a.content_hash() == b.content_hash()} · the card merged field by field: {laptop.get(a, 'card/plan').data['payload']}\n")
    show(laptop, "alice", 2_500 * MS)
    show(phone, "bob", 2_500 * MS)
    show(phone, "butler", 2_500 * MS)

    print("\nalice goes quiet; bob and the butler keep renewing")
    renew(phone, T, "bob", until=20_000 * MS)
    renew(phone, T, "butler", until=20_000 * MS)
    sync(laptop, phone, T)
    show(phone, "bob", 7_000 * MS)


if __name__ == "__main__":
    main()
