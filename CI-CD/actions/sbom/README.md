# RESOLVE SBOM Scan

A GitHub Action that scans an SPDX software bill of materials (SBOM) for known vulnerabilities using [`resolve sbom`](https://riversideresearch.github.io/resolve/latest/components/resolve-cli/sbom/), part of the [RESOLVE](https://riversideresearch.github.io/resolve/latest/) security toolchain.

Give it an SBOM and it returns:

- `vulnerabilities.json`, containing every finding, and
- one `cves/<CVE-ID>.json` file per CVE, so later stages can process CVEs one at a time (for example, as a job matrix).

Dependencies are looked up in NIST's [NVD](https://nvd.nist.gov/), each CVE is mapped to its CWE(s), and, optionally, an LLM extracts the affected file and function from the CVE description. The output format is the same [`vulnerabilities.json`](https://riversideresearch.github.io/resolve/latest/concepts/vulnerabilities-json/) that the rest of RESOLVE (reachability, remediation) consumes.

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
          image: ghcr.io/OWNER/resolve-ci:TAG               # the published RESOLVE image
          sbom-path: build/sbom.spdx.json

      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: sbom-results
          path: sbom-out
```

Replace the `image` value with the published RESOLVE runtime image and tag you want to use. Pin the action to a release tag or full commit SHA rather than `main` so your pipeline doesn't change underneath you.

## Requirements

- A runner with Docker (GitHub-hosted `ubuntu-latest` works) and internet access. The scan queries NVD and MITRE live.
- An SBOM in **SPDX JSON** format, checked out into the workspace (or produced by an earlier step). RESOLVE documents support for **CMake-generated** SPDX SBOMs; SBOMs from other generators may not parse or may give incomplete results.
- Access to the RESOLVE image. The action runs it with `docker run`, which pulls the image automatically if it isn't already on the runner. If the image is in a private registry, log in first (for example with `docker/login-action`).

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `image` | **yes** | | RESOLVE runtime image to run. |
| `sbom-path` | **yes** | | Path to the SPDX SBOM, relative to the workspace. |
| `output-dir` | no | `sbom-out` | Where results are written, relative to the workspace. Created if missing. |
| `llm-provider` | no | `none` | Fills in affected file/function. One of `none`, `claude`, `gemini`, `ollama`, `opencode`. See [LLM providers](#llm-providers). |
| `api-key` | no | `""` | API key for `llm-provider`. Required for `claude` and `gemini`, ignored otherwise. |
| `min-score` | no | `""` | Minimum CVSS v3 base score. Empty means no minimum. CVEs with no v3 score are also excluded when this is set. |
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

Every entry contains `cve-id`, `cve-description`, `cwe-id`, `affected-function` and `affected-file`, and usually `cwe-name`, `package-name` and `package-version`. If no LLM provider is used, or extraction fails, `affected-function` and `affected-file` are `UNKNOWN`.

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

By default (`llm-provider: none`), no LLM is contacted and the affected file/function fields are `UNKNOWN`. To fill them in, choose a provider:

| Provider | Needs | Notes |
|---|---|---|
| `gemini` | `api-key` (or `GEMINI_API_KEY` in the environment) | Uses `gemini-2.5-flash`. |
| `claude` | `api-key` (or `ANTHROPIC_API_KEY`) | |
| `ollama` | Ollama running and reachable from the container | Uses `gemma3`. |
| `opencode` | `opencode` CLI installed and authenticated in the image | |

Store keys as repository or organization secrets:

```yaml
with:
  image: ghcr.io/OWNER/resolve-ci:TAG
  sbom-path: build/sbom.spdx.json
  llm-provider: gemini
  api-key: ${{ secrets.GEMINI_API_KEY }}
```

If `claude` or `gemini` is selected and no key is provided, the action **fails** rather than silently returning `UNKNOWN` everywhere.

Extraction runs one CVE at a time, so it can be slow on large SBOMs. Check which providers your chosen image version supports; the `ollama` and `opencode` options depend on what is installed in the image.

## When the action fails

The step fails if:

| Condition | Message |
|---|---|
| SBOM file not found | `SBOM not found: <path>` |
| `claude`/`gemini` selected with no key | `llm-provider is <x> but no API key was given...` |
| `resolve sbom` exits non-zero | `resolve sbom failed with exit code <n>` |
| `resolve sbom` writes no output | `resolve sbom did not produce <path>` |
| An entry lacks a required field | `vulnerabilities[<i>] is missing [...]` |

Other likely causes are an invalid `min-score`, an unrecognized `llm-provider`, or a malformed SBOM, all of which surface as a `resolve sbom` failure. Docker errors such as a missing image or failed pull appear before the scan starts.

## Notes

- **Results come from live data.** The scan queries NVD, which can be slow or rate-limit requests, so the same SBOM can yield different results on different days. If a run fails intermittently, retrying often resolves it.
- **Defaults differ from the CLI.** The action excludes rejected CVEs by default (`filter-rejected: "true"`); the bare `resolve sbom` command includes rejected, disputed and deferred CVEs unless you pass the filter flags.
- **Output ownership.** The container runs as the runner's user, so files in `output-dir` aren't root-owned and later steps can modify or delete them.
- **Data sent externally.** Package names and versions from your SBOM are sent to NVD and MITRE. With an LLM provider enabled, CVE descriptions (public data, not your source code) are sent to that provider.

## Learn more

- [RESOLVE documentation](https://riversideresearch.github.io/resolve/latest/)
- [`resolve sbom` reference](https://riversideresearch.github.io/resolve/latest/components/resolve-cli/sbom/)
- [`vulnerabilities.json` format](https://riversideresearch.github.io/resolve/latest/concepts/vulnerabilities-json/)
- [Reachability analysis](https://riversideresearch.github.io/resolve/latest/components/reach/), which consumes this action's output

## License

LGPL-3.0. See `LICENSE.txt` in the repository root.