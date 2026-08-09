#!/usr/bin/env -S just --justfile
# ============================================================================
# Justfile for github-streamlined
# ----------------------------------------------------------------------------
# Every recipe is a thin wrapper around a plain command, so `just` stays a
# convenience rather than a requirement.
#
# Tool versions are pinned in .tool-versions and installed by mise, both
# locally and in CI. pytest is the exception on purpose: the tools themselves
# are stdlib-only, so instead of keeping a virtualenv and lockfile around,
# uv fetches pytest on demand at the version pinned below.
# ----------------------------------------------------------------------------

pytest_version := "9.0.2"

# Default: list available recipes
_default:
    @just --list --unsorted --list-prefix "  "

# ------------------------------ Running -------------------------------------

# Serve the PR triage dashboard for a checkout (default: this repo)
run dir=".":
    #!/usr/bin/env bash
    set -euo pipefail
    # The tool triages whichever repo it is *run from*, so cd first: the point
    # of the argument is `just run ~/Code/some-other-repo`.
    cd "{{ dir }}"
    exec python3 "{{ justfile_directory() }}/pr-triage/pr_triage.py"

# ------------------------------ Setup ---------------------------------------

# Check installation prerequisites
prerequisites:
    #!/usr/bin/env bash
    set -euo pipefail
    # Read the tools out of .tool-versions rather than repeating them, so
    # adding a pin cannot leave this check behind.
    missing=""
    while read -r tool _; do
        case "$tool" in
            "" | \#*) continue ;;
            python) command="python3" ;;
            nodejs) command="node" ;;
            *) command="$tool" ;;
        esac
        command -v "$command" > /dev/null 2>&1 || missing="$missing $command"
    done < .tool-versions
    if [ -n "$missing" ]; then
        echo "Not on PATH:$missing"
        if command -v mise > /dev/null 2>&1; then
            echo "Run 'mise install' to get them at the pinned versions."
        else
            echo "Install mise (https://mise.jdx.dev/getting-started.html), activate it"
            echo "in your shell, then run 'mise install'."
        fi
        exit 1
    fi

# ------------------------------ Checks --------------------------------------

# Lint Python and TOML sources
lint:
    ruff check .
    ruff format --check .
    taplo lint
    taplo fmt --check

# Rewrite sources into formatted form
fmt:
    ruff check --fix .
    ruff format .
    taplo fmt

# Run every test there is
tests: test-py test-js

alias test := tests

# Test the server and the triage logic
test-py:
    uv run --no-project --with pytest=={{ pytest_version }} -- pytest

# Test the browser-side query helpers (Node's built-in runner, no dependencies)
test-js:
    node --test pr-triage/tests/*.test.mjs

# Run everything CI enforces
check: prerequisites lint tests
