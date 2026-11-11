"""Unit tests for ci_env.py.

ci_env hides the differences between GitHub Actions and GitLab CI behind
neutral names. These tests check each part of that contract:

    1. Platform detection      which CI system ci_env thinks it is running on
    2. Saved values            COMMIT_SHA, WORKSPACE, etc. read at import
    3. IS_DEBUG                debug logging switch
    4. metadata()              all saved values as one dict
    5. get_input()             reading action / job inputs
    6. set_output() on GitHub  heredoc entries in the GITHUB_OUTPUT file
    7. set_output() on GitLab  KEY=value lines in a dotenv file
    8. set_output() locally    printed instead of written

Every test uses one of the github_env, gitlab_env or local_env fixtures from
tests/conftest.py. They give the test a clean environment and reload ci_env,
and test.set(NAME=value) changes a variable and reloads ci_env again.
"""

from pathlib import Path
import pytest
import ci_env


# Golden files the set_output() tests compare against.
EXPECTED = Path(__file__).parent / "expected"
EXPECTED_GITHUB_OUTPUT = EXPECTED / "github_output.txt"   # GITHUB_OUTPUT heredoc entries
EXPECTED_GITLAB_OUTPUT = EXPECTED / "gitlab_output.env"      # GitLab dotenv lines

# The delimiter set_output() writes on GitHub when uuid4() is pinned to the
# all-zero UUID by the fixed_uuid fixture in tests/conftest.py.
FIXED_DELIMITER = "EOF_" + "0" * 32

# The contract: neutral name -> (GitHub variable, GitLab variable).
# Written out here rather than read from ci_env._VARIABLES, so an accidental
# change to the mapping in ci_env fails a test instead of silently passing.
# WORKSPACE is tested separately because it becomes a resolved Path.
MAPPING = {
    #  neutral name    GitHub Actions                     GitLab CI
    "COMMIT_SHA":  {"github": "GITHUB_SHA",        "gitlab": "CI_COMMIT_SHA"},
    "REF_NAME":    {"github": "GITHUB_REF_NAME",   "gitlab": "CI_COMMIT_REF_NAME"},
    "REPOSITORY":  {"github": "GITHUB_REPOSITORY", "gitlab": "CI_PROJECT_PATH"},
    "RUN_ID":      {"github": "GITHUB_RUN_ID",     "gitlab": "CI_PIPELINE_ID"},
    "RUN_NUMBER":  {"github": "GITHUB_RUN_NUMBER", "gitlab": "CI_PIPELINE_IID"},
    "JOB_NAME":    {"github": "GITHUB_JOB",        "gitlab": "CI_JOB_NAME"},
    "EVENT":       {"github": "GITHUB_EVENT_NAME", "gitlab": "CI_PIPELINE_SOURCE"},
    "ACTOR":       {"github": "GITHUB_ACTOR",      "gitlab": "GITLAB_USER_LOGIN"},
    "SERVER_URL":  {"github": "GITHUB_SERVER_URL", "gitlab": "CI_SERVER_URL"},
}


def parse_github_output(text):
    """Read a GITHUB_OUTPUT file back into a list of (name, value, delimiter).

    Each entry has the form:
        name<<DELIMITER
        value (one or more lines)
        DELIMITER
    """
    entries = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue
        name, delimiter = lines[i].split("<<", 1)
        i += 1
        value_lines = []
        while lines[i] != delimiter:
            value_lines.append(lines[i])
            i += 1
        entries.append((name, "\n".join(value_lines), delimiter))
        i += 1
    return entries


# ------------------------------------------------------ platform detection ---


def test_detects_github(github_env):
    """GITHUB_ACTIONS=true means the code is running in a GitHub Actions step."""
    assert ci_env.PLATFORM == "github"


def test_detects_gitlab(gitlab_env):
    """GITLAB_CI=true means the code is running in a GitLab CI job."""
    assert ci_env.PLATFORM == "gitlab"


def test_detects_local(local_env):
    """With neither flag set, the code is running outside CI (a developer's machine)."""
    assert ci_env.PLATFORM == "local"


def test_github_wins_when_both_flags_set(local_env):
    """If both flags are set, GitHub is chosen, because it is checked first.

    This can happen when a GitLab job's variables leak into a GitHub run or
    the other way round; the result must be predictable.
    """
    local_env.set(GITHUB_ACTIONS="true", GITLAB_CI="true")
    assert ci_env.PLATFORM == "github"


@pytest.mark.parametrize("flag", ["GITHUB_ACTIONS", "GITLAB_CI"])
@pytest.mark.parametrize("value", ["True", "TRUE", "1", "yes", " true", ""])
def test_flag_must_be_exactly_true(local_env, flag, value):
    """Only the exact lowercase string "true" turns a flag on.

    Both runners set the flag to "true", so anything else (capitalised,
    "1", padded with spaces, empty) is treated as not running in CI.
    """
    local_env.set(**{flag: value})
    assert ci_env.PLATFORM == "local"


# ------------------------------------------------------------ saved values ---


@pytest.mark.parametrize("attribute", MAPPING)
def test_github_value_read_from_github_variable(github_env, attribute):
    """On GitHub, each neutral name is read from its GITHUB_* variable."""
    github_env.set(**{MAPPING[attribute]["github"]: f"github-{attribute}"})
    assert getattr(ci_env, attribute) == f"github-{attribute}"


@pytest.mark.parametrize("attribute", MAPPING)
def test_gitlab_value_read_from_gitlab_variable(gitlab_env, attribute):
    """On GitLab, each neutral name is read from its CI_* / GITLAB_* variable."""
    gitlab_env.set(**{MAPPING[attribute]["gitlab"]: f"gitlab-{attribute}"})
    assert getattr(ci_env, attribute) == f"gitlab-{attribute}"


@pytest.mark.parametrize("attribute", MAPPING)
def test_github_ignores_gitlab_variables(github_env, attribute):
    """On GitHub, a GitLab variable with the same meaning is ignored.

    Only the running platform's variables count, so a stray CI_COMMIT_SHA
    can't override the real commit on GitHub.
    """
    github_env.set(**{MAPPING[attribute]["gitlab"]: "from-gitlab"})
    assert getattr(ci_env, attribute) is None


@pytest.mark.parametrize("attribute", MAPPING)
def test_gitlab_ignores_github_variables(gitlab_env, attribute):
    """On GitLab, a GitHub variable with the same meaning is ignored."""
    gitlab_env.set(**{MAPPING[attribute]["github"]: "from-github"})
    assert getattr(ci_env, attribute) is None


def test_local_ignores_all_platform_variables(local_env):
    """Outside CI, every saved value is None, even if CI variables happen to be set.

    A developer's shell might export GITHUB_SHA for some other reason; ci_env
    must not treat that as a CI run.
    """
    local_env.set(GITHUB_SHA="abc", CI_COMMIT_SHA="def", GITHUB_WORKSPACE="/tmp")
    assert ci_env.COMMIT_SHA is None
    assert ci_env.WORKSPACE is None


def test_unset_value_is_none(github_env):
    """A variable the runner didn't set comes back as None, not an empty string."""
    assert ci_env.COMMIT_SHA is None


def test_empty_value_is_none(github_env):
    """An empty variable is treated the same as a missing one: None."""
    github_env.set(GITHUB_SHA="")
    assert ci_env.COMMIT_SHA is None


def test_github_workspace_is_resolved_path(github_env):
    """WORKSPACE is a pathlib.Path with symlinks and '..' resolved, not a string."""
    assert isinstance(ci_env.WORKSPACE, Path)
    assert ci_env.WORKSPACE == github_env.workspace.resolve()


def test_gitlab_workspace_read_from_ci_project_dir(gitlab_env):
    """On GitLab, WORKSPACE comes from CI_PROJECT_DIR."""
    assert ci_env.WORKSPACE == gitlab_env.workspace.resolve()


def test_relative_workspace_becomes_absolute(github_env, tmp_path, monkeypatch):
    """A relative workspace path is resolved against the current folder at import."""
    monkeypatch.chdir(tmp_path)
    github_env.set(GITHUB_WORKSPACE="workspace")
    assert ci_env.WORKSPACE == (tmp_path / "workspace").resolve()
    assert ci_env.WORKSPACE.is_absolute()


def test_empty_workspace_is_none(github_env):
    """An empty workspace variable gives None, not the current folder."""
    github_env.set(GITHUB_WORKSPACE="")
    assert ci_env.WORKSPACE is None


def test_values_saved_at_import(github_env, monkeypatch):
    """Values are read once, when ci_env is imported, and don't follow later changes.

    Changing a variable afterwards has no effect until ci_env is reloaded,
    which is why the fixtures reload it.
    """
    github_env.set(GITHUB_SHA="first")
    monkeypatch.setenv("GITHUB_SHA", "second")   # changed without reloading
    assert ci_env.COMMIT_SHA == "first"


# ---------------------------------------------------------------- IS_DEBUG ---


@pytest.mark.parametrize(
    "variables, expected",
    [
        ({}, False),
        ({"RUNNER_DEBUG": "1"}, True),           # GitHub's "debug logging" switch
        ({"CI_DEBUG_TRACE": "true"}, True),      # GitLab's debug trace switch
        ({"RUNNER_DEBUG": "true"}, False),       # GitHub uses "1", not "true"
        ({"RUNNER_DEBUG": "0"}, False),
        ({"CI_DEBUG_TRACE": "1"}, False),        # GitLab uses "true", not "1"
        ({"CI_DEBUG_TRACE": "false"}, False),
    ],
)
def test_is_debug(local_env, variables, expected):
    """IS_DEBUG is on only for each platform's exact debug value.

    It doesn't depend on the detected platform, so it is tested with no
    platform set.
    """
    local_env.set(**variables)
    assert ci_env.IS_DEBUG is expected


# -------------------------------------------------------------- metadata() ---


def test_metadata_github(github_env):
    """metadata() returns every saved value under its lowercase name.

    The workspace is a string, so the dict can go straight into a JSON report.
    """
    github_env.set(
        GITHUB_SHA="abc123",
        GITHUB_REF_NAME="main",
        GITHUB_REPOSITORY="owner/repo",
        GITHUB_RUN_ID="1001",
        GITHUB_RUN_NUMBER="7",
        GITHUB_JOB="build",
        GITHUB_EVENT_NAME="push",
        GITHUB_ACTOR="octocat",
        GITHUB_SERVER_URL="https://github.com",
    )
    assert ci_env.metadata() == {
        "platform": "github",
        "workspace": str(github_env.workspace.resolve()),
        "commit_sha": "abc123",
        "ref_name": "main",
        "repository": "owner/repo",
        "run_id": "1001",
        "run_number": "7",
        "job_name": "build",
        "event": "push",
        "actor": "octocat",
        "server_url": "https://github.com",
    }


def test_metadata_local(local_env):
    """Outside CI, metadata() still has every key, with only the platform filled in.

    Reports keep the same shape whether or not they were made in CI.
    """
    data = ci_env.metadata()
    assert data["platform"] == "local"
    assert all(value is None for key, value in data.items() if key != "platform")
    assert len(data) == 11


# ------------------------------------------------------------- get_input() ---


def test_github_input_read_from_input_prefix(github_env):
    """On GitHub, input build_path is read from INPUT_BUILD_PATH, as the runner sets it."""
    github_env.set(INPUT_BUILD_PATH="dist")
    assert ci_env.get_input("build_path") == "dist"


def test_input_name_is_case_insensitive(github_env):
    """The input name is upper-cased, so callers can use any case."""
    github_env.set(INPUT_BUILD_PATH="dist")
    assert ci_env.get_input("BUILD_PATH") == "dist"
    assert ci_env.get_input("Build_Path") == "dist"


def test_github_input_with_hyphen(github_env):
    """Hyphens are kept: input build-path is read from INPUT_BUILD-PATH.

    GitHub upper-cases input names but doesn't change hyphens, so this must
    match for action.yml inputs that use hyphens.
    """
    github_env.set(**{"INPUT_BUILD-PATH": "dist"})
    assert ci_env.get_input("build-path") == "dist"


def test_input_whitespace_is_stripped(github_env):
    """Leading and trailing spaces and newlines are removed from input values.

    Multi-line YAML inputs often end with a newline the script doesn't want.
    """
    github_env.set(INPUT_BUILD_PATH="  dist \n")
    assert ci_env.get_input("build_path") == "dist"


def test_missing_input_returns_default(github_env):
    """An input that wasn't set returns the default, or None without one."""
    assert ci_env.get_input("build_path", default="build") == "build"
    assert ci_env.get_input("build_path") is None


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_blank_input_returns_default(github_env, value):
    """An input that is empty or only whitespace counts as not set.

    GitHub passes optional inputs the user left out as empty strings, so
    these must fall back to the default.
    """
    github_env.set(INPUT_BUILD_PATH=value)
    assert ci_env.get_input("build_path", default="build") == "build"


def test_github_ignores_input_without_prefix(github_env):
    """On GitHub, a variable without the INPUT_ prefix is not an input."""
    github_env.set(BUILD_PATH="dist")
    assert ci_env.get_input("build_path") is None


def test_gitlab_input_read_without_prefix(gitlab_env):
    """On GitLab, input build_path is read from the job variable BUILD_PATH."""
    gitlab_env.set(BUILD_PATH="dist")
    assert ci_env.get_input("build_path") == "dist"


def test_gitlab_ignores_input_prefix(gitlab_env):
    """On GitLab, INPUT_BUILD_PATH is not read; only BUILD_PATH is."""
    gitlab_env.set(INPUT_BUILD_PATH="dist")
    assert ci_env.get_input("build_path") is None


def test_local_input_read_without_prefix(local_env):
    """Outside CI, inputs are read like GitLab: from BUILD_PATH, without the prefix."""
    local_env.set(BUILD_PATH="dist")
    assert ci_env.get_input("build_path") == "dist"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known issue: on GitLab and locally get_input() reads the bare name, "
        "so an input called 'path' returns the system PATH variable. "
        "Remove this marker once get_input() is fixed."
    ),
)
def test_gitlab_input_does_not_collide_with_system_variables(gitlab_env):
    """An input that wasn't set must return its default, even if its name matches
    a system variable such as PATH or HOME.

    Currently fails, so it is marked xfail. strict=True makes the suite fail
    once the bug is fixed, as a reminder to remove the marker.
    """
    assert ci_env.get_input("path", default="unset") == "unset"


# -------------------------------------------------- set_output() on GitHub ---


def test_github_output_matches_golden_file(github_env, fixed_uuid):
    """GitHub outputs are written in heredoc form and match expected/github_output.txt.

    Covers a plain string, a number (converted to text) and a multi-line value.
    """
    ci_env.set_output("report_file", "build-report.json")
    ci_env.set_output("findings", 3)
    ci_env.set_output("summary", "2 errors\n1 warning")

    actual = github_env.output_file.read_text(encoding="utf-8")
    expected = EXPECTED_GITHUB_OUTPUT.read_text(encoding="utf-8")
    assert actual == expected


def test_github_multiline_value_round_trips(github_env):
    """A multi-line value reads back exactly as written, with the real random delimiter."""
    value = "line one\nline two\n\nline four"
    ci_env.set_output("summary", value)

    [(name, read_back, _)] = parse_github_output(github_env.output_file.read_text(encoding="utf-8"))
    assert (name, read_back) == ("summary", value)


def test_github_delimiter_is_unique(github_env):
    """Each output gets its own random delimiter.

    A fixed delimiter would let a value containing that word end the entry
    early and inject fake outputs.
    """
    ci_env.set_output("first", "a")
    ci_env.set_output("second", "b")

    entries = parse_github_output(github_env.output_file.read_text(encoding="utf-8"))
    delimiters = [delimiter for _, _, delimiter in entries]
    assert len(set(delimiters)) == 2
    assert all(delimiter.startswith("EOF_") for delimiter in delimiters)


def test_github_output_appends(github_env):
    """set_output() adds to GITHUB_OUTPUT instead of overwriting it.

    Other tools in the same step may already have written outputs there.
    """
    github_env.output_file.write_text("existing=1\n", encoding="utf-8")
    ci_env.set_output("report_file", "build-report.json")

    text = github_env.output_file.read_text(encoding="utf-8")
    assert text.startswith("existing=1\n")
    assert "report_file<<" in text


def test_github_non_string_values_use_str(github_env):
    """Non-string values are converted with str().

    Note that True becomes "True", so a workflow checking
    steps.<id>.outputs.<name> == 'true' won't match. Scripts should pass
    "true"/"false" strings for flags.
    """
    ci_env.set_output("count", 3)
    ci_env.set_output("passed", True)

    entries = parse_github_output(github_env.output_file.read_text(encoding="utf-8"))
    assert [(name, value) for name, value, _ in entries] == [("count", "3"), ("passed", "True")]


def test_github_output_is_utf8(github_env):
    """Non-ASCII values are written as UTF-8, whatever the system's default encoding."""
    ci_env.set_output("message", "Café ✓")

    [(_, value, _)] = parse_github_output(github_env.output_file.read_text(encoding="utf-8"))
    assert value == "Café ✓"


def test_github_without_output_file_raises(github_env):
    """Without GITHUB_OUTPUT, set_output() fails loudly instead of losing the output."""
    github_env.set(GITHUB_OUTPUT=None)
    with pytest.raises(KeyError, match="GITHUB_OUTPUT"):
        ci_env.set_output("report_file", "build-report.json")


# -------------------------------------------------- set_output() on GitLab ---


# def test_gitlab_output_matches_golden_file(gitlab_env):
#     """GitLab outputs are written as KEY=value lines and match expected/ci_outputs.env.

#     Covers a plain string, a number, and a value containing '=' (a dotenv
#     line splits on the first '=' only, so the URL stays whole).
#     """
#     ci_env.set_output("report_file", "build-report.json")
#     ci_env.set_output("findings", 3)
#     ci_env.set_output("report_url", "https://example.com/report?id=42")

#     actual = gitlab_env.output_file.read_text(encoding="utf-8")
#     expected = EXPECTED_GITLAB_OUTPUT.read_text(encoding="utf-8")
#     assert actual == expected


# def test_gitlab_output_written_to_workspace(gitlab_env):
#     """The dotenv file is created in the workspace (CI_PROJECT_DIR).

#     The job's artifacts:reports:dotenv path is relative to that folder.
#     """
#     ci_env.set_output("report_file", "build-report.json")
#     assert (gitlab_env.workspace / "ci_outputs.env").is_file()


# def test_gitlab_multiline_value_raises(gitlab_env):
#     """A multi-line value raises ValueError naming the output, and nothing is written.

#     A dotenv file can only hold one line per value.
#     """
#     with pytest.raises(ValueError, match="summary"):
#         ci_env.set_output("summary", "2 errors\n1 warning")
#     assert not gitlab_env.output_file.exists()


# def test_gitlab_output_appends(gitlab_env):
#     """Each call adds a line; earlier outputs are kept."""
#     gitlab_env.output_file.write_text("existing=1\n", encoding="utf-8")
#     ci_env.set_output("report_file", "build-report.json")

#     lines = gitlab_env.output_file.read_text(encoding="utf-8").splitlines()
#     assert lines == ["existing=1", "report_file=build-report.json"]


# def test_gitlab_custom_output_file(gitlab_env):
#     """CI_ENV_OUTPUT_FILE changes the dotenv file name.

#     It is read at import, so the fixture reloads ci_env after setting it.
#     """
#     gitlab_env.set(CI_ENV_OUTPUT_FILE="custom.env")
#     ci_env.set_output("report_file", "build-report.json")

#     assert (gitlab_env.workspace / "custom.env").read_text(encoding="utf-8") == "report_file=build-report.json\n"
#     assert not gitlab_env.output_file.exists()


# def test_gitlab_without_workspace_uses_current_folder(gitlab_env, tmp_path, monkeypatch):
#     """Without CI_PROJECT_DIR, the dotenv file goes in the current folder."""
#     gitlab_env.set(CI_PROJECT_DIR=None)
#     elsewhere = tmp_path / "elsewhere"
#     elsewhere.mkdir()
#     monkeypatch.chdir(elsewhere)

#     ci_env.set_output("report_file", "build-report.json")
#     assert (elsewhere / "ci_outputs.env").read_text(encoding="utf-8") == "report_file=build-report.json\n"


# ---------------------------------------------------- set_output() locally ---


def test_local_output_is_printed(local_env, capsys):
    """Outside CI, outputs are printed as "[output] name=value" so a developer can see them."""
    ci_env.set_output("report_file", "build-report.json")
    assert capsys.readouterr().out == "[output] report_file=build-report.json\n"


def test_local_output_writes_no_file(local_env, tmp_path, monkeypatch):
    """Outside CI, set_output() writes no file to the current folder."""
    monkeypatch.chdir(tmp_path)
    ci_env.set_output("report_file", "build-report.json")
    assert list(tmp_path.iterdir()) == []