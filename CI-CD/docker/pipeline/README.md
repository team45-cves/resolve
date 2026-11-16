# RESOLVE runtime image

The container image every RESOLVE action runs in. It holds:

- the prebuilt [RESOLVE](https://riversideresearch.github.io/resolve/latest/) release (`resolve`, `resolvecc` and the rest of the toolchain) in `/opt/resolve`,
- the tools RESOLVE needs: clang/LLVM, binutils, zstd, CMake, Ninja, git and Python 3,
- optionally, one coding-agent CLI for RESOLVE's AI stages, and
- the action scripts and their entrypoint, `start_action.sh`, in `/app/actions`.

The image is based on Ubuntu 24.04 and runs on `linux/amd64`.

## Building

Build from the `CI-CD/` folder, which is the build context:

```sh
cd CI-CD
docker build -f docker/pipeline/Dockerfile --build-arg AI_BACKEND=none -t resolve-ci:dev .
```

### Build arguments

| Argument | Default | Description |
|---|---|---|
| `AI_BACKEND` | `codex` | Coding-agent CLI to install: `none`, `claude`, `codex` or `opencode`. |
| `RESOLVE_VERSION` | `v0.1.0` | RESOLVE release to install, as a tag from the [RESOLVE releases page](https://github.com/riversideresearch/resolve/releases). |

### Choosing `AI_BACKEND`

| `AI_BACKEND` | Installs | Use it for |
|---|---|---|
| `none` | No agent | Actions that don't use a coding agent, such as `sbom` (including `llm-provider: gemini` or `ollama`). Smallest image. |
| `claude` | Claude Code (`claude`) | AI actions with `agent: claude` |
| `codex` | OpenAI Codex (`codex`) | AI actions with `agent: codex` |
| `opencode` | opencode (`opencode`) | AI actions with `agent: opencode`, and `sbom` with `llm-provider: opencode` |

Build one image per agent you use, and tag them so they're easy to tell apart, for example `resolve-ci:none` and `resolve-ci:claude`.

## Running an action

The entrypoint takes the action name as its first argument. Mount your project at `/work`, which is the working directory, and pass inputs as environment variables named after the action's inputs in upper case with underscores (`sbom-path` becomes `SBOM_PATH`):

```sh
docker run --rm -v "$PWD:/work" \
  -e SBOM_PATH=build/sbom.spdx.json \
  -e OUTPUT_DIR=sbom-out \
  resolve-ci:dev sbom
```

Instead of the argument, the action name can be given in `RESOLVE_ACTION`:

```sh
docker run --rm -v "$PWD:/work" -e RESOLVE_ACTION=sbom -e SBOM_PATH=build/sbom.spdx.json resolve-ci:dev
```

On Windows, use `-v "${PWD}:/work"` in PowerShell or `-v "%cd%:/work"` in Command Prompt.

Each action's README (under `CI-CD/actions/`) lists its inputs. Running the image with no action prints the list of actions.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | The action failed; the log says why. |
| `2` | No action, or an unknown action, was given. |
| `3` | The action isn't included in this image. |
| `4` | An AI action couldn't switch to a non-root user. |

## API keys

Keys are passed in when the container runs and are never stored in the image:

```sh
docker run --rm -v "$PWD:/work" -e ANTHROPIC_API_KEY ... resolve-ci:claude <action>
```

| Agent or provider | Variable |
|---|---|
| `claude` | `ANTHROPIC_API_KEY` |
| `codex` | `OPENAI_API_KEY` |
| `gemini` (`sbom`) | `GEMINI_API_KEY` |

In CI, take keys from the platform's encrypted secrets, never from files in the repository. The GitHub Actions in this repository take them through an `api-key` input.

## Users and file ownership

The container starts as root, which GitHub Actions requires. Actions that run a coding agent then switch to a non-root user, because Claude Code refuses to run as root in the unattended mode RESOLVE uses:

- by default, the owner of the mounted `/work` folder, so files the agent writes belong to you;
- if `/work` is owned by root, the image's built-in `resolve` user.

To run every action as your own user from the start, pass `--user "$(id -u):$(id -g)"`.

## Interactive use

Pass a shell as the command to get a prompt inside the image with your project mounted:

```sh
docker run -it --rm -v "$PWD:/work" resolve-ci:dev bash
```

From there you can run RESOLVE directly, for example `resolve help`.

To use Claude Code with a Claude subscription instead of an API key, keep its login in a named volume so you only sign in once:

```sh
docker run -it --rm -v claude-auth:/root/.claude -v "$PWD:/work" resolve-ci:claude bash
claude    # inside the container: choose "Claude account with subscription" and follow the prompts
```

## Settings

These environment variables change how the entrypoint behaves. The defaults suit most uses.

| Variable | Default | Description |
|---|---|---|
| `RESOLVE_ACTION` | | Action to run when none is given as an argument. |
| `RESOLVE_WORKDIR` | current directory (`/work`) | Folder whose owner AI actions run as. |
| `RESOLVE_USER` | `resolve` | Fallback non-root user for AI actions. |
| `RESOLVE_HOME` | `/home/resolve` | Home folder for AI actions; agent CLIs keep their settings here. |

## Learn more

- [CI-CD README](../../README.md): the actions and how they fit together
- [RESOLVE documentation](https://riversideresearch.github.io/resolve/latest/)
