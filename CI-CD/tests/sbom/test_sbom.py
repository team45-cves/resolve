"""Unit tests for sbom.py (the SBOM lookup pipeline stage).

sbom.py runs `resolve sbom`, checks its output, and splits it into one file
per CVE. The tests come in two groups.

Unit tests (sections 1-5) never run the real `resolve` tool: subprocess.run
is replaced with a fake that replays real resolve output recorded from the
sbom-textkit sample project (sbom/recorded/), so they use real data but are
fast, need no network, and check only sbom.py's own logic:

    1. provider_env()          API key handling for LLM providers
    2. run_sbom_lookup()       the `resolve sbom` command line it builds
    3. load_vulnerabilities()  reading and validating resolve's output
    4. write_per_cve_files()   one document per CVE, CWEs grouped together
    5. main()                  the whole stage end to end, plus its exit codes

Real-pipeline tests (section 6, marked real_resolve) run main() with the
real `resolve sbom` on the SBOM sample projects in tests/sample-projects/.
resolve looks each package up in NIST's NVD, so they need the network and
take about 20 seconds in all. pytest.ini deselects them by default, and they
skip themselves if `resolve` can't run (for example on macOS, outside the
test image). Run them in the test image with:

    docker run --rm --platform linux/amd64 resolve-test -m real_resolve sbom -v

sbom.py reads its config (LLM_PROVIDER, MIN_SCORE, ...) once at import, so
tests change those values with monkeypatch.setattr on the module.
"""

import functools
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import sbom


# Real output of `resolve sbom` (v0.1.0, no LLM, --filter-rejected) on the
# sbom-textkit sample project, recorded 2026-11-12 from the real NVD. It is
# exactly what the tool wrote, underscore keys (cve_id) and all, so the
# offline tests work on real data. 5 CVEs in 7 CVE/CWE entries; CVE-2023-6992
# (zlib) has two CWEs, 126 and 787.
RECORDED_OUTPUT = Path(__file__).parent / "recorded" / "textkit-vulnerabilities.json"
RECORDED_RAW = json.loads(RECORDED_OUTPUT.read_text(encoding="utf-8"))["vulnerabilities"]

# The same entries with the hyphenated keys downstream stages read.
REAL_VULNERABILITIES = [
    {key.replace("_", "-"): value for key, value in entry.items()} for entry in RECORDED_RAW
]
REAL_CVE_FILES = [
    "cves/CVE-2016-5384.json",
    "cves/CVE-2023-6992.json",
    "cves/CVE-2026-22184.json",
    "cves/CVE-2026-27171.json",
    "cves/CVE-2026-27820.json",
]


@pytest.fixture
def default_config(monkeypatch):
    """Reset sbom.py's import-time config to known defaults for each test."""
    monkeypatch.setattr(sbom, "LLM_PROVIDER", "none")
    monkeypatch.setattr(sbom, "API_KEY", "")
    monkeypatch.setattr(sbom, "MIN_SCORE", "")
    monkeypatch.setattr(sbom, "CVE_IDS", [])
    monkeypatch.setattr(sbom, "FILTER_REJECTED", True)
    monkeypatch.setattr(sbom, "FILTER_DISPUTED", False)
    monkeypatch.setattr(sbom, "FILTER_DEFERRED", False)


@pytest.fixture
def fake_resolve(monkeypatch):
    """Replace subprocess.run with a fake `resolve sbom`.

    The fake records each command it is given and copies the recorded real
    output (RECORDED_OUTPUT) to the path after -o, as the real tool would.
    Returns the list of recorded calls, each a (command, kwargs) pair.
    """
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        shutil.copyfile(RECORDED_OUTPUT, command[command.index("-o") + 1])
        return subprocess.CompletedProcess(command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _read_github_outputs(output_file):
    """Parse the heredoc entries set_output() writes to GITHUB_OUTPUT into a dict."""
    outputs = {}
    lines = output_file.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        name, delimiter = lines[i].split("<<", 1)
        end = lines.index(delimiter, i + 1)
        outputs[name] = "\n".join(lines[i + 1:end])
        i = end + 1
    return outputs


# ------------------------------------------------------------ provider_env ---


def test_provider_env_needs_no_key_for_none(default_config, monkeypatch):
    """With llm-provider none, no API key is needed and none is added."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run_env = sbom.provider_env()
    assert "ANTHROPIC_API_KEY" not in run_env


def test_provider_env_passes_api_key_to_provider_variable(default_config, monkeypatch):
    """The api-key input is handed to resolve as the variable its provider reads."""
    monkeypatch.setattr(sbom, "LLM_PROVIDER", "claude")
    monkeypatch.setattr(sbom, "API_KEY", "test-key")
    assert sbom.provider_env()["ANTHROPIC_API_KEY"] == "test-key"


def test_provider_env_sends_claude_token_to_oauth_variable(default_config, monkeypatch):
    """A `claude setup-token` token goes to CLAUDE_CODE_OAUTH_TOKEN, not ANTHROPIC_API_KEY."""
    monkeypatch.setattr(sbom, "LLM_PROVIDER", "claude")
    monkeypatch.setattr(sbom, "API_KEY", "sk-ant-oat01-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-runner")
    run_env = sbom.provider_env()
    assert run_env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-test"
    assert "ANTHROPIC_API_KEY" not in run_env


def test_provider_env_uses_claude_token_from_environment(default_config, monkeypatch):
    """With no api-key input, a CLAUDE_CODE_OAUTH_TOKEN already set is enough."""
    monkeypatch.setattr(sbom, "LLM_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    assert sbom.provider_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-test"


def test_provider_env_fails_without_key(default_config, monkeypatch):
    """A provider that needs a key fails loudly rather than returning UNKNOWN for every file."""
    monkeypatch.setattr(sbom, "LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no API key"):
        sbom.provider_env()


# --------------------------------------------------------- run_sbom_lookup ---


def test_run_sbom_lookup_default_command(default_config, fake_resolve, tmp_path):
    """With default config, only --filter-rejected is added to the base command."""
    sbom.run_sbom_lookup(tmp_path / "in.spdx.json", tmp_path / "out.json")

    command, kwargs = fake_resolve[0]
    assert command == [
        "resolve", "sbom", str(tmp_path / "in.spdx.json"),
        "-o", str(tmp_path / "out.json"),
        "--filter-rejected",
    ]
    assert kwargs["check"] is True


def test_run_sbom_lookup_passes_every_option(default_config, fake_resolve, tmp_path, monkeypatch):
    """Each config value turns into its matching `resolve sbom` flag."""
    monkeypatch.setattr(sbom, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(sbom, "MIN_SCORE", "7.0")
    monkeypatch.setattr(sbom, "CVE_IDS", ["CVE-2016-5384", "CVE-2023-6992"])
    monkeypatch.setattr(sbom, "FILTER_REJECTED", False)
    monkeypatch.setattr(sbom, "FILTER_DISPUTED", True)
    monkeypatch.setattr(sbom, "FILTER_DEFERRED", True)

    sbom.run_sbom_lookup(tmp_path / "in.spdx.json", tmp_path / "out.json")

    command, _ = fake_resolve[0]
    assert command[5:] == [
        "-L", "ollama",
        "--min-score", "7.0",
        "--id", "CVE-2016-5384", "CVE-2023-6992",
        "--filter-disputed",
        "--filter-deferred",
    ]


# ----------------------------------------------------- load_vulnerabilities ---


def test_load_vulnerabilities_reads_hyphenated_file_unchanged(tmp_path, capsys):
    """A file that already has hyphenated keys is returned as-is, with no warning."""
    path = tmp_path / "vulnerabilities.json"
    path.write_text(json.dumps({"vulnerabilities": REAL_VULNERABILITIES}), encoding="utf-8")
    assert sbom.load_vulnerabilities(path) == REAL_VULNERABILITIES
    assert "::warning" not in capsys.readouterr().out


def test_load_vulnerabilities_missing_file(tmp_path):
    """If resolve wrote nothing, the stage fails with a clear message."""
    with pytest.raises(RuntimeError, match="did not produce"):
        sbom.load_vulnerabilities(tmp_path / "vulnerabilities.json")


def test_load_vulnerabilities_converts_real_underscore_output(tmp_path, capsys):
    """The real resolve output (cve_id) is converted to cve-id, with a warning, in memory and on disk.

    Downstream stages (reach, remediate, CVEAssert) read the hyphenated keys
    from the file, so the file itself must be rewritten too.
    """
    path = tmp_path / "vulnerabilities.json"
    shutil.copyfile(RECORDED_OUTPUT, path)

    assert sbom.load_vulnerabilities(path) == REAL_VULNERABILITIES
    assert json.loads(path.read_text(encoding="utf-8")) == {"vulnerabilities": REAL_VULNERABILITIES}
    assert "::warning" in capsys.readouterr().out


def test_load_vulnerabilities_rejects_missing_keys(tmp_path):
    """A real entry with a key downstream stages need removed fails, naming the entry and the key."""
    entry = copy.deepcopy(REAL_VULNERABILITIES[0])
    del entry["affected-file"]
    path = tmp_path / "vulnerabilities.json"
    path.write_text(json.dumps({"vulnerabilities": [entry]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"vulnerabilities\[0\] is missing \['affected-file'\]"):
        sbom.load_vulnerabilities(path)


# ----------------------------------------------------- write_per_cve_files ---


def test_write_per_cve_files_groups_by_cve(tmp_path):
    """One file per CVE, sorted by ID, each holding every CWE entry for that CVE.

    In the real textkit output CVE-2023-6992 (zlib) has two CWE entries, so
    both must land in the same file.
    """
    paths = sbom.write_per_cve_files(REAL_VULNERABILITIES, tmp_path / "cves")

    assert [f"cves/{p.name}" for p in paths] == REAL_CVE_FILES

    zlib = json.loads((tmp_path / "cves" / "CVE-2023-6992.json").read_text(encoding="utf-8"))
    assert [v["cwe-id"] for v in zlib["vulnerabilities"]] == ["126", "787"]
    assert {v["package-name"] for v in zlib["vulnerabilities"]} == {"ZLIB:ZLIB::ZLIB"}

    fontconfig = json.loads((tmp_path / "cves" / "CVE-2016-5384.json").read_text(encoding="utf-8"))
    assert fontconfig["vulnerabilities"] == [
        v for v in REAL_VULNERABILITIES if v["cve-id"] == "CVE-2016-5384"
    ]


# -------------------------------------------------------------------- main ---


def test_main_end_to_end_with_recorded_output(github_env, sample_project, default_config, fake_resolve, monkeypatch):
    """A full run on the real textkit SBOM, replaying resolve's recorded output.

    Checks everything the stage produces: the per-CVE files, the step outputs
    and the step summary. The real-NVD version is test_real_textkit below.
    """
    workspace = sample_project("sbom-textkit")
    output_dir = workspace / "sbom-out"
    summary_file = workspace / "summary.md"
    monkeypatch.setattr(sbom, "SBOM_PATH", str(workspace / "sbom.spdx.json"))
    monkeypatch.setattr(sbom, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    assert sbom.main() == 0

    command, _ = fake_resolve[0]
    assert command[2] == str(workspace / "sbom.spdx.json")
    assert sorted(f"cves/{p.name}" for p in (output_dir / "cves").iterdir()) == REAL_CVE_FILES

    outputs = _read_github_outputs(github_env.output_file)
    assert outputs["vulnerabilities-path"] == str(output_dir / "vulnerabilities.json")
    assert outputs["cve-count"] == "5"
    assert json.loads(outputs["cve-files"]) == REAL_CVE_FILES

    summary = summary_file.read_text(encoding="utf-8")
    assert "Found **5** CVEs (7 CVE/CWE entries)" in summary
    assert "| CVE-2016-5384 | CWE-415 Double Free | `fontconfig:fontconfig_project::fontconfig` | 2.2.1 |" in summary


def test_main_fails_when_sbom_missing(github_env, default_config, fake_resolve, monkeypatch):
    """A missing SBOM returns 1 without ever running resolve."""
    monkeypatch.setattr(sbom, "SBOM_PATH", str(github_env.workspace / "missing.spdx.json"))
    monkeypatch.setattr(sbom, "OUTPUT_DIR", str(github_env.workspace / "sbom-out"))

    assert sbom.main() == 1
    assert fake_resolve == []


def test_main_returns_resolve_exit_code(github_env, sample_project, default_config, monkeypatch):
    """If `resolve sbom` crashes, the stage exits with the same code."""
    workspace = sample_project("sbom-textkit")
    monkeypatch.setattr(sbom, "SBOM_PATH", str(workspace / "sbom.spdx.json"))
    monkeypatch.setattr(sbom, "OUTPUT_DIR", str(workspace / "sbom-out"))

    def failing_run(command, **kwargs):
        raise subprocess.CalledProcessError(3, command)

    monkeypatch.setattr(subprocess, "run", failing_run)
    assert sbom.main() == 3


# ------------------------------------------------- 6. real resolve pipeline ---
#
# These run main() with the real `resolve sbom`, which queries NIST's NVD.
# NVD keeps adding CVEs, so they check that specific known CVEs are present,
# never exact counts (see tests/sample-projects/SBOM-SAMPLE-PROJECTS.md).


@functools.cache
def _resolve_problem():
    """Why the real `resolve` can't run here, or None if it can. Checked once per session."""
    try:
        result = subprocess.run(["resolve", "version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"`resolve version` could not run: {exc}"
    if result.returncode != 0:
        return f"`resolve version` exited {result.returncode}: {result.stderr.strip()[:200]}"
    return None


@pytest.fixture
def real_resolve():
    """Skip the test unless the real `resolve` tool works (it does in the test image)."""
    if problem := _resolve_problem():
        pytest.skip(f"real resolve unavailable ({problem}); run these in the test image")


@pytest.fixture
def run_real_pipeline(github_env, sample_project, default_config, monkeypatch):
    """Return a function that runs the SBOM stage on a sample project, for real.

    It copies tests/sample-projects/<project> into the workspace, points
    SBOM_PATH at <sbom_file> in it, runs sbom.main() and returns
    (exit code, output folder, step outputs as a dict).
    """
    def run(project, sbom_file):
        workspace = sample_project(project)
        output_dir = workspace / "sbom-out"
        monkeypatch.setattr(sbom, "SBOM_PATH", str(workspace / sbom_file))
        monkeypatch.setattr(sbom, "OUTPUT_DIR", str(output_dir))
        exit_code = sbom.main()
        return exit_code, output_dir, _read_github_outputs(github_env.output_file)

    return run


@pytest.mark.real_resolve
def test_real_finds_known_cve(real_resolve, run_real_pipeline):
    """sbom-c-vuln (cJSON 1.7.17): the stage finds CVE-2024-31755 and writes a valid file for it.

    Also checks the files downstream stages read: the real resolve writes
    underscore keys (cve_id), so vulnerabilities.json must have been rewritten
    with hyphenated keys, and every entry must have every required key.
    """
    exit_code, output_dir, outputs = run_real_pipeline("sbom-c-vuln", "sbom.spdx.json")

    assert exit_code == 0

    cve_files = json.loads(outputs["cve-files"])
    assert "cves/CVE-2024-31755.json" in cve_files
    assert int(outputs["cve-count"]) == len(cve_files) >= 1
    assert sorted(f"cves/{p.name}" for p in (output_dir / "cves").iterdir()) == sorted(cve_files)

    target = json.loads((output_dir / "cves" / "CVE-2024-31755.json").read_text(encoding="utf-8"))
    assert {v["cve-id"] for v in target["vulnerabilities"]} == {"CVE-2024-31755"}
    assert "cjson" in target["vulnerabilities"][0]["package-name"]
    assert target["vulnerabilities"][0]["package-version"] == "1.7.17"

    on_disk = json.loads((output_dir / "vulnerabilities.json").read_text(encoding="utf-8"))
    for entry in on_disk["vulnerabilities"]:
        assert sbom.REQUIRED_KEYS <= set(entry)
        assert not any("_" in key for key in entry), f"underscore key left in {sorted(entry)}"


@pytest.mark.real_resolve
def test_real_cmake_generated_sbom(real_resolve, run_real_pipeline):
    """sbom-dolphin, a real CMake-generated SBOM from a KDE project: finds CVE-2016-5384 in fontconfig 2.2.1."""
    exit_code, output_dir, outputs = run_real_pipeline("sbom-dolphin", "sbom.spdx.json")

    assert exit_code == 0
    assert "cves/CVE-2016-5384.json" in json.loads(outputs["cve-files"])

    fontconfig = json.loads((output_dir / "cves" / "CVE-2016-5384.json").read_text(encoding="utf-8"))
    entry = fontconfig["vulnerabilities"][0]
    assert entry["package-name"] == "fontconfig:fontconfig_project::fontconfig"
    assert entry["package-version"] == "2.2.1"
    assert entry["cwe-id"] == "415"


@pytest.mark.real_resolve
def test_real_textkit(real_resolve, run_real_pipeline):
    """sbom-textkit, RESOLVE's own example: finds CVEs in both packages and groups a CVE's CWEs in one file.

    CVE-2023-6992 (zlib) has two CWEs, 126 and 787, which must share one per-CVE file.
    """
    exit_code, output_dir, outputs = run_real_pipeline("sbom-textkit", "sbom.spdx.json")

    assert exit_code == 0
    cve_files = json.loads(outputs["cve-files"])
    assert {"cves/CVE-2016-5384.json", "cves/CVE-2023-6992.json"} <= set(cve_files)

    zlib = json.loads((output_dir / "cves" / "CVE-2023-6992.json").read_text(encoding="utf-8"))
    assert {v["cwe-id"] for v in zlib["vulnerabilities"]} >= {"126", "787"}
    assert {v["package-name"] for v in zlib["vulnerabilities"]} == {"ZLIB:ZLIB::ZLIB"}


@pytest.mark.real_resolve
@pytest.mark.parametrize("project", ["sbom-c-clean", "sbom-mixed"])
def test_real_clean_project_reports_zero(real_resolve, run_real_pipeline, project):
    """A project with no vulnerable packages passes with cve-count 0 and no per-CVE files.

    sbom-c-clean has no packages; sbom-mixed has pybind11 2.13.6, which has no known CVEs.
    """
    exit_code, output_dir, outputs = run_real_pipeline(project, "sbom.spdx.json")

    assert exit_code == 0
    assert outputs["cve-count"] == "0"
    assert json.loads(outputs["cve-files"]) == []
    assert list((output_dir / "cves").iterdir()) == []


@pytest.mark.real_resolve
def test_real_project_without_sbom_fails(run_real_pipeline):
    """sbom-python has no SBOM, so the stage fails before running resolve and writes no outputs."""
    exit_code, output_dir, outputs = run_real_pipeline("sbom-python", "sbom.spdx.json")

    assert exit_code == 1
    assert not output_dir.exists()
    assert outputs == {}


@pytest.mark.real_resolve
@pytest.mark.xfail(
    strict=True,
    reason="Known bug: resolve sbom prints 'Could not ingest file' for a non-SPDX SBOM but exits 0, "
    "so sbom.py reports 0 CVEs. To be fixed with the resolve team; remove this marker once it fails.",
)
def test_real_unreadable_sbom_fails(real_resolve, run_real_pipeline):
    """sbom-bad-format (CycloneDX): an SBOM resolve can't read must fail, not report 'no vulnerabilities'."""
    exit_code, _, _ = run_real_pipeline("sbom-bad-format", "sbom.cdx.json")
    assert exit_code != 0
