"""``--range A...B``: the merge-base form, which is what reviewing a branch means.

``git diff A...B`` is ``merge-base(A, B)..B`` -- what the branch did, without the commits the
base gathered meanwhile. It is what a pull request shows and what this project's own
documentation and agent skill told people to type, and it was refused by name until a session
driving the tool reported it.

The setups below build a real divergence, because that is the only shape where the two forms
disagree, and it is the shape every review is.
"""

from __future__ import annotations

from pathlib import Path

from conftest import MakeGitRepo
from test_explain_pipeline import make_harness

from scitools_hook.runner.explain import CommitRange


def test_a_three_dot_range_compares_from_the_merge_base(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``A...B`` is ``merge-base(A, B)..B``: what the branch did, not what main gathered.

    Built as a real divergence -- a commit on ``main`` after the branch left it -- because
    that is the only shape where the two forms disagree, and it is the shape every review is.
    """
    builder = git_repo()
    builder.write("pkg/base.py", "def one():\n    return 1\n")
    builder.stage()
    fork = builder.commit("base")
    builder.run("checkout", "-q", "-b", "feature")
    builder.write("pkg/feature.py", "def two():\n    return 2\n")
    builder.stage()
    builder.commit("on the branch")
    builder.run("checkout", "-q", "main")
    builder.write("pkg/elsewhere.py", "def three():\n    return 3\n")
    builder.stage()
    builder.commit("on main, meanwhile")

    harness = make_harness(builder, tmp_path, answers={})
    plan = harness.pipeline._plan_range(
        CommitRange(base="main", head="feature", from_merge_base=True), harness.repo, None
    )

    assert plan.before == fork, "the before side must be the fork point, not main's tip"
    assert "pkg/elsewhere.py" not in plan.files, "main's own commit is not the branch's doing"
    assert "pkg/feature.py" in plan.files


def test_the_two_dot_form_still_compares_the_two_tips(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``A..B`` is unchanged: whatever differs between the two commits, either side's doing."""
    builder = git_repo()
    builder.write("pkg/base.py", "def one():\n    return 1\n")
    builder.stage()
    builder.commit("base")
    builder.run("checkout", "-q", "-b", "feature")
    builder.write("pkg/feature.py", "def two():\n    return 2\n")
    builder.stage()
    builder.commit("on the branch")
    builder.run("checkout", "-q", "main")
    builder.write("pkg/elsewhere.py", "def three():\n    return 3\n")
    builder.stage()
    tip = builder.commit("on main, meanwhile")

    harness = make_harness(builder, tmp_path, answers={})
    plan = harness.pipeline._plan_range(
        CommitRange(base="main", head="feature"), harness.repo, None
    )

    assert plan.before == tip
    assert "pkg/elsewhere.py" in plan.files, "the two tips differ by main's commit too"
