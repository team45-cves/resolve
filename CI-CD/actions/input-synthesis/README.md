# RESOLVE Input Synthesis

A GitHub Action that runs RESOLVE's input synthesis on the checked-out project. For each CVE file produced by the [`sbom`](../sbom/README.md) action, a coding agent tries to synthesize an input that triggers the vulnerability and verifies it, writing one result folder per CVE.

Two modes:

- `synthesize`: runs the synthesis stage on a project that is already checked out and built.
- `run-all`: runs the full pipeline (setup, improve-CVE, reachability, synthesize), checking out and building the affected version itself.

## Quick start

Run it in the same job as the `sbom` action, which writes the per-CVE files it reads:

```yaml
- id: sbom
  uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main   # pin to a release tag or commit SHA
  with:
    image: ghcr.io/OWNER/resolve-ci:TAG
    sbom-path: build/sbom.spdx.json

- id: synthesis
  uses: Team45-CVEs/resolve/CI-CD/actions/input-synthesis@main
  with:
    image: ghcr.io/OWNER/resolve-ci-claude:TAG        # RESOLVE image with the agent CLI installed
    cves-dir: sbom-out/cves
    agent: claude
    api-key: ${{ secrets.ANTHROPIC_API_KEY }}
    mode: run-all

- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: synthesis-results
    path: synthesis-out
```

## Requirements

- A runner with Docker and internet access.
- A RESOLVE runtime image with the agent's CLI installed (built with `AI_BACKEND=claude`, `codex` or `opencode`).
- An API key for the agent, stored as a secret.
- Per-CVE files from the `sbom` action, and the project's source checked out into the workspace.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `image` | **yes** | | RESOLVE runtime image with the agent CLI installed. |
| `cves-dir` | no | `sbom-out/cves` | Folder of per-CVE files to process (the `sbom` action's `output-dir` plus `/cves`). |
| `output-dir` | no | `synthesis-out` | Where per-CVE result folders are written, relative to the workspace. |
| `agent` | no | `claude` | Coding agent: `claude`, `codex` or `opencode`. |
| `api-key` | no | `""` | API key for the agent: `ANTHROPIC_API_KEY` for `claude`, `OPENAI_API_KEY` for `codex`. Not used by `opencode`. If empty, the key must already be set in the environment. |
| `model` | no | `""` | Model to pass to the agent CLI (`--model`). Empty uses the agent's default. |
| `mode` | no | `synthesize` | `synthesize` (project already built) or `run-all` (full pipeline). |
| `target-dir` | no | `.` | Folder of the project to analyze, relative to the workspace. |

## Outputs

| Output | Description |
|---|---|
| `synthesis-dir` | Folder containing one result folder per CVE. |
| `cve-count` | Number of CVE files processed. |
| `failed-count` | Number of CVEs whose synthesis didn't complete. |

A CVE that fails is counted in `failed-count`, and the action continues with the next one. To fail the job when any CVE fails, check the output:

```yaml
- if: steps.synthesis.outputs.failed-count != '0'
  run: exit 1
```

## Notes

- **This stage uses an AI agent.** Run it on demand, review what it produces, and expect results to vary between runs. Each CVE can take a long time.
- **The agent runs as a non-root user** inside the container.
- **Data sent externally.** The agent sends parts of your project's source code to its provider. Check that this is allowed for your project.

## License

LGPL-3.0. See `LICENSE.txt` in the repository root.
