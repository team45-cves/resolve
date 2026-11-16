# RESOLVE Input Synthesis: Setup

A GitHub Action that runs [`resolve input-synthesis-setup`](https://riversideresearch.github.io/resolve/latest/), the first stage of RESOLVE's input-synthesis pipeline.

Given one CVE file, it uses a coding agent to prepare the checked-out project for analysis:

- checks out the affected version,
- records the project's architecture, dependencies and build steps in an agent file (`CLAUDE.md` or `AGENTS.md`),
- installs dependencies with source available, and
- builds the project into `build/`.

It handles one CVE per run. To cover several CVEs, run it once per file from the [`sbom`](../sbom/README.md) action's `cve-files` output, for example as a job matrix.

## Quick start

```yaml
- uses: Team45-CVEs/resolve/CI-CD/actions/input_synthesis_setup@main   # pin to a release tag or commit SHA
  with:
    image: ghcr.io/OWNER/resolve-ci-claude:TAG    # RESOLVE image with the agent CLI installed
    cve-path: sbom-out/cves/CVE-2024-31755.json
    agent: claude
    api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

## Requirements

- A runner with Docker and internet access.
- A RESOLVE runtime image with the agent's CLI installed (built with `AI_BACKEND=claude`, `codex` or `opencode`).
- An API key for the agent, stored as a secret.
- The project's source checked out into the workspace.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `image` | **yes** | | RESOLVE runtime image with the agent CLI installed. |
| `cve-path` | **yes** | | Path to the CVE file, relative to the workspace. |
| `agent` | no | `claude` | Coding agent: `claude`, `codex` or `opencode`. |
| `api-key` | no | `""` | API key for the agent: `ANTHROPIC_API_KEY` for `claude`, `OPENAI_API_KEY` for `codex`. Not used by `opencode`. If empty, the key must already be set in the environment. |
| `model` | no | `""` | Model to pass to the agent CLI (`--model`). Empty uses the agent's default. |
| `target-dir` | no | `.` | Folder of the project to prepare, relative to the workspace. |

## Outputs

| Output | Description |
|---|---|
| `image` | The image used, so later stages can reuse it. |
| `agents-file` | The agent file written (`CLAUDE.md` or `AGENTS.md`). |

## Notes

- **This stage uses an AI agent.** Run it on demand, review what it produces, and expect results to vary between runs.
- **The agent runs as a non-root user** inside the container.
- **Data sent externally.** The agent sends parts of your project's source code to its provider. Check that this is allowed for your project.

## License

LGPL-3.0. See `LICENSE.txt` in the repository root.
