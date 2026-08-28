# Requirements Document

## Project Description (Input)
Commit-time maintainability gate on SciTools Understand: a uv/uvx Python CLI plus pre-commit hook (native installer and pre-commit-framework definition; the hook lives in the tool, not the target repo) that analyzes the staged change with Understand, enforces structure and complexity limits (KALOI thresholds per routine/class/file/project, before/after ratchet, dependency cycles, architecture layers, coupling/cohesion), emits agent-actionable violations with remediation hints, and generates review-at-scale aids (structural change summary, dependency/call graphs, change impact) so humans can review agent-written code by shape. The Understand install path is configurable.

Origin: the ideas in `srccheck` (https://github.com/sglebs/srccheck) — per-scope threshold JSON, statistics prefixes (AVG:/MEDIAN:/STDEV:...), adaptive lowering of limits, synthetic metrics, exit code = violation count, before/after diff of two databases — re-targeted at the commit boundary and at guiding AI coding agents.

## Introduction

`scitools-hook` (referred to below as "the Gate") is a command-line tool and git pre-commit hook for repositories where AI coding agents author most changes and humans review them. It uses SciTools Understand as its analysis engine. At commit time it examines only the staged change, compares the structural and complexity metrics of the affected code before and after the change, refuses commits that violate configured limits or make existing code worse, and explains each violation in a way an agent can act on. On demand it produces structural summaries and graphs of a change so that a human reviewer can assess an agent's work by its shape before reading code.

Terminology used throughout:
- **Scope**: one of *routine* (function/method/procedure), *class* (class/interface/struct), *file*, *architecture node* (a node in an Understand architecture, e.g. a directory or a user-defined layer), *project*.
- **Threshold**: a named metric with a maximum allowed value, bound to a scope.
- **Stats prefix**: a threshold expressed on a population rather than an element, e.g. `AVG:CyclomaticStrict`, `MEDIAN:CountLineCode`, `STDEV:CountParams`.
- **Affected entities**: routines, classes and files that are added, modified or deleted by the staged change, plus files whose dependencies change because of it.
- **Ratchet**: the rule that an affected entity's metric may not be worse after the change than before it, even when it stays under the absolute threshold.
- **Baseline**: the stored set of current maximum values that the adaptive mode lowers over time.

## Boundary Context

- **In scope**: locating and driving an installed Understand; maintaining an analysis database for a repository outside its working tree; evaluating thresholds, ratchets and structural rules on the staged change or on the whole project; human- and machine-readable violation output; review aids for a change; a rules snippet for agents; a native git hook installer and a pre-commit-framework hook definition; a self-diagnosis command.
- **Out of scope**: installing or licensing Understand itself; editing source code to fix violations; dashboards, trend storage or publishing to external services (Jenkins plots, SONAR, etc.); server-side or merge-request enforcement beyond what CI can do by invoking the CLI; analysis engines other than Understand; IDE integration.
- **Adjacent expectations**: git provides the staged file list and the pre-change file contents; Understand provides parsing, metrics, references, architectures, graphs and CodeCheck; the pre-commit framework (if used) provides hook orchestration and file filtering. The Gate does not own any of these and must fail clearly when they are unavailable.

## Requirements

### Requirement 1: Understand Location and Environment Diagnosis
**Objective:** As an operator, I want the Gate to find my Understand installation wherever it is and tell me precisely what is wrong when it cannot, so that setup problems never masquerade as code problems.

#### Acceptance Criteria
1. The Gate shall accept the Understand installation directory from, in order of precedence, a command-line option, an environment variable, a configuration file, the `und` executable found on the search path, and a fixed list of well-known installation directories per operating system.
2. When an installation directory is resolved, the Gate shall verify that both the `und` executable and the Understand Python API are usable from that directory before any analysis starts.
3. If no usable installation is found, the Gate shall stop with a distinct exit code and a message that lists every location that was tried and the option or variable to set.
4. If Understand reports that no valid license is available, the Gate shall stop with a distinct exit code and a message naming the license problem, without reporting any metric violations.
5. When the operator runs the diagnosis command, the Gate shall report the resolved installation directory, the Understand version, the Python API status, the license status, the git repository status, the location and state of the repository's analysis database, and the effective configuration with the file each setting came from.
6. The Gate shall exit with a distinct, documented exit code for each of: no violations, violations found, Understand not found, license unavailable, analysis failure, configuration error, and not inside a git repository.

### Requirement 2: Analysis Database Lifecycle
**Objective:** As an operator, I want the Gate to manage the Understand database for my repository automatically and keep it out of my working tree, so that nothing about the tool ends up committed and repeated runs stay fast.

#### Acceptance Criteria
1. When the Gate runs for a repository that has no analysis database yet, the Gate shall create one in a per-repository location outside the working tree, add the repository's source files to it, and analyze it before evaluating any rule.
2. The Gate shall never create or modify files inside the repository working tree except the hook shim written by the explicit hook-installation command and the configuration or baseline files the operator explicitly asks it to write.
3. When a database already exists, the Gate shall analyze only the files that changed since the previous analysis rather than the whole project.
4. When the Gate creates a database, the Gate shall determine the project languages from configuration if present, otherwise from the file types present in the repository, and shall report which languages were enabled.
5. The Gate shall honor include and exclude patterns from configuration when adding files, and shall by default exclude version-control metadata, dependency directories, build outputs and generated files that match a documented default pattern list.
6. If Understand's analysis reports parse errors in staged files, the Gate shall list those files and errors in its output and shall still evaluate all rules on the entities that were parsed.
7. When the operator requests a rebuild, the Gate shall discard the existing database and perform a full analysis.
8. When the operator requests the database location, the Gate shall print the path of the database so that it can be opened in the Understand GUI.

### Requirement 3: Threshold and Rule Configuration
**Objective:** As an operator, I want to express limits the way `srccheck` did — per scope, per metric, with statistics prefixes — but with sensible defaults, so that a repository works out of the box and can be tightened without editing scripts.

#### Acceptance Criteria
1. The Gate shall ship built-in default thresholds for the routine, class, file and project scopes that target reviewable agent output, and shall run with those defaults when no configuration file exists.
2. The Gate shall read configuration from, in order of increasing precedence, built-in defaults, a user-level configuration file, a repository-level configuration file, environment variables, and command-line options, and shall merge them so that a more specific source overrides only the keys it defines.
3. The Gate shall accept thresholds as a mapping from metric name to maximum value under each scope, where the metric name is any Understand metric valid for that scope or one of the Gate's documented synthetic metrics.
4. The Gate shall accept a stats prefix (`AVG`, `MEDIAN`, `MEDIANHIGH`, `MEDIANLOW`, `MODE`, `STDEV`, `VARIANCE`) on any element-scope metric name, and shall evaluate the prefixed threshold against the population of that scope rather than against individual elements.
5. The Gate shall provide the synthetic metrics `CountParams` (declared parameters of a routine) and `CountDeclMethodNonStub` (declared methods excluding trivial accessors) for the routine and class scopes respectively.
6. The Gate shall accept regular-expression ignore lists for files, classes and routines, and shall exclude matching entities from all threshold, ratchet and structural evaluation while still reporting how many entities were ignored.
7. The Gate shall accept a severity of `error` or `warning` per threshold and structural rule, where only `error` findings block a commit.
8. If a configuration file contains an unknown metric name, an unknown scope, a value of the wrong type, or an invalid regular expression, the Gate shall stop with the configuration-error exit code and a message naming the file, the key and the problem.
9. When the operator runs the configuration-initialization command, the Gate shall write a repository-level configuration file populated with the built-in defaults and explanatory comments, and shall refuse to overwrite an existing file unless forced.
10. When the operator runs the configuration-display command, the Gate shall print the effective merged configuration and the source of every value.

### Requirement 4: Staged-Change Gate and Ratchet
**Objective:** As a developer supervising an agent, I want each commit judged on what it changes — against absolute limits and against the code as it was — so that the codebase never gets worse one commit at a time.

#### Acceptance Criteria
1. When invoked in staged mode, the Gate shall evaluate the staged content of the index (not the working tree) of every staged, non-deleted file that Understand can parse, and shall ignore unstaged modifications to the same files.
2. When invoked in staged mode, the Gate shall determine the set of affected entities as the routines, classes and files defined in staged files, plus files whose dependency set changed because of the staged change.
3. When invoked in staged mode, the Gate shall obtain the pre-change metrics of affected entities from the committed state of the repository (the current `HEAD`), so that each finding can state the value before and after the change.
4. While the ratchet is enabled for a metric, if an affected entity's metric value after the change is worse than before the change, the Gate shall report a ratchet finding even when the new value is within the absolute threshold.
5. The Gate shall treat an entity that is new in the change as having no pre-change value, and shall evaluate it only against absolute thresholds.
6. When an affected entity exceeds an absolute threshold after the change but already exceeded it before the change and did not get worse, the Gate shall report it as pre-existing and shall not count it as a blocking finding unless strict mode is enabled.
7. Where strict mode is enabled, the Gate shall count pre-existing violations in affected entities as blocking findings.
8. When invoked in whole-project mode, the Gate shall evaluate every entity in the database against absolute thresholds and stats-prefixed thresholds, without ratchet findings.
9. When there are no staged files that Understand can parse, the Gate shall exit with the no-violations exit code and a one-line message saying nothing was analyzed.
10. When the staged change contains only deletions, the Gate shall evaluate the structural rules on the remaining affected files and exit with no violations if none are found.
11. The Gate shall complete a staged-mode run on a change touching up to 20 files in a repository whose database already exists in under 30 seconds on typical developer hardware, and shall print a progress message when any single phase exceeds 5 seconds.

### Requirement 5: Complexity and Size Limits
**Objective:** As a reviewer, I want every routine, class and file that an agent touches to stay within limits that a human can read in one sitting, so that reviewing by shape is possible.

#### Acceptance Criteria
1. The Gate shall evaluate routine-scope thresholds including at least cyclomatic complexity (strict and modified variants), essential complexity, maximum nesting depth, lines of code, statement count, parameter count and number of paths.
2. The Gate shall evaluate class-scope thresholds including at least number of declared methods, number of non-stub methods, number of instance variables, depth of inheritance tree, number of immediate subclasses, coupling between classes and lack of cohesion.
3. The Gate shall evaluate file-scope thresholds including at least lines of code, number of declared functions, number of declared classes, maximum cyclomatic complexity within the file and comment-to-code ratio.
4. The Gate shall evaluate project-scope thresholds including at least average and maximum cyclomatic complexity, average routine length and maximum nesting, and shall compute stats-prefixed thresholds over the population of the corresponding scope after applying ignore lists.
5. When a metric named in a threshold is not available for the language of an entity, the Gate shall skip that metric for that entity and shall report once per run which metrics were unavailable for which language.
6. The Gate shall report, per metric, the highest value found among affected entities and the entity that has it, even when that value is not a violation, when the operator asks for highest values.

### Requirement 6: Structural Limits
**Objective:** As an architect, I want the Gate to hold the line on structure — no new cycles, no layer violations, bounded coupling — because those are the properties an agent is most likely to erode and a human is least likely to notice in a diff.

#### Acceptance Criteria
1. When a staged change introduces a dependency cycle between files that did not exist before the change, the Gate shall report a structural finding that lists every file in the cycle and the references that close it.
2. When a staged change introduces a dependency cycle between architecture nodes at a configured architecture level, the Gate shall report a structural finding naming the nodes and the references that close the cycle.
3. Where the configuration defines allowed dependency directions between named architecture nodes (layers), the Gate shall report a structural finding for every new reference from a node to a node it is not allowed to depend on, naming the source entity, the target entity and the rule violated.
4. The Gate shall evaluate fan-in and fan-out thresholds for files and classes (number of files/classes that depend on the entity and that the entity depends on), and shall report ratchet findings when an affected entity's fan-out grows.
5. The Gate shall evaluate a threshold on the number of new external dependencies a file gains in one change, so that a change that wires one file to many new files is reported even when each individual metric stays within limits.
6. Where a coupling rule is configured for a pair of architecture nodes, the Gate shall report a finding when the number of references between the nodes after the change exceeds the configured maximum.
7. The Gate shall use Understand's architectures as the source of nodes, and shall by default use the directory-structure architecture at a configurable depth when no user-defined architecture is named.
8. If a configured architecture name does not exist in the database, the Gate shall stop with the configuration-error exit code and list the architectures that do exist.
9. Where the configuration names an Understand CodeCheck configuration, the Gate shall run it on the staged files and shall report each CodeCheck violation as a finding with the severity assigned in configuration.

### Requirement 7: Findings Output for Humans and Agents
**Objective:** As a coding agent, I want every finding to tell me exactly what is wrong, where, by how much, and what to do about it, so that I can fix it without a human intervening.

#### Acceptance Criteria
1. The Gate shall emit, for every finding, the rule kind (threshold, ratchet, structural, codecheck), the metric or rule name, the scope, the entity's qualified name, the file path relative to the repository root and the line number, the value after the change, the value before the change when known, the limit, the severity, and a remediation hint.
2. The Gate shall provide a remediation hint specific to the metric or rule (for example, for nesting depth: extract the inner block into a routine or use guard clauses), drawn from a documented hint catalogue that operators can extend in configuration.
3. When output format is human, the Gate shall print findings grouped by file, ordered by severity then by how far over the limit they are, followed by a one-line summary of counts per severity and the exit code meaning.
4. When output format is JSON, the Gate shall print a single JSON document with a documented, versioned schema containing the run metadata, the effective thresholds, all findings, the ignored-entity counts, the unavailable metrics, and the parse errors, and shall print nothing else on standard output.
5. Where the operator requests SARIF output, the Gate shall write the findings as a SARIF 2.1.0 file with one result per finding and one rule per metric or structural rule.
6. When the environment indicates a non-interactive terminal, the Gate shall not use color or cursor control sequences unless explicitly forced.
7. The Gate shall print findings to standard output and diagnostics to standard error, so that machine consumers can separate them.
8. When the operator requests quiet mode, the Gate shall print only the summary line and blocking findings.
9. The Gate shall exit with the violations-found exit code when at least one `error`-severity finding is blocking, and with the no-violations exit code when only warnings or pre-existing findings exist.

### Requirement 8: Adaptive Baseline
**Objective:** As a team lead, I want the limits to tighten automatically as the code improves and never loosen silently, so that the maintainability initiative keeps moving without anyone editing thresholds by hand.

#### Acceptance Criteria
1. When the operator runs the baseline command, the Gate shall record, for every configured threshold, the current maximum (or stats) value found in the project, in a baseline file at a location the operator chooses, defaulting to a repository-level file.
2. While adaptive mode is enabled and a baseline file exists, the Gate shall use, for each threshold, the lower of the configured maximum and the baseline value as the effective limit.
3. When a run in adaptive mode finds that the current maximum for a threshold is lower than the recorded baseline, the Gate shall lower the baseline to the new value and shall report which limits were tightened.
4. The Gate shall never raise a baseline value automatically; if the operator wants to loosen a limit, they shall edit the baseline or configuration explicitly.
5. When the effective limit for a threshold comes from the baseline rather than from configuration, the Gate shall say so in the finding.
6. If the baseline file is unreadable or contains a threshold not present in configuration, the Gate shall report the problem and continue using configured limits for the affected thresholds.

### Requirement 9: Review-at-Scale Aids
**Objective:** As a human reviewer, I want a structural picture of an agent's change — what it touched, what it now depends on, what depends on it, and how complexity moved — before I open a single file, so that I can review large changes by shape.

#### Acceptance Criteria
1. When the operator runs the explain command on a staged change or on a commit range, the Gate shall produce a change summary listing, per affected file, the routines and classes added, removed and modified, with their key metrics before and after and the delta.
2. The change summary shall list every dependency added or removed between affected files and other files, grouped by architecture node, and shall mark dependencies that cross an architecture boundary.
3. The change summary shall identify the affected entities with the largest metric deltas and the largest absolute values, ranked, so that a reviewer can start with the riskiest parts.
4. When the operator requests graphs, the Gate shall export, for each affected routine or class up to a configurable count, a callers/callees (butterfly) graph and, for each affected file, a depends-on graph, as SVG files in an output directory the operator chooses, and shall reference each file from the summary.
5. When the operator requests change impact, the Gate shall list, for each modified routine and class, the entities that reference it transitively up to a configurable depth, with counts, so that the reviewer knows the blast radius of the change.
6. The Gate shall produce the change summary as human-readable text, Markdown suitable for pasting into a merge request, and JSON.
7. When an affected entity belongs to one or more Understand architectures, the summary shall show its architecture path so that the reviewer can locate it in the Understand GUI.
8. The Gate shall print, at the end of the summary, the command to open the repository's database in the Understand GUI.

### Requirement 10: Agent Guidance
**Objective:** As a developer who delegates to coding agents, I want the Gate to tell the agent the rules up front and let it self-check, so that violations are fixed before I see them.

#### Acceptance Criteria
1. When the operator runs the agent-rules command, the Gate shall print a Markdown snippet that states the effective thresholds and structural rules in plain language, the command the agent must run before committing, how to read the JSON output, and the expected workflow when the Gate blocks a commit.
2. The agent-rules snippet shall be deterministic for a given effective configuration, so that it can be committed to an agent instructions file and regenerated when configuration changes.
3. Where the operator supplies a target file path, the Gate shall insert or replace the snippet inside that file between clearly marked begin/end markers without disturbing other content.
4. When the Gate blocks a commit in human output mode, the Gate shall end its output with a short instruction block telling an agent how to re-run the check and where to find the remediation hints.
5. The Gate shall accept a mode in which it evaluates the working tree instead of the index, so that an agent can check its edits before staging them.

### Requirement 11: Hook Installation and Pre-commit Framework Integration
**Objective:** As an operator, I want to switch the Gate on for a repository with one command, without committing hook scripts into that repository, so that adoption does not require changing the codebase.

#### Acceptance Criteria
1. When the operator runs the hook-installation command inside a git repository, the Gate shall write a pre-commit hook shim into the repository's git hooks directory that invokes the Gate in staged mode, and shall make it executable.
2. If a pre-commit hook already exists, the Gate shall refuse to overwrite it unless forced, and when forced shall keep the existing hook's content and chain to it after the Gate's own check.
3. The shim shall not contain thresholds or analysis logic; changing configuration shall never require reinstalling the hook.
4. When the hook runs and the Gate itself cannot be executed (for example the tool is not installed or Understand is missing), the hook shall print a clear message and shall block the commit by default, with a documented environment variable that turns this into a warning instead.
5. The Gate shall honor a documented environment variable that skips the check for one commit, and shall print a notice that the check was skipped.
6. When the operator runs the hook-uninstallation command, the Gate shall remove only the shim it installed and restore any chained hook.
7. The Gate shall provide a pre-commit-framework hook definition so that a repository can enable it by referencing the Gate's repository in its pre-commit configuration, with the framework passing the staged file list to the Gate.
8. When invoked by the pre-commit framework with a file list, the Gate shall evaluate only the given files as the staged set and shall still resolve the pre-change state from `HEAD`.
9. When the hook-installation command is run with the global option, the Gate shall install the shim into the user's global hooks path and report the path it used.

### Requirement 12: Command-Line Interface
**Objective:** As a user, I want one consistent command with subcommands that behaves the same locally, in a hook, in CI and when driven by an agent, so that there is only one thing to learn.

#### Acceptance Criteria
1. The Gate shall expose a single executable with the subcommands: `check`, `explain`, `baseline`, `init`, `config`, `doctor`, `db`, `install-hook`, `uninstall-hook` and `agent-rules`, each with `--help` that documents every option and the exit codes.
2. The Gate shall be installable and runnable with `uvx` from its published package without the user creating a virtual environment.
3. The Gate shall accept `--staged`, `--worktree`, `--all` and `--files <list>` as mutually exclusive selection modes for `check` and `explain`, defaulting to `--staged` when run from a hook and to `--all` otherwise.
4. The Gate shall accept `--format human|json|sarif|markdown` where applicable and `--output <path>` to write to a file instead of standard output.
5. When run outside a git repository, the Gate shall stop with the not-a-git-repository exit code for subcommands that need git, and shall still allow `doctor` and `config` to run.
6. The Gate shall never prompt for input; every choice shall be expressible as an option or environment variable.
7. When any subcommand fails because of an unexpected error, the Gate shall print a one-line error with the exception type and, where verbose mode is on, the full traceback, and shall exit with the analysis-failure exit code.
8. The Gate shall provide `--verbose` to print each external command it runs, with timing, to standard error.
