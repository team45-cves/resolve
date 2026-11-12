#!/usr/bin/env python3
# Copyright (c) 2026 Riverside Research.
# LGPL-3; See LICENSE.txt in the repo root for details.

"""Pipeline stage: SBOM lookup.

Runs the existing `resolve sbom` tool on an SPDX SBOM, checks its output, and
writes one vulnerabilities.json per CVE so the input-synthesis stage can take
them one at a time.

In the pipeline, the runtime image (CI-CD/docker/pipeline/Dockerfile) runs this
through its entrypoint, start_action.sh, when given the action name `sbom`
(see CI-CD/actions/sbom/action.yml). Standard library only, so it also runs
locally under any Python 3.10+.

Outputs (in OUTPUT_DIR):
  vulnerabilities.json     all findings from `resolve sbom`
  cves/<CVE-ID>.json       one document per CVE, input to input synthesis
Also sets step outputs (vulnerabilities-path, cve-count, cve-files) and, if
GITHUB_STEP_SUMMARY is set, writes a results table.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
import pwd  # TODO: remove after verifying this runs as the non-root user

# Shared CI helpers; they sit in the same folder as this script (scripts/ in the
# repo, /app/actions in the image), so Python finds them without a path change.
import ci_env as env
import logger

# --- TEMPORARY CONFIG -------------------------------------------------------
# Placeholder values until the rest of the pipeline's scripts and action.yml
# files exist. Read with env.get_input: on GitHub from INPUT_<NAME> (which is
# how action.yml passes its inputs in), locally from <NAME>.


# SPDX SBOM to scan.
SBOM_PATH = env.get_input("SBOM_PATH", "examples/sbom/example.spdx.json")
# Where vulnerabilities.json and cves/ are written.
OUTPUT_DIR = env.get_input("OUTPUT_DIR", "sbom-out")
# LLM used to fill in affected-file/affected-function: none | claude | gemini | ollama | opencode.
LLM_PROVIDER = env.get_input("LLM_PROVIDER", "none")
# Key for LLM_PROVIDER. Handed to resolve as the variable that provider reads
# (see PROVIDER_KEY_VARS); if empty, that variable is used as-is.
API_KEY = env.get_input("API_KEY", "")
# Minimum CVSS v3 base score; empty means no minimum.
MIN_SCORE = env.get_input("MIN_SCORE", "")
# Only report these CVE IDs (space-separated); empty means all.
CVE_IDS = env.get_input("CVE_IDS", "").split()
# Which CVE statuses to drop.
FILTER_REJECTED = env.get_input("FILTER_REJECTED", "true") == "true"
FILTER_DISPUTED = env.get_input("FILTER_DISPUTED", "false") == "true"
FILTER_DEFERRED = env.get_input("FILTER_DEFERRED", "false") == "true"
# -----------------------------------------------------------------------------

# Keys downstream stages (input synthesis, reach, remediate) rely on.
REQUIRED_KEYS = {
    "cve-id",
    "cve-description",
    "cwe-id",
    "affected-function",
    "affected-file",
}

# Providers that need an API key, and the environment variable resolve reads it from.
PROVIDER_KEY_VARS = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def provider_env() -> dict[str, str]:
    """Environment for `resolve sbom`, with the provider's key set if it needs one."""
    run_env = dict(os.environ)
    key_var = PROVIDER_KEY_VARS.get(LLM_PROVIDER)
    if key_var is None:
        return run_env

    key = API_KEY or run_env.get(key_var, "")
    # Without a key the lookup still succeeds but every affected file/function
    # comes back UNKNOWN, which is easy to miss in CI, so fail loudly instead.
    if not key:
        raise RuntimeError(
            f"llm-provider is {LLM_PROVIDER} but no API key was given. "
            f"Set the api-key input (or {key_var}), or set llm-provider to none."
        )
    run_env[key_var] = key
    return run_env


def run_sbom_lookup(sbom_path: Path, vulnerabilities_path: Path) -> None:
    run_env = provider_env()
    command = ["resolve", "sbom", str(sbom_path), "-o", str(vulnerabilities_path)]
    if LLM_PROVIDER != "none":
        command += ["-L", LLM_PROVIDER]
    if MIN_SCORE:
        command += ["--min-score", MIN_SCORE]
    if CVE_IDS:
        command += ["--id", *CVE_IDS]
    if FILTER_REJECTED:
        command.append("--filter-rejected")
    if FILTER_DISPUTED:
        command.append("--filter-disputed")
    if FILTER_DEFERRED:
        command.append("--filter-deferred")

    with logger.group("resolve sbom"):
        logger.info("+ " + " ".join(command))
        subprocess.run(command, check=True, env=run_env)


def hyphenate_keys(vulnerabilities: list[dict]) -> bool:
    """Rename underscore keys (cve_id) to the hyphenated form (cve-id), in place.

    TEMPORARY WORKAROUND: upstream `resolve sbom` (v0.1.0 and riversideresearch
    main) writes underscore keys, but reach, remediate and CVEAssert read
    hyphenated ones (see docs/concepts/vulnerabilities-json.md). Remove this
    once a RESOLVE release writes hyphenated keys itself.

    Returns True if any key was renamed.
    """
    changed = False
    for index, vuln in enumerate(vulnerabilities):
        renamed = {key.replace("_", "-"): value for key, value in vuln.items()}
        if renamed != vuln:
            vulnerabilities[index] = renamed
            changed = True
    return changed


def load_vulnerabilities(vulnerabilities_path: Path) -> list[dict]:
    if not vulnerabilities_path.is_file():
        raise RuntimeError(f"resolve sbom did not produce {vulnerabilities_path}")

    document = json.loads(vulnerabilities_path.read_text(encoding="utf-8"))
    vulnerabilities = document["vulnerabilities"]

    if hyphenate_keys(vulnerabilities):
        logger.warning(
            "resolve sbom wrote underscore keys (cve_id); converted them to hyphenated "
            "keys (cve-id) so reach, remediate and CVEAssert can read them.",
            title="SBOM scan",
        )
        # Rewrite the file so every later stage gets the hyphenated keys too.
        vulnerabilities_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    for index, vuln in enumerate(vulnerabilities):
        missing = REQUIRED_KEYS - set(vuln)
        if missing:
            raise RuntimeError(f"vulnerabilities[{index}] is missing {sorted(missing)}.")
    return vulnerabilities


def write_per_cve_files(vulnerabilities: list[dict], cves_dir: Path) -> list[Path]:
    """One document per CVE, holding every CWE entry `resolve sbom` reported for it."""
    by_cve: dict[str, list[dict]] = {}
    for vuln in vulnerabilities:
        by_cve.setdefault(vuln["cve-id"], []).append(vuln)

    cves_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for cve_id, entries in sorted(by_cve.items()):
        path = cves_dir / f"{cve_id}.json"
        path.write_text(json.dumps({"vulnerabilities": entries}, indent=2) + "\n", encoding="utf-8")
        logger.debug(f"Wrote {path} ({len(entries)} CWE entries)")
        paths.append(path)
    return paths


def write_github_outputs(output_dir: Path, vulnerabilities: list[dict], cve_paths: list[Path]) -> None:
    cve_files = [str(p.relative_to(output_dir)) for p in cve_paths]
    env.set_output("vulnerabilities-path", output_dir / "vulnerabilities.json")
    env.set_output("cve-count", len(cve_paths))
    env.set_output("cve-files", json.dumps(cve_files))

    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        lines = [
            f"## SBOM scan: `{SBOM_PATH}`",
            "",
            f"Found **{len(cve_paths)}** CVEs ({len(vulnerabilities)} CVE/CWE entries).",
            "",
            "| CVE ID | CWE | Package | Version | Function | File |",
            "|---|---|---|---|---|---|",
        ]
        for v in vulnerabilities:
            lines.append(
                f"| {v['cve-id']} | CWE-{v['cwe-id']} {v.get('cwe-name', '')} "
                f"| `{v.get('package-name', '')}` | {v.get('package-version', '')} "
                f"| `{v['affected-function']}` | `{v['affected-file']}` |"
            )
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def main() -> int:
    sbom_path = Path(SBOM_PATH)
    output_dir = Path(OUTPUT_DIR)
    vulnerabilities_path = output_dir / "vulnerabilities.json"


    current_uid = os.geteuid()
    logger.debug(f"Running as: {pwd.getpwuid(current_uid).pw_name} (uid {current_uid})")
    
    try:
        if not sbom_path.is_file():
            raise RuntimeError(f"SBOM not found: {sbom_path}")
        output_dir.mkdir(parents=True, exist_ok=True)

        run_sbom_lookup(sbom_path, vulnerabilities_path)
        vulnerabilities = load_vulnerabilities(vulnerabilities_path)
        cve_paths = write_per_cve_files(vulnerabilities, output_dir / "cves")
        write_github_outputs(output_dir, vulnerabilities, cve_paths)
    except subprocess.CalledProcessError as exc:
        logger.error(f"resolve sbom failed with exit code {exc.returncode}", title="SBOM scan")
        return exc.returncode
    except RuntimeError as exc:
        logger.error(str(exc), title="SBOM scan")
        return 1

    logger.notice(f"Found {len(cve_paths)} CVEs; wrote per-CVE files to {output_dir / 'cves'}", title="SBOM scan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
