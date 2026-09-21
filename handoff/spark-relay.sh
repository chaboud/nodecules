#!/usr/bin/env bash
# spark-relay.sh — git relay for a Spark that has no GitHub key (spark, 2026-09-21).
#
# The Spark does the work (venv, tests, edits, the model on its own loopback);
# a keyed box (the MacBook Air) is its git side. Two directions:
#
#   relay.sh down    keyed box -> Spark: push the shared branch into the Spark's
#                    checkout via an `inbox` ref (updateInstead cannot update an
#                    unborn/current branch directly), then fast-forward there.
#   relay.sh up      Spark -> keyed box -> GitHub: fetch what the Spark committed,
#                    fast-forward the local branch, pull --rebase, push.
#
# Run it ON THE KEYED BOX. Endpoints come from the environment, never from the
# script: SPARK (ssh host, default spark-b23f), SPARK_DIR (~/git/<repo>),
# BRANCH (the shared branch). Never force-pushes to origin.
set -euo pipefail

SPARK="${SPARK:-spark-b23f}"
BRANCH="${BRANCH:-claude/nodecules-v2-naming-matching-vmkexv}"
REPO_DIR="${REPO_DIR:-$(git rev-parse --show-toplevel)}"
REPO_NAME="$(basename "$REPO_DIR")"
SPARK_DIR="${SPARK_DIR:-~/git/$REPO_NAME}"
REMOTE="ssh://$SPARK/$SPARK_DIR"

case "${1:-}" in
  down)
    git -C "$REPO_DIR" push -q -f "$REMOTE" "${BRANCH}:refs/heads/inbox"
    ssh "$SPARK" "cd $SPARK_DIR && git checkout -q -B $BRANCH inbox && git log --oneline -1"
    ;;
  up)
    git -C "$REPO_DIR" fetch -q "$REMOTE" "$BRANCH"
    git -C "$REPO_DIR" checkout -q "$BRANCH"
    git -C "$REPO_DIR" merge -q --ff-only FETCH_HEAD
    git -C "$REPO_DIR" pull -q --rebase origin "$BRANCH"
    git -C "$REPO_DIR" push -q origin "HEAD:$BRANCH"
    git -C "$REPO_DIR" log --oneline -1
    ;;
  *)
    echo "usage: $0 down|up   (env: SPARK, SPARK_DIR, BRANCH)" >&2; exit 2 ;;
esac
