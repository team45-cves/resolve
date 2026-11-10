"""Shared fixtures for every test under tests/.

pytest loads this file automatically before running any test, and every
fixture here is available to every test file without an import. Test files
never import conftest.py directly.

Fixtures (see tests/README.md for examples):
    sample_project   copy a project from tests/sample-projects/ into the workspace
    github_env       a simulated GitHub Actions step
    gitlab_env       a simulated GitLab CI job
    local_env        a run with no CI platform detected
    fixed_uuid       make uuid.uuid4() return the same value every time

The three *_env fixtures remove every CI variable from the environment,
set the ones their platform needs, and reload ci_env so its saved values
match. When the test ends, every change is undone and ci_env is reloaded
again, so nothing leaks into the next test.

ci_env saves its values when it is imported, so tests must read them as
ci_env.COMMIT_SHA (through the module), never with
"from ci_env import COMMIT_SHA", which would keep a stale copy.
"""

import importlib
import os
import shutil
import uuid
from pathlib import Path

import pytest

import ci_env


# Small fake C/C++ projects used as test input (not pytest fixtures).
SAMPLE_PROJECTS_DIR = Path(__file__).parent / "sample-projects"


# Every variable ci_env reads. The platform variables come from ci_env's own
# mapping, so a newly mapped variable is cleaned without editing this file.
_PLATFORM_VARIABLES = {
    variable
    for per_platform in ci_env._VARIABLES.values()
    for variable in per_platform.values()
}
_CONTROL_VARIABLES = {
    "GITHUB_ACTIONS",      # platform detection
    "GITLAB_CI",           # platform detection
    "GITHUB_OUTPUT",       # where GitHub step outputs go
    "RUNNER_DEBUG",        # IS_DEBUG on GitHub
    "CI_DEBUG_TRACE",      # IS_DEBUG on GitLab
    "CI_ENV_OUTPUT_FILE",  # GitLab dotenv file name
}


class SimulatedCi:
    """What the github_env, gitlab_env and local_env fixtures return.

    Attributes:
        platform:     "github", "gitlab" or "local".
        workspace:    the workspace folder, or None for a local run.
        output_file:  the file set_output() writes to, or None for a local run.
    """

    def __init__(self, monkeypatch, platform, workspace=None, output_file=None):
        self._monkeypatch = monkeypatch
        self.platform = platform
        self.workspace = workspace
        self.output_file = output_file

    def set(self, **variables):
        """Set environment variables, then reload ci_env so it sees them.

        A value of None removes the variable instead. Names that aren't valid
        Python keywords can be passed as a dict: set(**{"INPUT_BUILD-PATH": "dist"}).
        Returns the reloaded ci_env module.
        """
        for name, value in variables.items():
            if value is None:
                self._monkeypatch.delenv(name, raising=False)
            else:
                self._monkeypatch.setenv(name, str(value))
        importlib.reload(ci_env)
        return ci_env


def _simulate(monkeypatch, platform, variables, workspace=None, output_file=None):
    """Clear every CI variable, set the given ones, and reload ci_env."""
    for name in list(os.environ):
        if name in _PLATFORM_VARIABLES or name in _CONTROL_VARIABLES or name.startswith("INPUT_"):
            monkeypatch.delenv(name)
    for name, value in variables.items():
        monkeypatch.setenv(name, str(value))
    importlib.reload(ci_env)
    return SimulatedCi(monkeypatch, platform, workspace, output_file)


def _restore(monkeypatch):
    """Undo every environment change, then reload ci_env from the real environment."""
    monkeypatch.undo()
    importlib.reload(ci_env)


def _make_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


@pytest.fixture
def sample_project(tmp_path):
    """Return a function that copies tests/sample-projects/<name> into the workspace.

    The copy lands in tmp_path/workspace, the same folder github_env and
    gitlab_env use as the workspace, so a test can change it freely.
    Usage: project = sample_project("simple-build")
    """
    def copy(name):
        source = SAMPLE_PROJECTS_DIR / name
        if not source.is_dir():
            raise ValueError(f"No sample project named {name!r} in {SAMPLE_PROJECTS_DIR}")
        destination = _make_workspace(tmp_path)
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
        return destination

    return copy


@pytest.fixture
def github_env(monkeypatch, tmp_path):
    """A simulated GitHub Actions step.

    Sets GITHUB_ACTIONS=true, GITHUB_WORKSPACE to tmp_path/workspace, and
    GITHUB_OUTPUT to an empty file at tmp_path/github_output (the runner
    creates this file before each step), then reloads ci_env.
    """
    workspace = _make_workspace(tmp_path)
    output_file = tmp_path / "github_output"
    output_file.touch()
    yield _simulate(
        monkeypatch,
        "github",
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_WORKSPACE": workspace,
            "GITHUB_OUTPUT": output_file,
        },
        workspace=workspace,
        output_file=output_file,
    )
    _restore(monkeypatch)


@pytest.fixture
def gitlab_env(monkeypatch, tmp_path):
    """A simulated GitLab CI job.

    Sets GITLAB_CI=true and CI_PROJECT_DIR to tmp_path/workspace, then
    reloads ci_env. Outputs go to workspace/ci_outputs.env, which is not
    created until set_output() writes to it.
    """
    workspace = _make_workspace(tmp_path)
    yield _simulate(
        monkeypatch,
        "gitlab",
        {
            "GITLAB_CI": "true",
            "CI_PROJECT_DIR": workspace,
        },
        workspace=workspace,
        output_file=workspace / "ci_outputs.env",
    )
    _restore(monkeypatch)


@pytest.fixture
def local_env(monkeypatch):
    """A run outside CI: every CI variable removed and ci_env reloaded."""
    yield _simulate(monkeypatch, "local", {})
    _restore(monkeypatch)


@pytest.fixture
def fixed_uuid(monkeypatch):
    """Make uuid.uuid4() return the all-zero UUID for this test.

    set_output() on GitHub uses a random delimiter, so its output differs on
    every run. Pinning uuid4() makes the output the same for the same input,
    so it can be compared with a golden file. The delimiter becomes
    "EOF_" followed by 32 zeros.
    """
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0))

    