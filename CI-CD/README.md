# RESOLVE CI/CD

Run [RESOLVE](https://riversideresearch.github.io/resolve/latest/), Riverside Research's LLVM-based toolchain for CVE triage and remediation of C/C++ code, in your CI/CD pipeline.

RESOLVE is packaged as a container image plus a set of GitHub Actions, one per RESOLVE command. Because every action runs the same image, the same steps can also be run from any CI system that can run containers.

## Actions

| Action | RESOLVE command | What it does |
|---|---|---|
| [`sbom`](actions/sbom/README.md) | `resolve sbom` | Finds known CVEs and CWEs for the packages in an SPDX SBOM, optionally using an LLM to find the affected file and function. |
| [`input_synthesis_setup`](actions/input_synthesis_setup/README.md) | `resolve input-synthesis-setup` | Uses a coding agent to check out and build the affected version of a project for one CVE. |
| [`input-synthesis`](actions/input-synthesis/README.md) | `resolve input-synthesis-*` | Uses a coding agent to synthesize and verify an input that triggers each CVE. |

Each action's README lists its inputs, outputs and examples.

## Quick start

```yaml
name: RESOLVE
on: [push, pull_request]

permissions:
  contents: read

jobs:
  sbom:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - id: sbom
        uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main   # pin to a release tag or commit SHA
        with:
          image: ghcr.io/OWNER/resolve-ci:TAG
          sbom-path: build/sbom.spdx.json

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: sbom-results
          path: sbom-out
```

## The image

All actions run the RESOLVE runtime image. It contains the RESOLVE release, the tools it needs (clang, LLVM, zstd, CMake, Ninja, Python) and the action scripts.

The image comes in one variant per coding agent, chosen at build time with `AI_BACKEND`:

| `AI_BACKEND` | Agent CLI | Use for |
|---|---|---|
| `none` | none | `sbom` and other actions that don't use a coding agent |
| `claude` | Claude Code | AI actions with `agent: claude` |
| `codex` | OpenAI Codex | AI actions with `agent: codex` |
| `opencode` | opencode | AI actions with `agent: opencode`, and `sbom` with `llm-provider: opencode` |

API keys are passed in at run time and are never built into the image.

### Building it yourself

From the `CI-CD/` folder:

```sh
docker build -f docker/pipeline/Dockerfile --build-arg AI_BACKEND=none -t resolve-ci:dev .
```

## Running outside GitHub Actions

The image's entrypoint takes the action name as its first argument, or from the `RESOLVE_ACTION` environment variable. Inputs are environment variables named after the action's inputs, in upper case with underscores (`sbom-path` becomes `SBOM_PATH`). Mount your workspace at `/work`:

```sh
docker run --rm -v "$PWD:/work" \
  -e SBOM_PATH=build/sbom.spdx.json \
  -e OUTPUT_DIR=sbom-out \
  resolve-ci:dev sbom
```

The exit code is `0` on success. Other codes:

| Code | Meaning |
|---|---|
| `1` | The action failed (the log says why). |
| `2` | No action, or an unknown action, was given. |
| `3` | The action isn't included in this image. |
| `4` | An AI action couldn't switch to a non-root user. |

## How it works

```
action.yml or CI job
    │  docker run <image> <action>        (workspace mounted at /work)
    ▼
start_action.sh   (the image's entrypoint)
    │  maps the action name to its script; AI actions switch to a non-root user
    ▼
<action>.py
    │  reads inputs, runs RESOLVE, writes results to the workspace
    ▼
resolve <command>
```

- `scripts/start_action.sh` chooses the script for the requested action. Only known action names can run.
- `scripts/ci_env.py` detects the CI platform and reads inputs and writes outputs in that platform's format.
- `scripts/logger.py` formats log messages for the platform.

## Folder layout

```
CI-CD/
├── actions/       one folder per action: action.yml and README.md
├── scripts/       the entrypoint, shared helpers and one script per action
├── docker/
│   ├── pipeline/       the runtime image
│   └── pipeline_test/  the runtime image plus the test suite
└── tests/         tests and sample projects (see tests/README.md)
```

## Contributing

To add an action for another RESOLVE command:

1. `actions/<name>/action.yml`: the inputs the command needs; run the image with `"$IMAGE" <name>`.
2. `scripts/<name>.py`: read each input with `ci_env.get_input()`, check required inputs, log through `logger.py`, and write results to the workspace.
3. Add the name to the table in `scripts/start_action.sh` (with `needs_non_root=true` if it runs a coding agent).
4. Add tests under `tests/`. `tests/test_parity.py` checks that every `action.yml` matches its script.
5. Add `actions/<name>/README.md`, following [`actions/sbom/README.md`](actions/sbom/README.md).

See [`tests/README.md`](tests/README.md) for running the tests.

## Learn more

- [RESOLVE documentation](https://riversideresearch.github.io/resolve/latest/)
- [`vulnerabilities.json` format](https://riversideresearch.github.io/resolve/latest/concepts/vulnerabilities-json/), the file the actions pass between stages

## License

LGPL-3.0. See `LICENSE.txt` in the repository root.
