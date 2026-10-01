"""The Spark's git relay (`handoff/spark-relay.sh`), on one machine.

`SPARK=local` makes the "Spark" a directory here instead of an ssh host,
so every case below runs offline in temp repos: a bare `origin`, the keyed
box's clone (`air`), and the Spark's checkout (`spark`). The cases are the
ways a relay round meets real history: the Spark behind, the Spark with
commits of its own (relayed or not), mid-edit, on another branch, or in
conflict with what landed on origin meanwhile. The rule throughout: no
commit is ever left only in a reflog, and a round that cannot proceed
safely changes nothing and says so."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

RELAY = Path(__file__).resolve().parents[3] / "handoff" / "spark-relay.sh"
BRANCH = "shared"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")


def _env(tmp: Path) -> dict:
    who = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return {**os.environ, **who, "HOME": str(tmp), "GIT_CONFIG_NOSYSTEM": "1"}


class World:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.env = _env(tmp)
        self.origin = tmp / "origin.git"
        self.air = tmp / "air"
        self.cloud = tmp / "cloud"
        self.spark = tmp / "spark"
        self.git(tmp, "init", "-q", "--bare", "-b", BRANCH, str(self.origin))
        self.git(tmp, "clone", "-q", str(self.origin), str(self.cloud))
        self.write(self.cloud, "README", "one\n")
        self.commit(self.cloud, "first")
        self.git(self.cloud, "push", "-q", "origin", f"HEAD:{BRANCH}")
        self.git(tmp, "clone", "-q", "-b", BRANCH, str(self.origin), str(self.air))
        self.git(tmp, "init", "-q", "-b", "main", str(self.spark))

    def git(self, cwd: Path, *args: str, check: bool = True) -> str:
        p = subprocess.run(["git", *args], cwd=cwd, env=self.env, text=True, capture_output=True, timeout=60)
        if check and p.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} in {cwd.name}: {p.stderr}")
        return p.stdout.strip()

    def relay(self, direction: str, *args: str) -> subprocess.CompletedProcess:
        env = {**self.env, "SPARK": "local", "SPARK_DIR": str(self.spark), "BRANCH": BRANCH, "REPO_DIR": str(self.air)}
        return subprocess.run(["bash", str(RELAY), direction, *args], cwd=self.air, env=env, text=True, capture_output=True, timeout=60)

    def write(self, repo: Path, name: str, text: str) -> None:
        (repo / name).write_text(text)

    def commit(self, repo: Path, msg: str) -> str:
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", msg)
        return self.head(repo)

    def head(self, repo: Path, ref: str = "HEAD") -> str:
        return self.git(repo, "rev-parse", ref)

    def is_ancestor(self, repo: Path, maybe: str, of: str) -> bool:
        return subprocess.run(["git", "merge-base", "--is-ancestor", maybe, of], cwd=repo, env=self.env, capture_output=True).returncode == 0

    def subjects(self, repo: Path, ref: str = "HEAD") -> list:
        return self.git(repo, "log", "--format=%s", ref).splitlines()

    def cloud_pushes(self, name: str, text: str, msg: str) -> None:
        self.git(self.cloud, "pull", "-q", "--rebase", "origin", BRANCH)
        self.write(self.cloud, name, text)
        self.commit(self.cloud, msg)
        self.git(self.cloud, "push", "-q", "origin", f"HEAD:{BRANCH}")

    def origin_subjects(self) -> list:
        return self.git(self.origin, "log", "--format=%s", BRANCH).splitlines()


@pytest.fixture
def w(tmp_path: Path) -> World:
    world = World(tmp_path)
    first = world.relay("down")
    assert first.returncode == 0, first.stderr
    return world


def test_down_into_a_fresh_checkout_creates_the_branch(w: World):
    assert w.git(w.spark, "symbolic-ref", "--short", "HEAD") == BRANCH
    assert w.head(w.spark) == w.head(w.origin, BRANCH)


def test_down_fast_forwards_a_spark_that_is_behind_and_keeps_its_unrelated_edit(w: World):
    w.cloud_pushes("cloud.txt", "c\n", "cloud work")
    w.git(w.air, "pull", "-q", "--rebase", "origin", BRANCH)
    w.write(w.spark, "README", "one\nspark is editing\n")  # uncommitted, does not overlap cloud.txt
    r = w.relay("down")
    assert r.returncode == 0, r.stderr
    assert w.head(w.spark) == w.head(w.origin, BRANCH)
    assert "spark is editing" in (w.spark / "README").read_text()


def test_up_then_down_leaves_no_duplicate_of_a_relayed_commit(w: World):
    w.write(w.spark, "s1.txt", "s1\n")
    w.commit(w.spark, "spark one")
    w.cloud_pushes("cloud.txt", "c\n", "cloud work")  # origin moves, so up must rebase spark one
    assert w.relay("up").returncode == 0
    r = w.relay("down")
    assert r.returncode == 0, r.stderr
    assert w.head(w.spark) == w.head(w.origin, BRANCH)
    assert w.subjects(w.spark).count("spark one") == 1


def test_down_keeps_a_spark_commit_made_after_up(w: World):
    w.write(w.spark, "s1.txt", "s1\n")
    w.commit(w.spark, "spark one")
    w.cloud_pushes("cloud.txt", "c\n", "cloud work")
    assert w.relay("up").returncode == 0
    w.write(w.spark, "s2.txt", "s2\n")
    w.commit(w.spark, "spark two")  # lands between up and down: the stranding case
    r = w.relay("down")
    assert r.returncode == 0, r.stderr
    subjects = w.subjects(w.spark)
    assert subjects[0] == "spark two" and subjects.count("spark one") == 1 and "cloud work" in subjects
    assert w.is_ancestor(w.spark, "inbox", "HEAD")


def test_down_leaves_a_spark_with_its_own_commits_and_uncommitted_edits_alone(w: World):
    w.cloud_pushes("cloud.txt", "c\n", "cloud work")
    w.git(w.air, "pull", "-q", "--rebase", "origin", BRANCH)
    w.write(w.spark, "s2.txt", "s2\n")
    before = w.commit(w.spark, "spark two")
    w.write(w.spark, "README", "one\nmid-edit\n")
    r = w.relay("down")
    assert r.returncode == 3
    assert "left alone" in r.stderr
    assert w.head(w.spark) == before
    assert "mid-edit" in (w.spark / "README").read_text()


def test_down_refuses_to_switch_a_spark_that_is_on_another_branch(w: World):
    w.git(w.spark, "checkout", "-q", "-b", "experiment")
    r = w.relay("down")
    assert r.returncode == 3
    assert w.git(w.spark, "symbolic-ref", "--short", "HEAD") == "experiment"


def test_down_aborts_a_conflicting_rebase_and_changes_nothing(w: World):
    w.cloud_pushes("README", "cloud's line\n", "cloud edits readme")
    w.git(w.air, "pull", "-q", "--rebase", "origin", BRANCH)
    w.write(w.spark, "README", "spark's line\n")
    before = w.commit(w.spark, "spark edits readme")
    r = w.relay("down")
    assert r.returncode == 1
    assert "conflict" in r.stderr
    assert w.head(w.spark) == before
    assert not (w.spark / ".git" / "rebase-merge").exists() and not (w.spark / ".git" / "rebase-apply").exists()


def test_up_carries_spark_commits_when_the_keyed_box_is_already_ahead(w: World):
    w.cloud_pushes("cloud.txt", "c\n", "cloud work")
    w.git(w.air, "pull", "-q", "--rebase", "origin", BRANCH)  # the keyed box has cloud work; the Spark does not
    w.write(w.spark, "s1.txt", "s1\n")
    w.commit(w.spark, "spark one")
    r = w.relay("up")
    assert r.returncode == 0, r.stderr
    assert w.origin_subjects()[:2] == ["spark one", "cloud work"]
    assert w.head(w.air) == w.head(w.origin, BRANCH)


def test_up_with_nothing_new_on_the_spark_pushes_nothing(w: World):
    before = w.head(w.origin, BRANCH)
    r = w.relay("up")
    assert r.returncode == 0, r.stderr
    assert w.head(w.origin, BRANCH) == before


def test_up_stops_on_a_conflict_and_leaves_the_keyed_box_clean(w: World):
    w.cloud_pushes("README", "cloud's line\n", "cloud edits readme")
    w.write(w.spark, "README", "spark's line\n")
    w.commit(w.spark, "spark edits readme")
    before = w.head(w.origin, BRANCH)
    r = w.relay("up")
    assert r.returncode == 1
    assert "conflict" in r.stderr
    assert w.head(w.origin, BRANCH) == before
    assert w.git(w.air, "symbolic-ref", "--short", "HEAD") == BRANCH
    assert w.git(w.air, "status", "--porcelain") == ""
    assert not (w.air / ".git" / "rebase-merge").exists() and not (w.air / ".git" / "rebase-apply").exists()
    assert "relay-spark" not in w.git(w.air, "branch", "--list")


def test_up_refuses_a_keyed_box_checkout_with_uncommitted_changes(w: World):
    w.write(w.spark, "s1.txt", "s1\n")
    w.commit(w.spark, "spark one")
    w.write(w.air, "README", "one\nsomeone is editing the keyed box's checkout\n")
    before = w.head(w.origin, BRANCH)
    r = w.relay("up")
    assert r.returncode == 3
    assert "uncommitted" in r.stderr
    assert w.head(w.origin, BRANCH) == before
    assert "someone is editing" in (w.air / "README").read_text()
    assert "relay-spark" not in w.git(w.air, "branch", "--list")


def test_a_conflict_resolved_on_the_keyed_box_is_not_carried_again(w: World):
    """A private repo: the Spark cannot pull, so a conflict is resolved on the
    keyed box. The resolved commit's patch differs from the Spark's, so
    patch-id cannot tell they are the same change; `carried <sha>` records
    it by identity, and neither `up` nor `down` replays it again."""
    w.cloud_pushes("README", "cloud's line\n", "cloud edits readme")
    w.write(w.spark, "README", "spark's line\n")
    spark_tip = w.commit(w.spark, "spark edits readme")
    assert w.relay("up").returncode == 1
    # the operator resolves by hand on the keyed box and pushes
    w.git(w.air, "pull", "-q", "--rebase", "origin", BRANCH)
    w.git(w.air, "fetch", "-q", str(w.spark), BRANCH)
    w.git(w.air, "cherry-pick", "FETCH_HEAD", check=False)
    w.write(w.air, "README", "cloud's line\nspark's line\n")
    w.git(w.air, "add", "README")
    w.git(w.air, "-c", "core.editor=true", "cherry-pick", "--continue")
    w.git(w.air, "push", "-q", "origin", f"HEAD:{BRANCH}")
    resolved = w.head(w.origin, BRANCH)
    marked = w.relay("carried", spark_tip)
    assert marked.returncode == 0, marked.stderr
    up = w.relay("up")
    assert up.returncode == 0, up.stderr
    assert w.head(w.origin, BRANCH) == resolved  # nothing carried twice
    down = w.relay("down")
    assert down.returncode == 0, down.stderr
    assert w.head(w.spark) == resolved
    assert (w.spark / "README").read_text() == "cloud's line\nspark's line\n"


def test_down_moves_a_mid_edit_spark_whose_commits_were_all_carried(w: World):
    """The common case once a runner is on a timer: the Spark committed, `up`
    carried it onto a moved origin (a new hash), and the Spark is already
    editing again. Its commits are all upstream, so `down` moves it and
    keeps the edit, instead of leaving it behind until the tree is clean."""
    w.write(w.spark, "s1.txt", "s1\n")
    w.commit(w.spark, "spark one")
    w.cloud_pushes("cloud.txt", "c\n", "cloud work")
    assert w.relay("up").returncode == 0
    w.write(w.spark, "README", "one\nspark is editing again\n")
    r = w.relay("down")
    assert r.returncode == 0, r.stderr
    assert w.head(w.spark) == w.head(w.origin, BRANCH)
    assert w.subjects(w.spark).count("spark one") == 1
    assert "spark is editing again" in (w.spark / "README").read_text()

