# RESOLVE tests

This folder holds the Python tests for the RESOLVE CI/CD platform: the core modules (`ci_env.py`, `logger.py`) and the target scripts (`action*.py`). This README covers running the tests and writing new ones.

## Contents

1. [Quick start](#quick-start)
2. [How testing is organised](#how-testing-is-organised)
3. [Running the tests](#running-the-tests)
4. [Folder layout](#folder-layout)
5. [Shared fixtures in conftest.py](#shared-fixtures-in-conftestpy)
6. [Writing a new test](#writing-a-new-test)
7. [Rules](#rules)
8. [Troubleshooting](#troubleshooting)

## Quick start

You need Docker. From the repo root:

```sh
cd CI-CD
docker build -f docker/pipeline/Dockerfile --build-arg AI_BACKEND=none -t resolve_ci:latest .
docker build -f docker/pipeline_test/Dockerfile -t resolve-test .
docker run --rm resolve-test
```

The commands are the same in a Linux or macOS terminal, PowerShell and Command Prompt. Every test should pass, apart from one expected failure marked `xfail` (see [Troubleshooting](#troubleshooting)).

## How testing is organised

Every change is checked in stages, cheapest first:

| Stage | What it checks | When |
| --- | --- | --- |
| Local runs | The same tests as CI, run in the test container on your machine | Before every push |
| Static checks and contract tests | YAML, shell and Dockerfile linting; every `action.yml` matches its script | Every commit |
| Unit tests in the image | Script and module logic, with the real tools installed | Pull requests and pushes to main |
| Smoke test | The image started exactly as a runner starts it | Pull requests and pushes to main |
| Real-world pipelines | The full tool on real C/C++ projects | Before each release |

The tests in this folder are the unit tests. They run in an image built on `docker/pipeline/Dockerfile`, the same image each action builds when it runs. CI repeats every check, so a local pass is a strong hint, not a gate.

## Running the tests

The tests run inside a Docker image, so they get the same Ubuntu, Python and tools as the actions on every machine.

### The two images

| Image | Built from | What it is |
| --- | --- | --- |
| `resolve_ci` | `docker/pipeline/Dockerfile` | The runtime image every action uses: RESOLVE, its toolchain, and the scripts from `scripts/`. |
| `resolve-test` | `docker/pipeline_test/Dockerfile` | `resolve_ci` plus pytest and the tests. `resolve_ci` itself isn't changed. |

Build both from the `CI-CD/` folder, which is the build context for each. `AI_BACKEND=none` leaves the AI tools out of `resolve_ci`; the unit tests don't use them, and the build is faster and needs no npm download.

The test image builds on whatever `resolve_ci:latest` currently is, so **rebuild in this order**:

| You changed | Rebuild |
| --- | --- |
| Anything in `scripts/`, or `docker/pipeline/Dockerfile` | Both images, `resolve_ci` first. The scripts are copied into `resolve_ci`, so rebuilding only `resolve-test` tests the old scripts. |
| Tests, sample projects, `pytest.ini` or `requirements.txt` | `resolve-test` only, or mount the tests instead (below) |

To build the test image on a `resolve_ci` with another tag, pass it as `BASE_IMAGE`:

```sh
docker build -f docker/pipeline_test/Dockerfile --build-arg BASE_IMAGE=resolve_ci:abc123 -t resolve-test .
```

### Choosing which tests run

Anything after the image name is passed to pytest. Paths are relative to `tests/`, the same as running pytest from `CI-CD/tests/` on your machine:

| Command | What it runs |
| --- | --- |
| `docker run --rm resolve-test` | Every test |
| `docker run --rm resolve-test logger` | One folder |
| `docker run --rm resolve-test logger/test_logger.py` | One file |
| `docker run --rm resolve-test ci_env/test_ci_env.py::test_detects_github` | One test |
| `docker run --rm resolve-test -k gitlab` | Tests with "gitlab" in their name (below) |
| `docker run --rm resolve-test -x` | Stop at the first failure |
| `docker run --rm resolve-test -v` | List every test by name as it runs |
| `docker run --rm resolve-test -v --durations=5` | Also show the five slowest tests |

Options can be combined, e.g. `docker run --rm resolve-test logger -k escaping -x`.

You can also put the options in the `PYTEST_ADDOPTS` environment variable, which pytest reads by itself. This is handy in scripts and CI:

```sh
docker run --rm -e PYTEST_ADDOPTS="-k gitlab -x" resolve-test
```

`--lf` (rerun the last failures) doesn't work in the image: pytest's cache is turned off and every container starts fresh, so there's never a previous run to remember.

### Selecting tests by name with -k

`-k` runs only the tests whose names match an expression; pytest reports the rest as "deselected". `-k gitlab` selects every test with "gitlab" anywhere in its name, such as `test_detects_gitlab`, `test_gitlab_output_matches_golden_file` and `test_same_output_on_every_platform[gitlab_env]`.

How the matching works:

- It's **case-insensitive**: `-k GitLab` matches the same tests.
- It matches **part of a name**, not just whole words: `-k output` matches `test_github_output_appends` and `test_local_output_is_printed`.
- It checks **more than the function name**: file and folder names count too, as do parameter labels in square brackets. That's why `[gitlab_env]` matches, and why `-k logger` selects every test in `tests/logger/`.

Terms can be combined with `and`, `or`, `not` and brackets. Quote the expression when it has spaces, so the shell passes it as one argument:

```sh
docker run --rm resolve-test -k "gitlab and output"      # GitLab output tests only
docker run --rm resolve-test -k "github or gitlab"       # either platform
docker run --rm resolve-test -k "not escaping"           # everything except the escaping tests
docker run --rm resolve-test -k "gitlab and not input"   # GitLab tests, minus the input ones
```

`-k` is for focusing on one area while you work on it. CI always runs everything.

### Keeping the JUnit report

The image writes a JUnit XML report to `/results/junit.xml` inside the container. Mount a folder there to keep it, from `CI-CD/`:

```sh
docker run --rm -v "${PWD}/test-results:/results" resolve-test     # bash, zsh, PowerShell
docker run --rm -v "%cd%/test-results:/results" resolve-test       # Command Prompt
```

The report includes each test's file and line number, which CI uses to put annotations on failing tests. `test-results/` is in `.gitignore`. On Linux, the file belongs to root, because the tests run as root inside the container.

### Running edited tests without rebuilding

The tests are copied into the image when it's built. To run your current files instead, mount `tests/` over the copy, from `CI-CD/`:

```sh
docker run --rm -v "${PWD}/tests:/app/tests:ro" resolve-test       # bash, zsh, PowerShell
docker run --rm -v "%cd%/tests:/app/tests:ro" resolve-test         # Command Prompt
```

This covers tests, sample projects and `pytest.ini`. Changes to `scripts/` or `requirements.txt` still need a rebuild.

### Debugging inside the image

Open a shell in the test image instead of running the tests:

```sh
docker run -it --rm --entrypoint bash resolve-test
```

You start in `/app/tests`. Run the tests there with `/opt/test-venv/bin/python -m pytest`, or explore the image.

### What's inside the test image

| Path | What it is |
| --- | --- |
| `/app/actions/` | The scripts, copied from `scripts/` by `resolve_ci` |
| `/app/scripts` | A link to `/app/actions`, so `pytest.ini`'s `pythonpath = ../scripts` works unchanged |
| `/app/tests/` | A copy of `tests/`, and the folder pytest runs from |
| `/opt/test-venv/` | A virtual environment holding pytest. Ubuntu's Python has no pip and blocks system-wide installs, so pytest is kept separate. It uses the same Python as the actions. |
| `/results/` | Where `junit.xml` is written |

The image's entrypoint runs pytest from `/app/tests` with three fixed options: `-p no:cacheprovider` (no warnings when `tests/` is mounted read-only), `-o junit_family=xunit1` (file and line numbers in the report) and `--junitxml=/results/junit.xml`.

### Without Docker, for quick checks

You can also run pytest directly on your machine. This is quick while editing, but it doesn't have the Ubuntu tools, so tests that need them fail or can't run. Always use the image before pushing.

You need Python 3.12 or newer. From `CI-CD/`, create a virtual environment and install pytest once:

```sh
python3 -m venv .venv                              # Windows: py -3.12 -m venv .venv
source .venv/bin/activate                          # PowerShell: .venv\Scripts\Activate.ps1
                                                   # Command Prompt: .venv\Scripts\activate.bat
python -m pip install -r tests/requirements.txt
```

Then **run pytest from inside `tests/`**:

```sh
cd tests
pytest
pytest -k gitlab
```

`tests/pytest.ini` tells pytest where the scripts are, and pytest only finds it when run from `tests/` or a folder below it:

```ini
[pytest]
minversion = 8.0          # fail clearly on an older pytest
testpaths = .
pythonpath = ../scripts   # lets tests import ci_env, logger and the action scripts
```

Running `pytest` from `CI-CD/` or the repo root fails with `module 'ci_env' has no attribute '_VARIABLES'`. Every way of choosing tests above works the same here, including `--lf`, because the local cache is kept. Run `deactivate` to leave the virtual environment.

## Folder layout

```
CI-CD/
├── .gitignore                      # .venv, test results, caches, .env
├── .env.example                    # template for local API keys
├── docker/
│   ├── pipeline/
│   │   └── Dockerfile              # resolve_ci: the image every action uses
│   └── pipeline_test/
│       └── Dockerfile              # resolve-test: resolve_ci plus pytest and the tests
├── scripts/                        # ci_env.py, logger.py, action*.py, start_action.sh
    └── tests/
        ├── README.md
        ├── pytest.ini              # where scripts are, minimum pytest version
        ├── conftest.py             # shared fixtures for every test
        ├── requirements.txt        # pytest and test helpers, pinned
        ├── test_contracts.py       # (planned) action.yml vs scripts
        ├── sample-projects/        # small fake C/C++ projects used as test input
        │   └── simple-build/
        │       ├── src/hello.c
        │       └── dist/hello.txt
        ├── ci_env/
        │   ├── test_ci_env.py
        │   └── expected/
        │       ├── github_output.txt
        │       └── ci_outputs.env
        ├── logger/                 # (planned)
        │   ├── test_logger.py
        │   └── expected/
        │       └── annotations.txt
        └── actionN/                # (planned) one per target script
            ├── conftest.py         # fixtures used only by this action
            ├── test_actionN.py
            └── expected/
```

Each core module and each target script gets its own folder with its tests and an `expected/` folder of golden files: the exact output the tests compare against. Sample projects stay in the shared `sample-projects/` folder, because several scripts and the smoke test use the same ones.

In this README, a **sample project** is a small fake C/C++ project in `sample-projects/`, and a **fixture** always means a pytest fixture: a setup function from `conftest.py`. The folder isn't called `fixtures/` so the two don't get mixed up.

### Test data files

| File or folder | What it is |
| --- | --- |
| `sample-projects/` | Small fake C/C++ projects used as test input. `simple-build` is a one-file C project with a tiny `dist/` build. Tests copy one into a temporary workspace with the `sample_project` fixture, so they can change it freely. |
| `<module>/expected/` | Golden files: the exact output one module or script should produce. When an output format changes on purpose, update its golden file in the same commit. |
| `requirements.txt` | pytest and other test helpers, pinned to exact versions so results don't change when a new release comes out. |

`CI-CD/.gitignore` keeps the sample projects' `dist/` folders in git even if the repo ignores `dist/` elsewhere.

## Shared fixtures in conftest.py

`conftest.py` is loaded by pytest automatically. Any fixture in it can be used by every test below `tests/`, with no import: name the fixture as a parameter of your test function and pytest runs it first and passes in the result. **Never import `conftest.py`.**

| Fixture | Gives you |
| --- | --- |
| `github_env` | A simulated GitHub Actions step |
| `gitlab_env` | A simulated GitLab CI job |
| `local_env` | A run outside CI, with every CI variable removed |
| `sample_project` | A function that copies a project from `tests/sample-projects/` into the workspace |
| `fixed_uuid` | Makes `uuid.uuid4()` return the same value every time, for golden-file tests |

### github_env, gitlab_env and local_env

Each one clears every CI variable from the environment, sets the ones its platform needs, and reloads `ci_env` so its values match. When the test ends, every change is undone and `ci_env` is reloaded again, so nothing leaks into the next test, even if the test fails.

| | `github_env` | `gitlab_env` | `local_env` |
| --- | --- | --- | --- |
| Platform flag | `GITHUB_ACTIONS=true` | `GITLAB_CI=true` | none |
| Workspace | `GITHUB_WORKSPACE` → a temp folder | `CI_PROJECT_DIR` → a temp folder | none |
| Outputs go to | `GITHUB_OUTPUT` → an empty temp file | `ci_outputs.env` in the workspace (created on first write) | printed |

Each fixture returns an object with:

| Attribute or method | What it is |
| --- | --- |
| `.platform` | `"github"`, `"gitlab"` or `"local"` |
| `.workspace` | The workspace folder (a `Path`), or `None` for `local_env` |
| `.output_file` | The file `set_output()` writes to, or `None` for `local_env` |
| `.set(NAME=value, ...)` | Set variables and reload `ci_env` in one step. `None` removes a variable. |

Setting a variable and reading it back:

```python
import ci_env

def test_reads_commit_sha(github_env):
    github_env.set(GITHUB_SHA="abc123")
    assert ci_env.COMMIT_SHA == "abc123"
```

Inputs use `INPUT_<NAME>` on GitHub and the bare name on GitLab:

```python
def test_reads_build_path_input(github_env):
    github_env.set(INPUT_BUILD_PATH="dist")
    assert ci_env.get_input("build_path") == "dist"

def test_reads_build_path_input_on_gitlab(gitlab_env):
    gitlab_env.set(BUILD_PATH="dist")
    assert ci_env.get_input("build_path") == "dist"
```

Names that aren't valid Python keywords, such as inputs with hyphens, go in a dict:

```python
def test_hyphenated_input(github_env):
    github_env.set(**{"INPUT_BUILD-PATH": "dist"})
    assert ci_env.get_input("build-path") == "dist"
```

Checking what `set_output()` wrote:

```python
def test_writes_report_file_output(github_env):
    ci_env.set_output("report_file", "build-report.json")
    assert "report_file<<" in github_env.output_file.read_text(encoding="utf-8")

def test_writes_dotenv_output(gitlab_env):
    ci_env.set_output("findings", 3)
    assert gitlab_env.output_file.read_text(encoding="utf-8") == "findings=3\n"

def test_prints_outputs_locally(local_env, capsys):
    ci_env.set_output("findings", 3)
    assert capsys.readouterr().out == "[output] findings=3\n"
```

Removing a variable the fixture set, for example to test what happens without `GITHUB_OUTPUT`:

```python
def test_missing_output_file(github_env):
    github_env.set(GITHUB_OUTPUT=None)
    ...
```

### Read values through the module

`ci_env` reads the environment once, when it is imported, and the fixtures reload it to pick up new values. Reloading updates the module, but not copies taken from it. So always read values as `ci_env.COMMIT_SHA`:

```python
import ci_env                    # good: ci_env.COMMIT_SHA follows every reload
from ci_env import COMMIT_SHA    # bad: keeps the value from when the file loaded
```

This applies to **target scripts as well as tests**. A script that does `from ci_env import WORKSPACE` at the top captures the workspace once, before any fixture runs, and its tests will see the wrong folder. Scripts should `import ci_env` and use `ci_env.WORKSPACE`.

You don't need to call `importlib.reload(ci_env)` yourself; `.set()` does it.

### sample_project

Returns a function that copies a project from `tests/sample-projects/<name>` into `tmp_path/workspace`, which is the same folder `github_env` and `gitlab_env` use as the workspace. Use them together and the project lands where the script expects it. The test gets its own copy, so it can change files freely.

```python
def test_project_lands_in_workspace(github_env, sample_project):
    project = sample_project("simple-build")
    assert project == github_env.workspace
    assert (github_env.workspace / "dist").is_dir()
```

Asking for a project that doesn't exist raises a `ValueError` naming the sample-projects folder.

### fixed_uuid

`set_output()` on GitHub separates each value with a random delimiter (`EOF_` plus a random UUID), so the `GITHUB_OUTPUT` file is different on every run and can't be compared with a golden file. Asking for `fixed_uuid` makes `uuid.uuid4()` return the all-zero UUID for that test, so the delimiter is always `EOF_` followed by 32 zeros:

```python
def test_github_output_matches_golden_file(github_env, fixed_uuid):
    ci_env.set_output("findings", 3)
    expected = "findings<<EOF_" + "0" * 32 + "\n3\nEOF_" + "0" * 32 + "\n"
    assert github_env.output_file.read_text(encoding="utf-8") == expected
```

It affects every `uuid.uuid4()` call during the test, not just `set_output()`'s, and is undone when the test ends. Use it only in tests that compare output exactly.

### pytest's built-in fixtures

These come with pytest and are requested by name in the same way.

| Fixture | Use it for |
| --- | --- |
| `tmp_path` | A new, empty folder for this test only |
| `monkeypatch` | Temporary changes undone when the test ends: `setenv`, `delenv`, `setattr`, `chdir` |
| `capsys` | Capturing printed output, e.g. logger's annotation lines |

Inside a `github_env`/`gitlab_env`/`local_env` test, prefer `.set()` over `monkeypatch.setenv` for CI variables, because `.set()` also reloads `ci_env`. A plain `monkeypatch.setenv` has no effect on `ci_env` until it is reloaded.

## Writing a new test

### A new target script

Create a folder with the same shape as the others:

```
tests/actionN/
├── test_actionN.py
├── conftest.py          # only if this action needs its own fixtures
└── expected/
```

A typical test copies a sample project, sets the inputs, runs the script, and compares the result with a golden file. The example below is illustrative; replace `build_report.main()` with however your script is called:

```python
from pathlib import Path

import ci_env
import build_report

EXPECTED = Path(__file__).parent / "expected"
EXPECTED_REPORT = EXPECTED / "simple-build-report.json"

def test_report_for_simple_build(github_env, sample_project):
    """The report for the simple-build project matches the golden file."""
    sample_project("simple-build")
    github_env.set(INPUT_BUILD_PATH="dist")

    build_report.main()

    actual = (github_env.workspace / "build-report.json").read_text(encoding="utf-8")
    assert actual == EXPECTED_REPORT.read_text(encoding="utf-8")
```

Keep the paths to golden files as constants at the top of the test file, as above. When the report format changes on purpose, update the golden file in the same commit.

### Fixtures for just one folder

Setup used by only one action goes in that folder's own `conftest.py`. It can use the shared fixtures too:

```python
# tests/actionN/conftest.py
import pytest

@pytest.fixture
def report_path(github_env):
    """Where this action writes its report."""
    return github_env.workspace / "build-report.json"
```

Setup used by more than one folder belongs in the top-level `conftest.py`.

### Code that runs external tools

Logic that works on a tool's output (parsing, counting findings, building the report) is tested with a fake `subprocess.run`, so the test is fast and doesn't depend on the tool's version:

```python
import subprocess

def test_parses_findings(monkeypatch):
    fake_xml = "<results><error id='nullPointer' severity='error'/></results>"

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr=fake_xml)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ...
```

This only works if the script calls `subprocess.run(...)`. A script that does `from subprocess import run` keeps its own copy, and the fake never reaches it.

Tests that run the real tool on a sample project need the tool installed, so run them in the test container.

### Document every test

Give each test a docstring saying what it checks and why, as in `ci_env/test_ci_env.py`. pytest shows the test name in failures, and the docstring tells the next person what broke.

## Rules

- **No test may depend on a secret.** Pull requests from forks can't access secrets, so those tests would fail for outside contributors.
- **Shared setup goes in a `conftest.py` fixture**, not copied between test files.
- **Scripts must give the same output for the same input**, or golden-file comparisons can't work. Anything random, such as the delimiter `set_output()` uses on GitHub, has to be pinned in the test. For `set_output()`, use the `fixed_uuid` fixture.
- **Test dependencies are pinned** to exact versions in `requirements.txt`. To add one, add it there with its version.
- **Read `ci_env` values through the module**, in tests and scripts alike.

## Troubleshooting

**`AttributeError: module 'ci_env' has no attribute '_VARIABLES'`**
pytest couldn't find `ci_env.py`. Because `tests/ci_env/` is a folder with the same name, Python imports that folder as an empty package instead, and the error doesn't mention the real cause. On your machine, check that you're running pytest from `tests/`. In the test image, check that `resolve_ci` still copies the scripts to `/app/actions/`, which `/app/scripts` links to.

**`SyntaxError` when importing `ci_env`**
Your Python is older than 3.12. The `get_input()` line in `ci_env.py` reuses double quotes inside an f-string, which only 3.12 and newer accept. Use Python 3.12+, or change the inner quotes to single quotes and any Python 3 will work.

**`error: externally-managed-environment` from pip**
The Python was installed by the operating system (`apt`), which blocks system-wide installs. On your machine, install into a virtual environment, as in [Without Docker](#without-docker-for-quick-checks). The test image already does this, in `/opt/test-venv`.

**`PytestCacheWarning: could not create cache path`**
pytest is trying to save its cache into a read-only folder. The test image already passes `-p no:cacheprovider`; add it if you run pytest some other way on a read-only `tests/` mount.

**`pull access denied for resolve_ci` or `resolve_ci:latest: not found` when building the test image**
The test image builds on `resolve_ci:latest`, which has to exist on your machine first. Build it with the first command in [Quick start](#quick-start), from `CI-CD/`.

**A script change has no effect in the test image**
The scripts are copied into `resolve_ci`, not `resolve-test`. Rebuild `resolve_ci` first, then `resolve-test`.

**`ERROR: file or directory not found`**
A path you passed doesn't exist. Paths are relative to `tests/`: use `logger`, not `CI-CD/tests/logger`. pytest stops with exit code 4.

**Every test `deselected`, and `no tests ran`**
Your `-k` expression didn't match any test name. Check the spelling, or run with `--collect-only -q` to list the test names. pytest exits with code 5 when no tests run, so CI treats this as a failure.

**A test marked `xfail` in the results**
`test_gitlab_input_does_not_collide_with_system_variables` documents a known bug: on GitLab and locally, `get_input("path")` returns the system `PATH`. It's expected to fail. Once `get_input()` is fixed it will start passing, and because it's marked `strict=True` the suite will then fail as a reminder to remove the marker.

**A test passes alone but fails in the full run, or the other way round**
Something is leaking between tests. Make sure the test uses `github_env`, `gitlab_env` or `local_env` rather than setting CI variables with `monkeypatch` directly, and that it reads values as `ci_env.X` rather than importing them.