# Product Overview

**scitools-hook** is a maintainability gate for codebases where AI agents write most of the code and humans review it. It uses SciTools Understand (metrics, dependency graph, architectures, CodeCheck) to (1) refuse commits that push complexity or structural coupling past agreed limits and (2) generate the navigation aids that let a human reviewer explore an agent's change at scale instead of reading every line.

It takes the KALOI idea ("Keep A Lid On It") from `srccheck` — threshold JSON per scope, stats prefixes, adaptive lowering of limits — and reshapes it for the commit boundary: fast, incremental, focused on what changed, and with output an agent can act on.

## Core Capabilities

- **Commit-time gate**: a pre-commit hook (native `.git/hooks` installer and pre-commit-framework hook definition) that analyzes only the staged change, compares before/after metrics, and blocks regressions and threshold violations.
- **CLI**: the same checks on demand (`check`, `explain`, `baseline`, `install-hook`, `doctor`, `agent-rules`), for local runs, CI, and agents.
- **Structure and complexity limits**: routine/class/file/project thresholds (cyclomatic, nesting, params, size, coupling, cohesion, inheritance depth, fan-in/out, dependency cycles, architecture-layer violations), with a "never worse than before" ratchet on top of absolute limits.
- **Review-at-scale aids**: per-change summaries built from the Understand database — what changed structurally, new dependencies, call/dependency graphs, change impact — exported as text/JSON/SVG so a reviewer can look at shape first and code second.
- **Agent guidance**: emits machine-readable violations with remediation hints, and a rules snippet (CLAUDE.md / AGENTS.md style) that tells agents the limits and how to self-check before committing.

## Target Use Cases

- A developer supervising one or more coding agents wants each commit to stay reviewable: small routines, shallow nesting, no new cycles, no layer violations.
- A reviewer wants a structural picture of a large agent-authored change before opening files.
- A team wants the limits to ratchet down over time without editing build scripts by hand.

## Value Proposition

Reviewing agent output line-by-line does not scale; reviewing its structure does. Understand already has the data — this tool puts it at the commit boundary and shapes it for both the agent (fix it now) and the human (see it at a glance).

---
_Focus on patterns and purpose, not exhaustive feature lists_
