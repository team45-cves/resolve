# RESOLVE SBOM Scan

A GitHub Action that scans an SPDX software bill of materials (SBOM) for known vulnerabilities using [`resolve sbom`](https://riversideresearch.github.io/resolve/latest/components/resolve-cli/sbom/), part of the [RESOLVE](https://riversideresearch.github.io/resolve/latest/) security toolchain.

Give it an SBOM and it returns:

- `vulnerabilities.json`, containing every finding, and
- one `cves/<CVE-ID>.json` file per CVE, so later stages can process CVEs one at a time (for example, as a job matrix).

Each dependency is looked up in NIST's [NVD](https://nvd.nist.gov/), each CVE is mapped to its CWE(s), and, optionally, an LLM extracts the affected file and function from the CVE description. The output is the same [`vulnerabilities.json`](https://riversideresearch.github.io/resolve/latest/concepts/vulnerabilities-json/) format that the rest of RESOLVE (reachability, remediation) consumes.

## Quick start

```yaml
name: SBOM scan
on: [push, pull_request]

permissions:
  contents: read

jobs:
  sbom:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Scan SBOM
        id: sbom
        uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main   # pin to a release tag or commit SHA
        with:
          image: ghcr.io/OWNER/resolve-ci:TAG               # the RESOLVE runtime image
          sbom-path: build/sbom.spdx.json

      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: sbom-results
          path: sbom-out
```

Replace the `image` value with the RESOLVE runtime image and tag you want to use. Pin the action to a release tag or full commit SHA rather than `main` so your pipeline doesn't change underneath you.

## Requirements

- A runner with Docker (GitHub-hosted `ubuntu-latest` works) and internet access. The scan queries NVD and MITRE.
- An SBOM in **SPDX JSON** format, checked out into the workspace or produced by an earlier step. RESOLVE is designed for **CMake-generated** SPDX SBOMs.
- Access to the RESOLVE runtime image. The action runs it with `docker run`, which pulls the image if it isn't already on the runner. For a private registry, log in first (for example with `docker/login-action`).

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `image` | **yes** | | RESOLVE runtime image to run. |
| `sbom-path` | **yes** | | Path to the SPDX SBOM, relative to the workspace. |
| `output-dir` | no | `sbom-out` | Where results are written, relative to the workspace. Created if missing. |
| `llm-provider` | no | `none` | LLM that fills in the affected file and function: `none`, `gemini`, `ollama` or `opencode`. See [LLM providers](#llm-providers). |
| `api-key` | no | `""` | API key for `llm-provider`. Required for `gemini`. |
| `ollama-host` | no | `""` | URL of the ollama server, as reached from inside the container (for example `http://172.17.0.1:11434`). Used with `llm-provider: ollama`. |
| `min-score` | no | `""` | Minimum CVSS v3 base score. Empty means no minimum. When set, CVEs with no v3 score are also excluded. |
| `cve-ids` | no | `""` | Only report these CVE IDs, space-separated. Empty means all. |
| `filter-rejected` | no | `true` | Exclude CVEs rejected by NIST. |
| `filter-disputed` | no | `false` | Exclude disputed CVEs. |
| `filter-deferred` | no | `false` | Exclude CVEs deferred by NIST. |

All paths are relative to the workspace root. Boolean inputs are the strings `"true"` or `"false"`.

## Outputs

| Output | Description |
|---|---|
| `vulnerabilities-path` | Path to `vulnerabilities.json`, relative to the workspace. |
| `cve-count` | Number of distinct CVEs found. |
| `cve-files` | JSON array of per-CVE files, **relative to `output-dir`**, e.g. `["cves/CVE-2026-34085.json"]`. |

The action also adds a results table (CVE, CWE, package, version, function, file) to the job summary.

### Files written to `output-dir`

```
sbom-out/
├── vulnerabilities.json     # every finding
└── cves/
    ├── CVE-2024-31755.json  # one document per CVE
    └── ...
```

`vulnerabilities.json` has one entry per CVE/CWE pair, so a CVE with two CWEs yields two entries but is counted once in `cve-count` and gets a single file. Each per-CVE file uses the same `{"vulnerabilities": [...]}` shape as the full file.

Every entry contains `cve-id`, `cve-description`, `cwe-id`, `cwe-name`, `package-name`, `package-version`, `affected-function` and `affected-file`. Without an LLM provider, `affected-function` and `affected-file` are `UNKNOWN`.

## Examples

### Only serious CVEs, skipping disputed ones

```yaml
- uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main
  with:
    image: ghcr.io/OWNER/resolve-ci:TAG
    sbom-path: build/sbom.spdx.json
    min-score: "7.0"
    filter-disputed: "true"
```

### Look up specific CVEs

```yaml
- uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main
  with:
    image: ghcr.io/OWNER/resolve-ci:TAG
    sbom-path: build/sbom.spdx.json
    cve-ids: "CVE-2024-31755 CVE-2023-12345"
```

### Fail the build if any CVEs are found

Finding no CVEs is a success, and so is finding some. To gate on the result, check the output:

```yaml
- id: sbom
  uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main
  with:
    image: ghcr.io/OWNER/resolve-ci:TAG
    sbom-path: build/sbom.spdx.json

- name: Gate on findings
  if: steps.sbom.outputs.cve-count != '0'
  run: |
    echo "::error::${{ steps.sbom.outputs.cve-count }} known CVEs found"
    exit 1
```

### One job per CVE

```yaml
jobs:
  scan:
    runs-on: ubuntu-latest
    outputs:
      cve-files: ${{ steps.sbom.outputs.cve-files }}
    steps:
      - uses: actions/checkout@v4
      - id: sbom
        uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main
        with:
          image: ghcr.io/OWNER/resolve-ci:TAG
          sbom-path: build/sbom.spdx.json
      - uses: actions/upload-artifact@v4
        with:
          name: sbom-out
          path: sbom-out

  per-cve:
    needs: scan
    runs-on: ubuntu-latest
    # Skip when there are no CVEs: an empty matrix fails the workflow.
    if: needs.scan.outputs.cve-files != '[]'
    strategy:
      matrix:
        cve-file: ${{ fromJSON(needs.scan.outputs.cve-files) }}
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: sbom-out
          path: sbom-out
      - run: echo "Processing sbom-out/${{ matrix.cve-file }}"
```

## LLM providers

By default (`llm-provider: none`) no LLM is contacted, and the affected file and function are `UNKNOWN`. To fill them in, choose one of these providers:

| Provider | Model | Requires |
|---|---|---|
| `gemini` | `gemini-2.5-flash` | `api-key` (a Google AI Studio key) |
| `ollama` | `gemma3` | An ollama server with the model pulled, reachable through `ollama-host`. No API key; runs locally. |
| `opencode` | `openai/gpt-5.3-codex-spark` | The `opencode` CLI installed and authenticated in the image (build it with `AI_BACKEND=opencode`). |

The model for each provider is set by the RESOLVE release in the image.

### Gemini

Store the key as a repository or organization secret:

```yaml
- uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main
  with:
    image: ghcr.io/OWNER/resolve-ci:TAG
    sbom-path: build/sbom.spdx.json
    llm-provider: gemini
    api-key: ${{ secrets.GEMINI_API_KEY }}
```

If `gemini` is selected and no key is provided, the action fails.

### Ollama

Ollama runs the model on the runner itself, with no outside AI service. On a GitHub-hosted runner, start it as a second container and point `ollama-host` at Docker's bridge gateway:

```yaml
- name: Start ollama
  id: ollama
  run: |
    docker run -d --name ollama -p 11434:11434 ollama/ollama:latest
    until curl -sf http://localhost:11434/api/version; do sleep 2; done
    docker exec ollama ollama pull gemma3
    gateway=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')
    echo "host=http://$gateway:11434" >> "$GITHUB_OUTPUT"

- uses: Team45-CVEs/resolve/CI-CD/actions/sbom@main
  with:
    image: ghcr.io/OWNER/resolve-ci:TAG
    sbom-path: build/sbom.spdx.json
    llm-provider: ollama
    ollama-host: ${{ steps.ollama.outputs.host }}
```

Pulling `gemma3` downloads a few GB, and the model runs on the CPU, so allow several minutes.

### Reviewing LLM results

Extraction runs one CVE at a time. If a provider can't answer for a CVE, RESOLVE logs `Failed to identify affected file, func for <CVE>` in the `resolve sbom` log group and leaves that entry `UNKNOWN`. LLM answers are suggestions: review `affected-file` and `affected-function` before passing them to reachability or remediation.

## When the action fails

| Condition | Message |
|---|---|
| SBOM file not found | `SBOM not found: <path>` |
| `gemini` selected with no key | `llm-provider is gemini but no API key was given...` |
| `resolve sbom` exits non-zero | `resolve sbom failed with exit code <n>` |
| `resolve sbom` writes no output | `resolve sbom did not produce <path>` |
| An entry lacks a required field | `vulnerabilities[<i>] is missing [...]` |

An invalid `min-score`, an unrecognized `llm-provider` or a malformed SBOM surface as a `resolve sbom` failure. Docker errors, such as a missing image or a failed pull, appear before the scan starts.

## Notes

- **Results come from live data.** The scan queries NVD, so the same SBOM can give different results on different days.
- **Check for lookup failures.** If NVD can't be reached, RESOLVE logs `Lookup failure: ...` in the `resolve sbom` log group and reports that package with no CVEs. Rerun the scan if you see it.
- **Defaults differ from the CLI.** The action excludes rejected CVEs by default (`filter-rejected: "true"`); the bare `resolve sbom` command includes rejected, disputed and deferred CVEs unless you pass the filter flags.
- **Output ownership.** The container runs as the runner's user, so files in `output-dir` aren't root-owned and later steps can change or delete them.
- **Data sent externally.** Package names and versions from your SBOM are sent to NVD and MITRE. With `gemini` or `opencode`, CVE descriptions (public data, not your source code) are sent to that provider. With `ollama`, they stay on the runner.

## Learn more

- [RESOLVE documentation](https://riversideresearch.github.io/resolve/latest/)
- [`resolve sbom` reference](https://riversideresearch.github.io/resolve/latest/components/resolve-cli/sbom/)
- [`vulnerabilities.json` format](https://riversideresearch.github.io/resolve/latest/concepts/vulnerabilities-json/)
- [Reachability analysis](https://riversideresearch.github.io/resolve/latest/components/reach/), which consumes this action's output

## License

LGPL-3.0. See `LICENSE.txt` in the repository root.
