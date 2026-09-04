#!/bin/sh
# scitools-hook-shim v1
#
# Installed by `scitools-hook install-hook --pre-push`; removed by
# `scitools-hook uninstall-hook --pre-push`, which also puts back whatever hook was here
# before. That earlier hook, if there was one, is kept beside this file as
# `pre-push.scitools-hook-chained` and is run at the end of this one.
#
# What it checks is NOT what the pre-commit hook checks. At push time nothing is staged and
# the working tree is beside the point, so the only honest question is what the commits
# being pushed did to the code: for each ref, `check --range <remote oid>..<local oid>`.
#
# There are no thresholds and no analysis logic in this file (requirement 11.3).
#
#   SCITOOLS_HOOK_SKIP=1       Skip the Gate's check for this one push; a notice is printed.
#                              A chained hook still runs. `git push --no-verify` skips every
#                              hook.
#
#   SCITOOLS_HOOK_SOFT_FAIL=1  Warn instead of blocking when the Gate could not run at all:
#                              exit status 2 and above. Findings -- exit status 1 -- block
#                              whatever this is set to.
#
# At install time the Gate resolved to: @SCITOOLS_HOOK_RESOLVED@
#
# POSIX sh, exercised under dash: no `local`, no `[[ ]]`, no `==` inside `[ ]`.

set -u

CHAINED="$0.scitools-hook-chained"

note() {
    printf '%s\n' "$1" >&2
}

# --- take the ref list once -----------------------------------------------------
#
# git sends the refs being pushed on standard input, and standard input can only be read
# once. A chained pre-push hook needs the same lines, so they are read here and replayed to
# it below; a shim that consumed them would leave that hook believing nothing was pushed.
#
# `read` and `printf` rather than `cat`, and that is not style. A hook inherits whatever PATH
# the caller had, and `refs=$(cat)` with no `cat` on it sets `refs` to nothing, runs the loop
# zero times and exits 0 -- a push waved through unchecked, with one line on stderr that
# nobody reads. Measured here before this was fixed. `read` and `printf` are builtins in dash
# and bash alike, so this reads the refs with no PATH at all.

refs=""
while IFS= read -r line; do
    refs="${refs}${line}
"
done

if [ -z "$refs" ]; then
    note 'scitools-hook: git named no refs on standard input, so nothing was checked.'
fi

# --- run the Gate over one range, or say why it did not run ---------------------
#
# `</dev/null` on each invocation because this shim's own standard input is the here-document
# feeding the loop: a Gate that read it would eat the refs still to be checked.

check_range() {
    if command -v scitools-hook >/dev/null 2>&1; then
        scitools-hook check --range "$1" </dev/null
        return $?
    fi
    if command -v uvx >/dev/null 2>&1; then
        # `uvx scitools-hook` alone CANNOT work: this tool is not on PyPI and never will be.
        # A resolution failure exits 1, which is the Gate's code for "blocking violations
        # found", so the two are told apart here rather than reported as bad code.
        uvx --from '@SCITOOLS_HOOK_SOURCE@' scitools-hook check --range "$1" </dev/null
        checked=$?
        if [ "$checked" -eq 1 ] && ! uvx --from '@SCITOOLS_HOOK_SOURCE@' scitools-hook --version >/dev/null 2>&1; then
            note 'scitools-hook: uvx could not resolve the tool, so nothing was checked.'
            note 'hint: install it with `uv tool install @SCITOOLS_HOOK_SOURCE@`, or set SCITOOLS_HOOK_SOFT_FAIL=1.'
            return 3
        fi
        return "$checked"
    fi
    note 'scitools-hook: neither scitools-hook nor uvx is on PATH, so nothing was checked.'
    note 'hint: install it with `uv tool install @SCITOOLS_HOOK_SOURCE@`, or set SCITOOLS_HOOK_SOFT_FAIL=1.'
    return 3
}

# --- one range per ref being pushed ---------------------------------------------
#
# `blocked` and `broken` are kept apart rather than reduced to a worst status, because they
# mean different things: findings block whatever SCITOOLS_HOOK_SOFT_FAIL says, and an
# infrastructure failure is what that variable exists to downgrade. A single "worst" number
# would let one ref's missing licence mask another ref's real findings.

blocked=0
broken=0

if [ -n "${SCITOOLS_HOOK_SKIP-}" ]; then
    note 'scitools-hook: check skipped because SCITOOLS_HOOK_SKIP is set.'
else
    while read -r local_ref local_oid remote_ref remote_oid; do
        [ -n "${local_ref:-}" ] || continue
        # An all-zero local oid is a ref being DELETED: there is nothing to check.
        case "${local_oid:-}" in
            *[!0]*) ;;
            *) continue ;;
        esac
        # An all-zero remote oid is a branch the remote does not have yet, so there is no
        # before side to compare against. Reported rather than guessed at: picking some base
        # for it would judge commits this push is not responsible for.
        case "${remote_oid:-}" in
            *[!0]*) ;;
            *)
                note "scitools-hook: $local_ref is new on the remote, so there is nothing to compare it against; not checked."
                continue
                ;;
        esac
        check_range "$remote_oid..$local_oid"
        checked=$?
        if [ "$checked" -eq 1 ]; then
            blocked=1
        elif [ "$checked" -ne 0 ]; then
            broken=$checked
        fi
    done <<SCITOOLS_HOOK_REFS
$refs
SCITOOLS_HOOK_REFS
fi

# --- decide what that means ------------------------------------------------------

if [ "$blocked" -ne 0 ]; then
    exit 1
fi

if [ "$broken" -ne 0 ]; then
    if [ -n "${SCITOOLS_HOOK_SOFT_FAIL-}" ]; then
        note "scitools-hook: the check could not run (exit $broken); continuing anyway because SCITOOLS_HOOK_SOFT_FAIL is set."
    else
        note "scitools-hook: the check could not run (exit $broken), so the push is blocked."
        note 'hint: run `scitools-hook doctor`, or set SCITOOLS_HOOK_SOFT_FAIL=1 to warn instead of blocking.'
        exit "$broken"
    fi
fi

# --- hand over to the hook this one replaced ------------------------------------
#
# The refs are replayed on its standard input, because it expects them there and this shim
# has already consumed the original. `%s` and not `%s\n`: each line kept its own newline as it
# was read, so a format that added one would hand the chained hook a blank final line that git
# never sent. `exec` is not used: the replay needs a pipeline, whose status is taken
# explicitly.

if [ -x "$CHAINED" ]; then
    printf '%s' "$refs" | "$CHAINED" "$@"
    exit $?
fi

if [ -e "$CHAINED" ] || [ -h "$CHAINED" ]; then
    note "scitools-hook: not running $CHAINED because it is not an executable file."
fi

exit 0
