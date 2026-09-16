#!/usr/bin/env python3
"""Passed notes between the sessions working on nodecules. Standard
library only; see `handoff/README.md` for the protocol this enforces.

    python3 handoff/note.py new --from cloud --to spark --kind ask --re "first round" < body.md
    python3 handoff/note.py list [--to spark] [--all]
    python3 handoff/note.py show 0001
    python3 handoff/note.py done 0001 --by spark [--note "one line on how it ended"]

A note is a markdown file `inbox/<to>/NNNN-slug.md` with YAML-ish
frontmatter. The sender never edits a note after writing it; the
recipient flips `status` to `done` and moves it to `done/`. Schedule
notes (`--kind schedule --every 6h [--until DATE]`) stay open until their
`until` date or until someone closes them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

KINDS = ("ask", "report", "question", "schedule", "handoff")
PARTIES = ("cloud", "spark", "laptop", "founder")
ROOT = Path(__file__).resolve().parent


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "note"


def _all_notes(root: Path) -> List[Path]:
    out: List[Path] = []
    for d in (root / "inbox", root / "done"):
        if d.exists():
            out.extend(p for p in d.rglob("[0-9][0-9][0-9][0-9]-*.md"))
    return sorted(out, key=lambda p: p.name)


def _next_id(root: Path) -> str:
    ids = [int(p.name[:4]) for p in _all_notes(root)]
    return f"{(max(ids) + 1) if ids else 1:04d}"


def parse(path: Path) -> Tuple[Dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def render(meta: Dict[str, str], body: str) -> str:
    front = "\n".join(f"{k}: {v}" for k, v in meta.items() if v != "")
    return f"---\n{front}\n---\n\n{body.rstrip()}\n"


def cmd_new(a: argparse.Namespace) -> int:
    root = Path(a.root)
    body = a.body if a.body is not None else sys.stdin.read()
    if not body.strip():
        print("a note needs a body (stdin or --body)", file=sys.stderr)
        return 2
    nid = _next_id(root)
    meta = {
        "id": nid,
        "from": a.sender,
        "to": a.to,
        "date": a.date or dt.date.today().isoformat(),
        "kind": a.kind,
        "re": a.re,
        "status": "open",
        "reply-to": a.reply_to or "",
        "every": a.every or "",
        "until": a.until or "",
    }
    if a.kind == "schedule" and not a.every:
        print("a schedule note needs --every (e.g. 6h, 1d, mon)", file=sys.stderr)
        return 2
    path = root / "inbox" / a.to / f"{nid}-{_slug(a.re)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(meta, body), encoding="utf-8")
    print(path.relative_to(root.parent) if root.parent in path.parents else path)
    return 0


def _find(root: Path, nid: str) -> Optional[Path]:
    for p in _all_notes(root):
        if p.name.startswith(f"{nid}-"):
            return p
    return None


def cmd_list(a: argparse.Namespace) -> int:
    root = Path(a.root)
    rows = []
    for p in _all_notes(root):
        meta, _ = parse(p)
        if not a.all and meta.get("status", "open") != "open":
            continue
        if a.to and meta.get("to") != a.to:
            continue
        sched = f" every {meta['every']}" + (f" until {meta['until']}" if meta.get("until") else "") if meta.get("every") else ""
        rows.append(f"{meta.get('id', p.name[:4])}  {meta.get('status', '?'):5}  {meta.get('from', '?'):7}-> {meta.get('to', '?'):7} {meta.get('kind', '?'):8} {meta.get('date', '')}  {meta.get('re', p.stem)}{sched}")
    print("\n".join(rows) if rows else "(no open notes)")
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    p = _find(Path(a.root), a.id)
    if p is None:
        print(f"no note {a.id}", file=sys.stderr)
        return 1
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_done(a: argparse.Namespace) -> int:
    root = Path(a.root)
    p = _find(root, a.id)
    if p is None:
        print(f"no note {a.id}", file=sys.stderr)
        return 1
    meta, body = parse(p)
    if meta.get("status") == "done":
        print(f"{a.id} is already done ({p})")
        return 0
    meta["status"] = "done"
    meta["closed"] = f"{a.date or dt.date.today().isoformat()} by {a.by}" + (f": {a.note}" if a.note else "")
    dest = root / "done" / p.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(meta, body), encoding="utf-8")
    if dest != p:
        os.remove(p)
    print(dest.relative_to(root.parent) if root.parent in dest.parents else dest)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT), help="the handoff directory (default: where this script lives)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--from", dest="sender", required=True, choices=PARTIES)
    n.add_argument("--to", required=True, choices=PARTIES)
    n.add_argument("--kind", required=True, choices=KINDS)
    n.add_argument("--re", required=True, help="subject, one line")
    n.add_argument("--reply-to", help="id of the note this answers")
    n.add_argument("--every", help="schedule notes: cadence, e.g. 6h, 1d, mon")
    n.add_argument("--until", help="schedule notes: last date, YYYY-MM-DD")
    n.add_argument("--date", help="override today's date (tests)")
    n.add_argument("--body", help="body text; otherwise read from stdin")
    n.set_defaults(fn=cmd_new)
    ls = sub.add_parser("list")
    ls.add_argument("--to", choices=PARTIES)
    ls.add_argument("--all", action="store_true", help="include done notes")
    ls.set_defaults(fn=cmd_list)
    sh = sub.add_parser("show")
    sh.add_argument("id")
    sh.set_defaults(fn=cmd_show)
    d = sub.add_parser("done")
    d.add_argument("id")
    d.add_argument("--by", required=True, choices=PARTIES)
    d.add_argument("--note", help="one line on how it ended")
    d.add_argument("--date")
    d.set_defaults(fn=cmd_done)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
