"""One command line, assembled into the adapters a pipeline runs on.

``check``, ``explain`` and ``baseline`` ask different questions of Understand, but they reach
it the same way: a repository, a cache keyed on that repository, a shadow synchroniser, a
database manager and a snapshot extractor, all built from the settings
:func:`~scitools_hook.runner.context.build_context` merged. That assembly lives here once, so
the three commands hold their option grammar and nothing else, and so a fourth command --
or a test -- substitutes one seam rather than three.

Two decisions in this module are load-bearing.

**The repository is required before any Understand work starts.** Requirement 12.5 says a
command that needs git stops with the not-a-git-repository code when there is no working
tree, and that promise must not depend on whether Understand happens to be installed:
``build_context`` locates and *verifies* an installation -- running ``und version`` and
pinging the API in a child process -- before anything asks it for a repository, so a run
started outside a working tree on a machine with no Understand would answer "no Understand"
(exit 3) to a question that is really "you are not in a repository" (exit 6). The check is
therefore made first, with :meth:`~scitools_hook.git.repo.GitRepo.discover`, which raises the
located refusal itself rather than restating one. The price is one extra ``git rev-parse``
per run -- a few milliseconds against the seconds a locator probe costs -- and, under
``--verbose``, that call appearing twice in the command log, because ``build_context``
discovers the repository again for its own purposes. Both are deliberate; the second is what
tells an operator reading the log that nothing is being hidden from them.

**The cache is re-derived from the required repository rather than read off the context.**
:attr:`~scitools_hook.runner.context.RunContext.cache` answers ``CachePaths | None``, and the
``None`` is exactly the case :meth:`~scitools_hook.runner.context.RunContext.require_repo`
has already refused two lines earlier -- so reading it would leave a branch no input can
reach, and this project has recorded "a test whose failure mode is unreachable" as one of its
standing hazards. The expression below is the one that property computes, given a repository
that certainly exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from scitools_hook.cli.common import GlobalOptions
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.cache import CachePaths
from scitools_hook.runner.baseline_cmd import BaselineCmd
from scitools_hook.runner.check import CheckPipeline
from scitools_hook.runner.context import ContextOptions, RunContext, build_context, cache_dir
from scitools_hook.runner.explain import ExplainPipeline
from scitools_hook.understand.codecheck import CodeCheckRunner
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.snapshot import SnapshotExtractor


@dataclass(frozen=True, slots=True)
class Assembly:
    """The three things every pipeline is built from, assembled once per run."""

    ctx: RunContext
    dbm: DatabaseManager
    extractor: SnapshotExtractor

    def check(self) -> CheckPipeline:
        """The gate pipeline (req 4).

        The CodeCheck runner is always supplied rather than conditionally: the pipeline
        already treats an unconfigured ``codecheck.config`` as "do not run it" (req 6.9), so
        deciding it a second time here would be a second place for the two answers to differ.
        """
        return CheckPipeline(
            self.ctx,
            self.dbm,
            self.extractor,
            CodeCheckRunner(self.ctx.und),
            self.ctx.baseline_store(),
        )

    def explain(self) -> ExplainPipeline:
        """The review-aid pipeline (req 9); it builds its own graph and impact adapters."""
        return ExplainPipeline(self.ctx, self.dbm, self.extractor)

    def baseline(self) -> BaselineCmd:
        """The baseline capture (req 8.1)."""
        return BaselineCmd(self.ctx, self.dbm, self.extractor)


def assemble(options: GlobalOptions, overrides: Mapping[str, object] | None = None) -> Assembly:
    """Build everything one run needs, or raise the typed error that stops it.

    ``overrides`` are the settings this command line sets by option -- dotted keys the loader
    merges above every file (req 3.2). They are passed through
    :class:`~scitools_hook.runner.context.ContextOptions` rather than into a pipeline, because
    the pipelines read ``ratchet.strict``, ``baseline.adaptive`` and ``output.show_highest``
    off the settings and a second channel would let the two disagree.

    Raises ``NotAGitRepositoryError`` (exit 6) outside a working tree, ``ConfigError`` (exit 2)
    for invalid configuration, ``UnderstandNotFoundError`` (exit 3) and ``LicenseError``
    (exit 4) from the locator. Nothing is caught here: ``GateGroup`` maps each to its code.
    """
    context_options = _with_overrides(options.context_options(), overrides)
    GitRepo.discover(options.cwd, context_options.log)
    ctx = build_context(context_options)
    repo = ctx.require_repo()
    paths = CachePaths.for_repo(
        repo.common_dir, ctx.settings.understand.db_location, cache_dir(ctx.env)
    )
    manager = DatabaseManager(
        paths, ctx.und, ShadowSync(repo, paths, ctx.settings.project), ctx.settings, ctx.progress
    )
    return Assembly(ctx, manager, SnapshotExtractor(ctx.api, ctx.settings))


def _with_overrides(base: ContextOptions, overrides: Mapping[str, object] | None) -> ContextOptions:
    """``base`` with this command's settings overrides merged over the global ones."""
    if not overrides:
        return base
    return replace(base, cli_overrides={**base.cli_overrides, **overrides})
