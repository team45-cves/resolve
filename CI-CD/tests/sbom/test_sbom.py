"""Unit tests for sbom.py (the SBOM lookup pipeline stage).

sbom.py runs `resolve sbom`, checks its output, and splits it into one file
per CVE. These tests never run the real `resolve` tool: subprocess.run is
replaced with a fake that writes a known vulnerabilities.json, so the tests
are fast and check only sbom.py's own logic:

    1. provider_env()          API key handling for LLM providers
    2. run_sbom_lookup()       the `resolve sbom` command line it builds
    3. load_vulnerabilities()  reading and validating resolve's output
    4. write_per_cve_files()   one document per CVE, CWEs grouped together
    5. main()                  the whole stage end to end, plus its exit codes

sbom.py reads its config (LLM_PROVIDER, MIN_SCORE, ...) once at import, so
tests change those values with monkeypatch.setattr on the module.
"""

import json
import subprocess

import pytest

import sbom


def _vuln(cve_id, cwe_id, **extra):
    """One entry as `resolve sbom` writes it, with every required key."""
    entry = {
        "cve-id": cve_id,
        "cve-description": f"Description of {cve_id}",
        "cwe-id": cwe_id,
        "affected-function": "parse_header",
        "affected-file": "src/parser.c",
    }
    entry.update(extra)
    return entry


# Two CVEs; the first has two CWE entries, so it must stay together in one file.
SAMPLE_VULNERABILITIES = [
    _vuln("CVE-2024-0001", "787", **{"package-name": "libfoo", "package-version": "1.0"}),
    _vuln("CVE-2024-0001", "125", **{"package-name": "libfoo", "package-version": "1.0"}),
    _vuln("CVE-2023-9999", "476", **{"package-name": "libbar", "package-version": "2.3"}),
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

    The fake records each command it is given and writes SAMPLE_VULNERABILITIES
    to the path after -o, as the real tool would. Returns the list of recorded
    calls, each a (command, kwargs) pair.
    """
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output_path = command[command.index("-o") + 1]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"vulnerabilities": SAMPLE_VULNERABILITIES}, f)
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
    monkeypatch.setattr(sbom, "CVE_IDS", ["CVE-2024-0001", "CVE-2023-9999"])
    monkeypatch.setattr(sbom, "FILTER_REJECTED", False)
    monkeypatch.setattr(sbom, "FILTER_DISPUTED", True)
    monkeypatch.setattr(sbom, "FILTER_DEFERRED", True)

    sbom.run_sbom_lookup(tmp_path / "in.spdx.json", tmp_path / "out.json")

    command, _ = fake_resolve[0]
    assert command[5:] == [
        "-L", "ollama",
        "--min-score", "7.0",
        "--id", "CVE-2024-0001", "CVE-2023-9999",
        "--filter-disputed",
        "--filter-deferred",
    ]


# ----------------------------------------------------- load_vulnerabilities ---


def test_load_vulnerabilities_reads_list(tmp_path):
    """A valid vulnerabilities.json is returned as its list of entries."""
    path = tmp_path / "vulnerabilities.json"
    path.write_text(json.dumps({"vulnerabilities": SAMPLE_VULNERABILITIES}), encoding="utf-8")
    assert sbom.load_vulnerabilities(path) == SAMPLE_VULNERABILITIES


def test_load_vulnerabilities_missing_file(tmp_path):
    """If resolve wrote nothing, the stage fails with a clear message."""
    with pytest.raises(RuntimeError, match="did not produce"):
        sbom.load_vulnerabilities(tmp_path / "vulnerabilities.json")


def test_load_vulnerabilities_converts_underscore_keys(tmp_path, capsys):
    """resolve sbom writes cve_id; it is converted to cve-id, with a warning, in memory and on disk.

    Downstream stages (reach, remediate, CVEAssert) read the hyphenated keys
    from the file, so the file itself must be rewritten too.
    """
    expected = _vuln("CVE-2024-0001", "787")
    entry = {key.replace("-", "_"): value for key, value in expected.items()}
    path = tmp_path / "vulnerabilities.json"
    path.write_text(json.dumps({"vulnerabilities": [entry]}), encoding="utf-8")

    assert sbom.load_vulnerabilities(path) == [expected]
    assert json.loads(path.read_text(encoding="utf-8")) == {"vulnerabilities": [expected]}
    assert "::warning" in capsys.readouterr().out


def test_load_vulnerabilities_rejects_missing_keys(tmp_path):
    """An entry missing a key downstream stages need fails, naming the entry and the key."""
    entry = _vuln("CVE-2024-0001", "787")
    del entry["affected-file"]
    path = tmp_path / "vulnerabilities.json"
    path.write_text(json.dumps({"vulnerabilities": [entry]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"vulnerabilities\[0\] is missing \['affected-file'\]"):
        sbom.load_vulnerabilities(path)


# ----------------------------------------------------- write_per_cve_files ---


def test_write_per_cve_files_groups_by_cve(tmp_path):
    """One file per CVE, sorted by ID, each holding every CWE entry for that CVE."""
    paths = sbom.write_per_cve_files(SAMPLE_VULNERABILITIES, tmp_path / "cves")

    assert [p.name for p in paths] == ["CVE-2023-9999.json", "CVE-2024-0001.json"]

    first = json.loads(paths[1].read_text(encoding="utf-8"))
    assert [v["cwe-id"] for v in first["vulnerabilities"]] == ["787", "125"]

    second = json.loads(paths[0].read_text(encoding="utf-8"))
    assert second["vulnerabilities"] == [SAMPLE_VULNERABILITIES[2]]


# -------------------------------------------------------------------- main ---


def test_main_end_to_end(github_env, default_config, fake_resolve, monkeypatch):
    """A full run writes the per-CVE files, the step outputs and the step summary."""
    sbom_file = github_env.workspace / "example.spdx.json"
    sbom_file.write_text("{}", encoding="utf-8")
    output_dir = github_env.workspace / "sbom-out"
    summary_file = github_env.workspace / "summary.md"
    monkeypatch.setattr(sbom, "SBOM_PATH", str(sbom_file))
    monkeypatch.setattr(sbom, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    assert sbom.main() == 0

    assert (output_dir / "vulnerabilities.json").is_file()
    assert sorted(p.name for p in (output_dir / "cves").iterdir()) == [
        "CVE-2023-9999.json",
        "CVE-2024-0001.json",
    ]

    outputs = _read_github_outputs(github_env.output_file)
    assert outputs["vulnerabilities-path"] == str(output_dir / "vulnerabilities.json")
    assert outputs["cve-count"] == "2"
    assert json.loads(outputs["cve-files"]) == [
        "cves/CVE-2023-9999.json",
        "cves/CVE-2024-0001.json",
    ]

    summary = summary_file.read_text(encoding="utf-8")
    assert "Found **2** CVEs (3 CVE/CWE entries)" in summary
    assert "| CVE-2024-0001 | CWE-787" in summary


def test_main_fails_when_sbom_missing(github_env, default_config, fake_resolve, monkeypatch):
    """A missing SBOM returns 1 without ever running resolve."""
    monkeypatch.setattr(sbom, "SBOM_PATH", str(github_env.workspace / "missing.spdx.json"))
    monkeypatch.setattr(sbom, "OUTPUT_DIR", str(github_env.workspace / "sbom-out"))

    assert sbom.main() == 1
    assert fake_resolve == []


def test_main_returns_resolve_exit_code(github_env, default_config, monkeypatch):
    """If `resolve sbom` fails, the stage exits with the same code."""
    sbom_file = github_env.workspace / "example.spdx.json"
    sbom_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sbom, "SBOM_PATH", str(sbom_file))
    monkeypatch.setattr(sbom, "OUTPUT_DIR", str(github_env.workspace / "sbom-out"))

    def failing_run(command, **kwargs):
        raise subprocess.CalledProcessError(3, command)

    monkeypatch.setattr(subprocess, "run", failing_run)
    assert sbom.main() == 3
