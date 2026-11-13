#!/usr/bin/env python3
# Copyright (c) 2026 Riverside Research.
# LGPL-3; See LICENSE.txt in the repo root for details.

"""Pipeline stage: input-synthesis setup.

Drives a coding agent through the `resolve.input_synthesis.setup` stage against
the checked-out repo: check out the affected version named in the CVE, map the
project's architecture/deps/build steps into the agent file (CLAUDE.md /
AGENTS.md), install dependencies with source available, and build into build/.

Standard library only, so it runs under the RESOLVE image's bundled
/opt/resolve/bin/python3 (see CI-CD/actions/input_synthesis_setup/action.yml) or any local
Python 3.10+. Operates on a single CVE; loop in the caller to cover many.

Inputs (via ci_env.get_input -> INPUT_<NAME> on GitHub):
  CVE_PATH    (required) CVE file to set up for
  AGENT       claude | codex | opencode          (default claude)
  API_KEY     key for the agent; else inherited from the environment
  MODEL       optional --model override
  TARGET_DIR  subdir of the repo to prepare       (default ".")

After the agent exits 0, checks that it left the agent file and a non-empty
build/ in the working directory (the target dir) for later stages; fails the
step if not.

Sets the step output `agents-file` (the memory file the stage writes) and, if
GITHUB_STEP_SUMMARY is set, appends a one-line summary.
"""

import os
import subprocess
import sys
from pathlib import Path

# Shared CI helpers (ci_env.py, logger.py) live next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_env as env  # noqa: E402
import logger  # noqa: E402

# --- inputs -----------------------------------------------------------------
CVE_PATH = env.get_input("CVE_PATH", "")
AGENT = env.get_input("AGENT", "claude")
API_KEY = env.get_input("API_KEY", "")
MODEL = env.get_input("MODEL", "")
TARGET_DIR = env.get_input("TARGET_DIR", ".")

# agent -> (the env var its CLI reads for auth, the memory file it writes).
AGENT_SPEC = {
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE.md"),
    "codex": ("OPENAI_API_KEY", "AGENTS.md"),
    "opencode": ("OPENCODE_API_KEY", "AGENTS.md"),
}
# Agents that require a key (opencode may run without one).
KEY_REQUIRED = {"claude", "codex"}
# `claude setup-token` makes subscription tokens (sk-ant-oat...) that the claude
# CLI reads from CLAUDE_CODE_OAUTH_TOKEN; sent as ANTHROPIC_API_KEY they fail auth.
CLAUDE_OAUTH_PREFIX = "sk-ant-oat"
CLAUDE_OAUTH_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def agent_env() -> dict[str, str]:
    """Environment for the setup subprocess, with the agent's key set."""
    run_env = dict(os.environ)
    key_var, _ = AGENT_SPEC[AGENT]
    key = API_KEY or run_env.get(key_var, "")
    if AGENT == "claude":
        key = key or run_env.get(CLAUDE_OAUTH_VAR, "")
        if key.startswith(CLAUDE_OAUTH_PREFIX):
            # The CLI prefers an API key over a token, so drop any it inherited.
            run_env.pop(key_var, None)
            key_var = CLAUDE_OAUTH_VAR
    # Without a key the agent CLI fails deep inside the run; check up front.
    if AGENT in KEY_REQUIRED and not key:
        raise RuntimeError(
            f"agent is {AGENT} but no API key was given. "
            f"Set the api-key input (or {key_var})."
        )
    if key:
        run_env[key_var] = key
    return run_env


def run_setup(cve_path: Path) -> None:
    run_env = agent_env()
    # Run the stage as a module so it works purely from the working-tree code on
    # PYTHONPATH, without depending on console-script binaries baked into the
    # pinned release image.
    command = [
        sys.executable, "-m", "resolve.input_synthesis.setup",
        AGENT, str(cve_path),
    ]
    if MODEL:
        command += ["--model", MODEL]

    with logger.group("input-synthesis setup"):
        logger.info("+ " + " ".join(command))
        subprocess.run(command, check=True, env=run_env)


def check_build() -> None:
    """Fail if setup exited 0 but left nothing for the next stages.

    Paths are relative to the working directory, which the action sets to the
    target dir (-w /work/<target-dir>).
    """
    _, agents_file = AGENT_SPEC[AGENT]
    memory = Path(agents_file)
    if not memory.is_file() or memory.stat().st_size == 0:
        raise RuntimeError(f"setup finished but did not write {agents_file}")

    build = Path("build")
    if not build.is_dir():
        raise RuntimeError("setup finished but build/ was not created")
    if not any(p.is_file() for p in build.rglob("*")):
        raise RuntimeError("build/ is empty; the build likely failed")


def write_outputs(cve_path: Path) -> None:
    _, agents_file = AGENT_SPEC[AGENT]
    rel = agents_file if TARGET_DIR in ("", ".") else f"{TARGET_DIR.rstrip('/')}/{agents_file}"
    env.set_output("agents-file", rel)

    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(
                f"## Input-synthesis setup: `{cve_path}`\n\n"
                f"Prepared the target with **{AGENT}**; wrote `{rel}` and built into `build/`.\n"
            )


def main() -> int:
    if AGENT not in AGENT_SPEC:
        logger.error(f"unknown agent: {AGENT} (use claude | codex | opencode)", title="setup")
        return 1
    if not CVE_PATH:
        logger.error("cve-path input is required", title="setup")
        return 1

    cve_path = Path(CVE_PATH)
    try:
        if not cve_path.is_file():
            raise RuntimeError(f"CVE not found: {cve_path}")
        run_setup(cve_path)
        check_build()
        write_outputs(cve_path)
    except subprocess.CalledProcessError as exc:
        logger.error(f"setup agent failed with exit code {exc.returncode}", title="setup")
        return exc.returncode
    except RuntimeError as exc:
        logger.error(str(exc), title="setup")
        return 1

    logger.notice(f"Setup complete for {cve_path}", title="setup")
    return 0


if __name__ == "__main__":
    sys.exit(main())
