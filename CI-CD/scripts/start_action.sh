#!/usr/bin/env bash
#
# start_action.sh - ENTRYPOINT for the RESOLVE container (see ART-001).
#
# An image can have only one entrypoint, so this script dispatches to the
# action script that matches the calling action.yml. Action names mirror the
# RESOLVE v0.1.0 subcommands recorded in ART-004 (`resolve <subcommand>`).
#
# Which action to run is taken from, in order:
#   1. The first argument  (GitHub: `runs.args` in action.yml)
#   2. $RESOLVE_ACTION     (GitLab, Jenkins or local runs)
#
# Exit codes:
#   0  the action script succeeded
#   2  no action, or an unknown action, was requested
#   3  the action script for a known action is missing from the image
#   4  an AI action could not drop root privileges (ART-004 finding F-9)
#   *  any other code is passed through from the action script

set -euo pipefail

# Where the action scripts live inside the image.
SCRIPTS_DIR="${RESOLVE_SCRIPTS_DIR:-/app/actions}"
PYTHON="${RESOLVE_PYTHON:-python3}"

# Unprivileged user the Dockerfile creates for the AI stages, and the HOME
# it gets (the claude backend reads and writes its config under HOME).
RESOLVE_USER="${RESOLVE_USER:-resolve}"
RESOLVE_HOME="${RESOLVE_HOME:-/home/resolve}"

# The shared mount. Runners start the container with it as the working
# directory, so $PWD is the default and no platform variables are needed.
WORKDIR="${RESOLVE_WORKDIR:-$PWD}"

# Plain stderr messages. Platform-specific formatting is logger.py's job;
# this script only reports problems that happen before Python starts.
die() {
    local code="$1"
    shift
    echo "start_action.sh: $*" >&2
    exit "$code"
}

warn() {
    echo "start_action.sh: warning: $*" >&2
}

usage() {
    cat >&2 <<'USAGE'
Usage: start_action.sh <action> [args...]
   or: RESOLVE_ACTION=<action> start_action.sh

Deterministic actions (safe for every commit):
  get-facts                      extract CFG facts from a resolvecc-built binary
  reach                          static reachability of each CVE
  remediate                      toggle CVEAssert checks in an instrumented binary
  sbom                           CVEs/CWEs from an SPDX SBOM (needs network)

AI actions (run on demand; dropped to a non-root user):
  crash-analysis                 findings from crash evidence
  input-synthesis-setup          check out and build the affected version
  input-synthesis-improve-cve    refine CVE trigger conditions
  input-synthesis-reachability   AI-assisted reachability
  input-synthesis-synthesize     synthesize and verify a triggering input
  input-synthesis-run-all        setup -> improve -> reachability -> synthesize
USAGE
}

# GitLab's Docker executor starts the container with a shell to run the
# job's script. Pass those through untouched so GitLab jobs still work.
if [[ $# -gt 0 ]]; then
    case "$1" in
        sh | bash | /bin/sh | /bin/bash | /usr/bin/env)
            exec "$@"
            ;;
    esac
fi

# Work out which action was requested.
if [[ $# -gt 0 ]]; then
    action="$1"
    shift
else
    action="${RESOLVE_ACTION:-}"
fi

if [[ -z "$action" ]]; then
    usage
    die 2 "no action given"
fi

# Map action names to scripts. Only names listed here can run, so input
# from a workflow can never point the container at an arbitrary file.
# Add a line here when a new action.yml and action script are added.
needs_non_root=false
case "$action" in
    # Deterministic core (ART-004: reproducible, offline, automation-ready).
    get-facts)    script="get_facts.py" ;;
    reach)        script="reach.py" ;;
    remediate)    script="remediate.py" ;;

    # Reproducible short-term only: queries live NVD/MITRE data (F-2).
    sbom)         script="sbom.py" ;;

    # LLM-agent stages. RESOLVE runs `claude --dangerously-skip-permissions`,
    # which Claude Code refuses as root, so these must not run as root (F-9).
    crash-analysis)
        script="crash_analysis.py"; needs_non_root=true ;;
    input-synthesis-setup)
        script="input_synthesis_setup.py"; needs_non_root=true ;;
    input-synthesis-improve-cve)
        script="input_synthesis_improve_cve.py"; needs_non_root=true ;;
    input-synthesis-reachability)
        script="input_synthesis_reachability.py"; needs_non_root=true ;;
    input-synthesis-synthesize)
        script="input_synthesis_synthesize.py"; needs_non_root=true ;;
    input-synthesis-run-all)
        script="input_synthesis_run_all.py"; needs_non_root=true ;;

    *)
        usage
        die 2 "unknown action '$action'"
        ;;
esac

script_path="$SCRIPTS_DIR/$script"
if [[ ! -f "$script_path" ]]; then
    die 3 "action '$action' maps to $script_path, which does not exist in this image"
fi

# Already non-root (Dockerfile USER, or a runner that sets --user), or a
# deterministic action: run as we are. exec hands the exit code and stop
# signals straight to the action script.
if [[ "$needs_non_root" == false || "$(id -u)" -ne 0 ]]; then
    exec "$PYTHON" "$script_path" "$@"
fi

# Root and an AI action: drop privileges (F-9). GitHub requires Docker
# actions to start as root, so this can't be fixed with USER alone.
#
# Run as whoever owns the shared mount, so the action can write its outputs
# there and the files belong to the runner's user, not root, afterwards.
# If the mount is owned by root, fall back to the image's resolve user.
uid="$(stat -c %u "$WORKDIR")"
gid="$(stat -c %g "$WORKDIR")"
if [[ "$uid" -eq 0 ]]; then
    if ! id "$RESOLVE_USER" >/dev/null 2>&1; then
        die 4 "action '$action' can't run as root, and user '$RESOLVE_USER' doesn't exist in this image"
    fi
    uid="$(id -u "$RESOLVE_USER")"
    gid="$(id -g "$RESOLVE_USER")"
    warn "$WORKDIR is owned by root; running as '$RESOLVE_USER', which may not be able to write outputs there"
fi

if ! command -v setpriv >/dev/null 2>&1; then
    die 4 "action '$action' can't run as root, and setpriv (util-linux) isn't installed"
fi

# The agent needs a HOME it can write to for its config.
mkdir -p "$RESOLVE_HOME"
chown "$uid:$gid" "$RESOLVE_HOME"
export HOME="$RESOLVE_HOME"

exec setpriv --reuid="$uid" --regid="$gid" --clear-groups -- \
    "$PYTHON" "$script_path" "$@"