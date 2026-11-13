"""Tests for scripts/setup.py, the input-synthesis setup stage wrapper.

The real stage drives a coding agent, so every test replaces subprocess.run
with a fake that records the command and environment it was given and then
does what the test asks: create the agent file and build/ (a successful
setup), create nothing, or fail with an exit code.

setup.py reads its inputs when it is imported, so the run_setup fixture sets
the INPUT_* variables first and then reloads the module.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import input_synthesis_setup as setup


KEY_VARIABLES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "OPENCODE_API_KEY")
AGENTS_FILE = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "opencode": "AGENTS.md"}
SECRET = "sk-test-not-a-real-key-123"


# ----------------------------------------------------------------- helpers ---

class FakeAgent:
    """Stands in for subprocess.run. Records each call and plays a scenario.

    Scenarios:
        "build"        write the agent file and build/app (a good setup)
        "nothing"      write nothing, exit 0
        "empty-file"   write an empty agent file and build/app
        "no-build"     write the agent file only
        "empty-build"  write the agent file and build/ with empty subfolders
        "wrong-file"   write CLAUDE.md and build/app whatever the agent is
        "fail"         raise CalledProcessError with exit code 3
    """

    def __init__(self, scenario="build"):
        self.scenario = scenario
        self.calls = []

    def __call__(self, command, check=False, env=None, **kwargs):
        self.calls.append({"command": command, "env": env})
        agents_file = AGENTS_FILE[command[3]]   # [python, -m, module, agent, cve]

        if self.scenario == "fail":
            raise subprocess.CalledProcessError(3, command)
        if self.scenario in ("build", "no-build", "empty-build"):
            Path(agents_file).write_text("# Build notes\n", encoding="utf-8")
        if self.scenario == "empty-file":
            Path(agents_file).touch()
        if self.scenario in ("build", "empty-file"):
            Path("build").mkdir()
            Path("build/app").write_bytes(b"\x7fELF")
        if self.scenario == "empty-build":
            Path("build/CMakeFiles/app.dir").mkdir(parents=True)
        if self.scenario == "wrong-file":
            Path("CLAUDE.md").write_text("# Build notes\n", encoding="utf-8")
            Path("build").mkdir()
            Path("build/app").write_bytes(b"\x7fELF")

        return subprocess.CompletedProcess(command, 0)


class SetupRun:
    """The result of one main() call."""

    def __init__(self, code, agent, workspace, output_file, summary_file, capsys):
        self.code = code
        self.agent = agent
        self.workspace = workspace
        self._output_file = output_file
        self.summary_file = summary_file
        captured = capsys.readouterr()
        self.stdout = captured.out
        self.stderr = captured.err

    @property
    def calls(self):
        return self.agent.calls

    @property
    def outputs(self):
        """Step outputs written to GITHUB_OUTPUT, as {name: value}."""
        outputs = {}
        lines = iter(self._output_file.read_text(encoding="utf-8").splitlines())
        for line in lines:
            name, delimiter = line.split("<<", 1)
            outputs[name] = next(lines)
            assert next(lines) == delimiter
        return outputs


@pytest.fixture
def run_setup(github_env, monkeypatch, capsys):
    """Return a function that runs setup.main() as a GitHub step.

    Keyword arguments become inputs (agent, api_key, model, target_dir,
    cve_path). cve_path defaults to a real file in the workspace. scenario
    picks what the fake agent does; env sets extra environment variables
    such as an *_API_KEY the runner already has.
    """
    workspace = github_env.workspace
    cve = workspace / "cve.json"
    cve.write_text('{"id": "CVE-2026-0001"}', encoding="utf-8")

    summary_file = workspace.parent / "step_summary"
    summary_file.write_text("previous step\n", encoding="utf-8")

    for name in KEY_VARIABLES + ("GITHUB_STEP_SUMMARY",):
        monkeypatch.delenv(name, raising=False)

    def run(scenario="build", env=None, summary=True, **inputs):
        inputs.setdefault("agent", "claude")
        inputs.setdefault("cve_path", str(cve))
        if inputs["agent"] in ("claude", "codex") and "api_key" not in inputs:
            inputs["api_key"] = SECRET

        github_env.set(**{f"INPUT_{name.upper()}": value for name, value in inputs.items()})
        for name, value in (env or {}).items():
            monkeypatch.setenv(name, value)
        if summary:
            monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

        # The action runs the script from the target dir (-w /work/<target-dir>).
        target = workspace / inputs.get("target_dir", ".")
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(target)

        agent = FakeAgent(scenario)
        monkeypatch.setattr(subprocess, "run", agent)
        importlib.reload(setup)

        code = setup.main()
        return SetupRun(code, agent, workspace, github_env.output_file, summary_file, capsys)

    return run


# --------------------------------------------------------- accepts inputs ---

@pytest.mark.parametrize("agent", ["claude", "codex", "opencode"])
def test_valid_agent_runs_the_stage(run_setup, agent):
    result = run_setup(agent=agent)

    assert result.code == 0
    assert len(result.calls) == 1
    assert result.calls[0]["command"] == [
        sys.executable, "-m", "resolve.input_synthesis.setup",
        agent, str(result.workspace / "cve.json"),
    ]


def test_model_is_passed_through(run_setup):
    result = run_setup(model="claude-opus-5-5")

    assert result.calls[0]["command"][-2:] == ["--model", "claude-opus-5-5"]


def test_no_model_flag_when_model_is_empty(run_setup):
    result = run_setup(model="")

    assert "--model" not in result.calls[0]["command"]


def test_agent_input_is_trimmed(run_setup):
    result = run_setup(agent="  codex  ", api_key=SECRET)

    assert result.code == 0
    assert result.calls[0]["command"][3] == "codex"


def test_inputs_are_read_without_prefix_outside_ci(local_env, monkeypatch, tmp_path, capsys):
    cve = tmp_path / "cve.json"
    cve.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for name in KEY_VARIABLES + ("GITHUB_STEP_SUMMARY",):
        monkeypatch.delenv(name, raising=False)
    local_env.set(AGENT="opencode", CVE_PATH=str(cve), MODEL="m1")
    agent = FakeAgent("build")
    monkeypatch.setattr(subprocess, "run", agent)
    importlib.reload(setup)

    assert setup.main() == 0
    assert agent.calls[0]["command"][3:] == ["opencode", str(cve), "--model", "m1"]
    assert "[output] agents-file=AGENTS.md" in capsys.readouterr().out


# ----------------------------------------------------------- rejects inputs ---

@pytest.mark.parametrize("agent", ["gpt", "Claude", "claude,codex"])
def test_unknown_agent_is_rejected(run_setup, agent):
    result = run_setup(agent=agent)

    assert result.code == 1
    assert result.calls == []
    assert f"unknown agent: {agent}" in result.stdout


def test_missing_cve_path_is_rejected(run_setup):
    result = run_setup(cve_path="")

    assert result.code == 1
    assert result.calls == []
    assert "cve-path input is required" in result.stdout


def test_cve_that_does_not_exist_is_rejected(run_setup, tmp_path):
    result = run_setup(cve_path=str(tmp_path / "missing.json"))

    assert result.code == 1
    assert result.calls == []
    assert "CVE not found" in result.stdout


def test_cve_that_is_a_directory_is_rejected(run_setup, tmp_path):
    result = run_setup(cve_path=str(tmp_path))

    assert result.code == 1
    assert result.calls == []


def test_agent_is_checked_before_cve(run_setup):
    result = run_setup(agent="gpt", cve_path="")

    assert "unknown agent" in result.stdout
    assert "cve-path" not in result.stdout


def test_rejected_input_writes_no_outputs(run_setup):
    result = run_setup(agent="gpt")

    assert result.outputs == {}
    assert result.summary_file.read_text(encoding="utf-8") == "previous step\n"


# ------------------------------------------------------------------- keys ---

@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_agent_that_needs_a_key_fails_without_one(run_setup, agent):
    result = run_setup(agent=agent, api_key="")

    assert result.code == 1
    assert result.calls == []
    assert "no API key" in result.stdout


@pytest.mark.parametrize("agent, variable", [
    ("claude", "ANTHROPIC_API_KEY"),
    ("codex", "OPENAI_API_KEY"),
    ("opencode", "OPENCODE_API_KEY"),
])
def test_key_input_goes_to_the_agents_variable(run_setup, agent, variable):
    result = run_setup(agent=agent, api_key=SECRET)

    run_env = result.calls[0]["env"]
    assert run_env[variable] == SECRET
    for other in set(KEY_VARIABLES) - {variable}:
        assert other not in run_env


def test_key_from_the_environment_is_used(run_setup):
    result = run_setup(api_key="", env={"ANTHROPIC_API_KEY": "from-runner"})

    assert result.code == 0
    assert result.calls[0]["env"]["ANTHROPIC_API_KEY"] == "from-runner"


def test_key_input_wins_over_the_environment(run_setup):
    result = run_setup(api_key=SECRET, env={"ANTHROPIC_API_KEY": "from-runner"})

    assert result.calls[0]["env"]["ANTHROPIC_API_KEY"] == SECRET


def test_claude_token_goes_to_the_oauth_variable(run_setup):
    """A `claude setup-token` token goes to CLAUDE_CODE_OAUTH_TOKEN, not ANTHROPIC_API_KEY."""
    token = "sk-ant-oat01-not-a-real-token"
    result = run_setup(api_key=token, env={"ANTHROPIC_API_KEY": "from-runner"})

    run_env = result.calls[0]["env"]
    assert result.code == 0
    assert run_env["CLAUDE_CODE_OAUTH_TOKEN"] == token
    assert "ANTHROPIC_API_KEY" not in run_env


def test_claude_token_from_the_environment_is_used(run_setup):
    token = "sk-ant-oat01-not-a-real-token"
    result = run_setup(api_key="", env={"CLAUDE_CODE_OAUTH_TOKEN": token})

    assert result.code == 0
    assert result.calls[0]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == token


def test_opencode_runs_without_a_key(run_setup):
    result = run_setup(agent="opencode", api_key="")

    assert result.code == 0
    assert "OPENCODE_API_KEY" not in result.calls[0]["env"]


def test_key_is_never_printed(run_setup):
    result = run_setup(api_key=SECRET, model="m1")

    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
    assert SECRET not in " ".join(result.calls[0]["command"])


def test_key_stays_out_of_this_process(run_setup):
    run_setup(api_key=SECRET)

    assert "ANTHROPIC_API_KEY" not in os.environ


# ----------------------------------------------- left something to build with ---

def test_successful_build_passes(run_setup):
    result = run_setup(scenario="build")

    assert result.code == 0
    assert "Setup complete" in result.stdout


@pytest.mark.parametrize("scenario, message", [
    ("nothing", "did not write CLAUDE.md"),
    ("empty-file", "did not write CLAUDE.md"),
    ("no-build", "build/ was not created"),
    ("empty-build", "build/ is empty"),
])
def test_missing_build_output_fails(run_setup, scenario, message):
    result = run_setup(scenario=scenario)

    assert result.code == 1
    assert message in result.stdout
    assert result.outputs == {}
    assert result.summary_file.read_text(encoding="utf-8") == "previous step\n"


def test_build_check_uses_the_agents_own_file(run_setup):
    # codex is expected to write AGENTS.md; a CLAUDE.md must not count.
    result = run_setup(agent="codex", scenario="wrong-file")

    assert result.code == 1
    assert "did not write AGENTS.md" in result.stdout


# ------------------------------------------------------------------ outputs ---

@pytest.mark.parametrize("agent, target_dir, expected", [
    ("claude", ".", "CLAUDE.md"),
    ("claude", "sub", "sub/CLAUDE.md"),
    ("claude", "sub/", "sub/CLAUDE.md"),
    ("codex", ".", "AGENTS.md"),
    ("opencode", "a/b", "a/b/AGENTS.md"),
])
def test_agents_file_output(run_setup, agent, target_dir, expected):
    result = run_setup(agent=agent, target_dir=target_dir)

    assert result.outputs == {"agents-file": expected}


def test_agent_failure_returns_its_exit_code(run_setup):
    result = run_setup(scenario="fail")

    assert result.code == 3
    assert "failed with exit code 3" in result.stdout
    assert result.outputs == {}


def test_summary_is_appended(run_setup):
    result = run_setup()

    summary = result.summary_file.read_text(encoding="utf-8")
    assert summary.startswith("previous step\n")
    assert "## Input-synthesis setup" in summary
    assert "**claude**" in summary


def test_no_summary_when_variable_is_unset(run_setup):
    result = run_setup(summary=False)

    assert result.code == 0
    assert result.summary_file.read_text(encoding="utf-8") == "previous step\n"
