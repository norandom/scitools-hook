#!/bin/sh
# scitools-hook-shim v1
#
# Installed by `scitools-hook install-hook`; removed by `scitools-hook uninstall-hook`,
# which also puts back whatever hook was here before. That earlier hook, if there was one,
# is kept beside this file as `pre-commit.scitools-hook-chained` and is run at the end of
# this one, so installing the Gate does not switch it off.
#
# There are no thresholds and no analysis logic in this file (requirement 11.3). Limits live
# in `scitools-hook.toml`, so changing one never means reinstalling this hook.
#
# Two environment variables, each read as "set to a non-empty value":
#
#   SCITOOLS_HOOK_SKIP=1       Skip the Gate's check for this one commit; a notice is
#                              printed. A chained hook still runs -- this variable turns off
#                              the Gate, not somebody else's hook. `git commit --no-verify`
#                              is what skips every hook.
#
#   SCITOOLS_HOOK_SOFT_FAIL=1  Warn instead of blocking when the Gate could not run at all:
#                              exit status 2 and above, which is every infrastructure
#                              failure (the tool is missing, Understand is missing, the
#                              configuration is broken, the report could not be written).
#                              Findings -- exit status 1 -- block whatever this is set to,
#                              because they are the answer the Gate exists to give.
#
# At install time the Gate resolved to: @SCITOOLS_HOOK_RESOLVED@
#
# POSIX sh, exercised under dash: no `local`, no `[[ ]]`, no `==` inside `[ ]`.

set -u

CHAINED="$0.scitools-hook-chained"

note() {
    printf '%s\n' "$1" >&2
}

# --- run the Gate, or say why it did not run ------------------------------------
#
# The `</dev/null` on the whole `if` is deliberate, and it is written once rather than on
# each branch on purpose: git gives a pre-commit hook /dev/null on standard input (measured
# on 2.43.0), but a chained hook may read standard input when this shim is driven by
# something else, and a Gate that consumed it would leave that hook with nothing. Put on one
# branch and forgotten on the other, that is invisible until somebody chains a hook that
# reads -- so the redirection covers the whole construct and there is no other branch to
# forget.

if [ -n "${SCITOOLS_HOOK_SKIP-}" ]; then
    note 'scitools-hook: check skipped because SCITOOLS_HOOK_SKIP is set.'
    status=0
else
    if command -v scitools-hook >/dev/null 2>&1; then
        scitools-hook check --staged
        status=$?
    elif command -v uvx >/dev/null 2>&1; then
        uvx scitools-hook check --staged
        status=$?
    else
        note 'scitools-hook: neither scitools-hook nor uvx is on PATH, so nothing was checked.'
        note 'hint: install it with `uv tool install scitools-hook`, or set SCITOOLS_HOOK_SOFT_FAIL=1.'
        status=3
    fi </dev/null
fi

# --- decide what that status means ----------------------------------------------
#
# A `case` rather than two numeric comparisons, so there is no boundary to get wrong: `*`
# is every status that is neither 0 nor 1, which is "2 and above" for a shell status and
# also covers the 128+signal statuses a crashing Gate would produce. Findings (1) block
# whatever SCITOOLS_HOOK_SOFT_FAIL says, because they are the answer the Gate exists to
# give; everything above is an infrastructure failure, which is what requirement 11.4 lets
# an operator turn into a warning.

case "$status" in
    0)
        ;;
    1)
        exit 1
        ;;
    *)
        if [ -n "${SCITOOLS_HOOK_SOFT_FAIL-}" ]; then
            note "scitools-hook: the check could not run (exit $status); continuing anyway because SCITOOLS_HOOK_SOFT_FAIL is set."
            status=0
        else
            note "scitools-hook: the check could not run (exit $status), so the commit is blocked."
            note 'hint: run `scitools-hook doctor`, or set SCITOOLS_HOOK_SOFT_FAIL=1 to warn instead of blocking.'
            exit "$status"
        fi
        ;;
esac

# --- hand over to the hook this one replaced ------------------------------------
#
# `exec` is what makes the chained hook's exit status, standard input and streams its own,
# with nothing of this shim left in the way. A pre-commit hook is passed no arguments
# (measured), so "$@" is normally empty; it is forwarded rather than dropped so that a
# caller which does pass arguments is not silently robbed of them.

if [ -x "$CHAINED" ]; then
    exec "$CHAINED" "$@"
fi

if [ -e "$CHAINED" ] || [ -h "$CHAINED" ]; then
    note "scitools-hook: not running $CHAINED because it is not an executable file."
fi

exit "$status"
