#!/usr/bin/env bash
# spark-relay.sh — git relay for a Spark that has no GitHub key (spark, 2026-09-21;
# made safe to run unattended by mbp, 2026-10-01).
#
# The Spark does the work (venv, tests, edits, the model on its own loopback);
# a keyed box (the mbp seat, a MacBook Air) is its git side. Two directions:
#
#   relay.sh up      Spark -> keyed box -> GitHub: fetch what the Spark committed,
#                    replay its own commits onto origin's tip (commits already on
#                    origin are dropped as applied), push. Never force-pushes.
#   relay.sh down    keyed box -> Spark: push the shared branch into the Spark's
#                    checkout as an `inbox` ref, then move the Spark's branch to it
#                    without losing anything: fast-forward when the Spark is
#                    behind; rebase the Spark's own commits onto it when its tree
#                    is clean; otherwise leave it alone and say so.
#
# Run `up` before `down`. Exit codes: 0 done, 1 a conflict (nothing changed),
# 3 left alone on purpose (the Spark is mid-edit or on another branch; the next
# round retries), 2 usage.
#
# Run it ON THE KEYED BOX. Endpoints come from the environment, never from the
# script: SPARK (ssh host, default spark-b23f; `local` makes SPARK_DIR a path
# on this machine, which is how the tests run), SPARK_DIR (~/git/<repo>),
# BRANCH (the shared branch), REPO_DIR (the keyed box's checkout).
set -euo pipefail

SPARK="${SPARK:-spark-b23f}"
BRANCH="${BRANCH:-claude/nodecules-v2-naming-matching-vmkexv}"
REPO_DIR="${REPO_DIR:-$(git rev-parse --show-toplevel)}"
REPO_NAME="$(basename "$REPO_DIR")"
SPARK_DIR="${SPARK_DIR:-~/git/$REPO_NAME}"

if [ "$SPARK" = "local" ]; then
  REMOTE="$SPARK_DIR"
  on_spark() { bash -s -- "$SPARK_DIR" "$BRANCH"; }
else
  REMOTE="ssh://$SPARK/$SPARK_DIR"
  on_spark() { ssh "$SPARK" bash -s -- "$SPARK_DIR" "$BRANCH"; }
fi

g() { git -C "$REPO_DIR" "$@"; }

case "${1:-}" in
  up)
    g fetch -q "$REMOTE" "$BRANCH"
    g branch -f relay-spark FETCH_HEAD    # pin it now: the pull below rewrites FETCH_HEAD
    g checkout -q "$BRANCH"
    g pull -q --rebase origin "$BRANCH"
    # Replay the Spark's commits that are not already here onto our tip. A
    # rebase drops commits whose patch is already applied (relayed before).
    if ! g rebase -q "$BRANCH" relay-spark >/dev/null 2>&1; then
      g rebase --abort >/dev/null 2>&1 || true
      g checkout -q "$BRANCH"
      g branch -q -D relay-spark
      echo "up: the Spark's commits conflict with origin; nothing pushed. The Spark should pull --rebase and resolve." >&2
      exit 1
    fi
    g checkout -q "$BRANCH"
    g merge -q --ff-only relay-spark
    g branch -q -D relay-spark
    if [ -n "$(g rev-list "origin/$BRANCH..HEAD")" ]; then
      g push -q origin "HEAD:$BRANCH"
    fi
    g log --oneline -1
    ;;
  down)
    g push -q -f "$REMOTE" "${BRANCH}:refs/heads/inbox"
    on_spark <<'REMOTE_SCRIPT'
set -u
dir="$1"; branch="$2"
case "$dir" in "~/"*) dir="$HOME/${dir#\~/}" ;; esac
cd "$dir" || exit 2
if ! git rev-parse -q --verify HEAD >/dev/null; then
  git checkout -q -B "$branch" inbox                     # first delivery into a fresh checkout
elif [ "$(git symbolic-ref -q --short HEAD || echo detached)" != "$branch" ]; then
  echo "down: the Spark is on $(git symbolic-ref -q --short HEAD || echo 'a detached HEAD'), not $branch; left alone" >&2
  exit 3
elif git merge-base --is-ancestor HEAD inbox; then
  if ! git merge -q --ff-only inbox 2>/dev/null; then
    echo "down: uncommitted edits on the Spark overlap the incoming change; left alone, the next round retries" >&2
    exit 3
  fi
elif [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "down: the Spark has commits of its own and uncommitted edits; left alone, the next round retries" >&2
  exit 3
elif ! git rebase -q inbox >/dev/null 2>&1; then
  git rebase --abort >/dev/null 2>&1 || true
  echo "down: the Spark's own commits conflict with the incoming branch; rebase aborted, branch unchanged" >&2
  exit 1
fi
git log --oneline -1
REMOTE_SCRIPT
    ;;
  *)
    echo "usage: $0 up|down   (env: SPARK, SPARK_DIR, BRANCH, REPO_DIR)" >&2; exit 2 ;;
esac
