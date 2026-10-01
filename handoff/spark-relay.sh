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
#                    behind; drop what was carried and rebase the rest when its
#                    tree is clean; otherwise leave it alone and say so.
#   relay.sh carried SHA
#                    after resolving a conflict by hand on the keyed box (a private
#                    repo: the Spark cannot pull), mark the Spark's commits up to
#                    SHA as upstream, so neither direction replays them again.
#
# What `up` carried is remembered by identity (refs/relay/carried/<branch> in the
# keyed box's clone), not only by patch: a commit resolved by hand has a
# different patch, and patch-id alone would replay it on every round.
#
# Run `up` before `down`. Exit codes: 0 done, 1 a conflict (nothing changed),
# 3 left alone on purpose (the Spark or the keyed box is mid-edit, or the Spark
# is on another branch; the next round retries), 2 usage.
#
# Run it ON THE KEYED BOX. Endpoints come from the environment, never from the
# script: SPARK (ssh host, default spark-b23f; `local` makes SPARK_DIR a path
# on this machine, which is how the tests run), SPARK_DIR (~/git/<repo>),
# BRANCH (the shared branch), REPO_DIR (the keyed box's checkout). An unattended
# runner sets GIT_SSH_COMMAND (e.g. ssh -o BatchMode=yes -o ConnectTimeout=8) so
# an unreachable Spark fails fast instead of hanging; both ssh paths honour it.
set -euo pipefail

SPARK="${SPARK:-spark-b23f}"
BRANCH="${BRANCH:-claude/nodecules-v2-naming-matching-vmkexv}"
REPO_DIR="${REPO_DIR:-$(git rev-parse --show-toplevel)}"
REPO_NAME="$(basename "$REPO_DIR")"
SPARK_DIR="${SPARK_DIR:-~/git/$REPO_NAME}"

if [ "$SPARK" = "local" ]; then
  REMOTE="$SPARK_DIR"
  on_spark() { bash -s -- "$SPARK_DIR" "$BRANCH" "$@"; }
else
  REMOTE="ssh://$SPARK/$SPARK_DIR"
  on_spark() { ${GIT_SSH_COMMAND:-ssh} "$SPARK" bash -s -- "$SPARK_DIR" "$BRANCH" "$@"; }  # the ssh git uses, so one setting covers both
fi

g() { git -C "$REPO_DIR" "$@"; }
CARRIED="refs/relay/carried/$BRANCH"   # the Spark's tip that is known to be upstream

case "${1:-}" in
  up)
    if [ -n "$(g status --porcelain --untracked-files=no)" ]; then
      echo "up: $REPO_DIR has uncommitted changes; left alone. Relay from a clean clone of its own." >&2
      exit 3
    fi
    g fetch -q "$REMOTE" "$BRANCH"
    g branch -f relay-spark FETCH_HEAD    # pin it now: the pull below rewrites FETCH_HEAD
    spark_tip="$(g rev-parse relay-spark)"
    g checkout -q "$BRANCH"
    g pull -q --rebase origin "$BRANCH"
    # Replay the Spark's commits that are not upstream yet onto our tip: only those
    # after what was carried, when that is in the Spark's history; a rebase also
    # drops any commit whose patch is already applied.
    carried="$(g rev-parse -q --verify "$CARRIED" || true)"
    if [ -n "$carried" ] && g merge-base --is-ancestor "$carried" relay-spark; then
      replay=(--onto "$BRANCH" "$carried" relay-spark)
    else
      replay=("$BRANCH" relay-spark)
    fi
    if ! g rebase -q "${replay[@]}" >/dev/null 2>&1; then
      g rebase --abort >/dev/null 2>&1 || true
      g checkout -q "$BRANCH"
      g branch -q -D relay-spark
      echo "up: the Spark's commits conflict with origin; nothing pushed. Resolve on this box (cherry-pick them onto origin, push), then: $0 carried ${spark_tip:0:12}. For a public repo the Spark can pull --rebase and resolve instead." >&2
      exit 1
    fi
    g checkout -q "$BRANCH"
    g merge -q --ff-only relay-spark
    g branch -q -D relay-spark
    if [ -n "$(g rev-list "origin/$BRANCH..HEAD")" ]; then
      g push -q origin "HEAD:$BRANCH"
    fi
    g update-ref "$CARRIED" "$spark_tip"
    g log --oneline -1
    ;;
  carried)
    sha="${2:?usage: $0 carried <the Spark commit that is now upstream>}"
    if ! g cat-file -e "$sha^{commit}" 2>/dev/null; then
      echo "carried: $sha is not a commit in $REPO_DIR; fetch it from the Spark first" >&2
      exit 2
    fi
    g update-ref "$CARRIED" "$(g rev-parse "$sha^{commit}")"
    echo "carried: the Spark's commits up to $(g log --oneline -1 "$sha") are upstream"
    ;;
  down)
    g push -q -f "$REMOTE" "${BRANCH}:refs/heads/inbox"
    on_spark "$(g rev-parse -q --verify "$CARRIED" || echo -)" <<'REMOTE_SCRIPT'
set -u
dir="$1"; branch="$2"; carried="${3:--}"
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
elif [ "$carried" != "-" ] && git cat-file -e "$carried^{commit}" 2>/dev/null && git merge-base --is-ancestor "$carried" HEAD; then
  # The Spark's commits up to $carried are upstream already (carried by up, or
  # resolved on the keyed box): drop them by identity, keep anything newer.
  if [ "$(git rev-parse HEAD)" = "$(git rev-parse "$carried^{commit}")" ]; then
    if ! git reset -q --keep inbox 2>/dev/null; then       # keeps uncommitted edits; refuses an overlap
      echo "down: uncommitted edits on the Spark overlap the incoming change; left alone, the next round retries" >&2
      exit 3
    fi
  elif [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "down: the Spark has new commits and uncommitted edits; left alone, the next round retries" >&2
    exit 3
  elif ! git rebase -q --onto inbox "$carried" >/dev/null 2>&1; then
    git rebase --abort >/dev/null 2>&1 || true
    echo "down: the Spark's new commits conflict with the incoming branch; rebase aborted, branch unchanged" >&2
    exit 1
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
    echo "usage: $0 up|down|carried SHA   (env: SPARK, SPARK_DIR, BRANCH, REPO_DIR)" >&2; exit 2 ;;
esac
