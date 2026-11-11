"""Unit tests for logger.py.

logger prints GitHub "workflow commands": lines such as
    ::warning file=src/app.py,line=12::Coverage is low
which the runner turns into annotations on the run and the pull request.
These tests check each part of that format:

    1. info()                   plain output, not a command
    2. debug()                  ::debug:: lines
    3. notice/warning/error()   annotations and their properties
    4. Escaping                 characters that would break a command
    5. group()                  collapsible blocks of lines
    6. Golden file              a realistic run compared with expected/Annotations.txt
    7. Platforms                the same output on GitHub, GitLab and locally

capsys (a pytest built-in fixture) captures everything printed during a test;
capsys.readouterr().out returns it as one string.
"""

from pathlib import Path

import pytest

import logger


# Golden file the full-run test compares against.
EXPECTED = Path(__file__).parent / "expected"
EXPECTED_ANNOTATIONS = EXPECTED / "Annotations.txt"

# The three annotation functions and the command each one prints.
ANNOTATIONS = [
    (logger.notice, "notice"),
    (logger.warning, "warning"),
    (logger.error, "error"),
]
ANNOTATION_IDS = [command for _, command in ANNOTATIONS]


# ------------------------------------------------------------------ info() ---


def test_info_prints_message(capsys):
    """info() prints the message as an ordinary log line, with no :: command."""
    logger.info("Starting build report")
    assert capsys.readouterr().out == "Starting build report\n"


def test_info_is_not_escaped(capsys):
    """info() prints text exactly as given: '%' and newlines are not escaped.

    Escaping only applies to workflow commands. A multi-line info() message
    prints as several ordinary log lines.
    """
    logger.info("100% done\nnext line")
    assert capsys.readouterr().out == "100% done\nnext line\n"


def test_info_converts_non_strings(capsys):
    """info() accepts any value and prints its text form."""
    logger.info(42)
    assert capsys.readouterr().out == "42\n"


# ----------------------------------------------------------------- debug() ---


def test_debug_format(capsys):
    """debug() prints a ::debug:: command, which GitHub hides unless debug logging is on."""
    logger.debug("Build path: dist")
    assert capsys.readouterr().out == "::debug::Build path: dist\n"


def test_debug_escapes_message(capsys):
    """debug() escapes its message like every other command, so a newline stays on one line."""
    logger.debug("first\nsecond")
    assert capsys.readouterr().out == "::debug::first%0Asecond\n"


# ------------------------------------------------- notice / warning / error ---


@pytest.mark.parametrize("function, command", ANNOTATIONS, ids=ANNOTATION_IDS)
def test_annotation_without_properties(capsys, function, command):
    """With only a message, each function prints ::<command>::<message>."""
    function("Something happened")
    assert capsys.readouterr().out == f"::{command}::Something happened\n"


def test_warning_with_file_and_line(capsys):
    """A warning with a file and line, so GitHub attaches it to that line of code."""
    logger.warning("Coverage is low", file="src/app.py", line=12)
    assert capsys.readouterr().out == "::warning file=src/app.py,line=12::Coverage is low\n"


@pytest.mark.parametrize("function, command", ANNOTATIONS, ids=ANNOTATION_IDS)
def test_annotation_with_every_property(capsys, function, command):
    """Every property is printed, in GitHub's names and a fixed order.

    end_line and end_col become endLine and endColumn, the names GitHub
    expects. Numbers are printed as text.
    """
    function(
        "Possible null dereference",
        file="src/main.c", line=10, col=5, end_line=12, end_col=8, title="Null pointer",
    )
    assert capsys.readouterr().out == (
        f"::{command} file=src/main.c,line=10,col=5,endLine=12,endColumn=8,"
        f"title=Null pointer::Possible null dereference\n"
    )


def test_property_order_does_not_depend_on_call_order(capsys):
    """Properties always print as file, line, col, endLine, endColumn, title,
    whatever order the caller passes them in. This keeps output identical
    for golden-file comparisons.
    """
    logger.warning("Message", title="Check", line=3, file="a.c")
    assert capsys.readouterr().out == "::warning file=a.c,line=3,title=Check::Message\n"


def test_unset_properties_are_left_out(capsys):
    """A property left as None doesn't appear at all, not even as 'line='."""
    logger.error("Message", file="a.c", line=None, title=None)
    assert capsys.readouterr().out == "::error file=a.c::Message\n"


def test_zero_is_a_real_value(capsys):
    """Only None means 'not set': line=0 and col=0 are still printed."""
    logger.warning("Message", line=0, col=0)
    assert capsys.readouterr().out == "::warning line=0,col=0::Message\n"


def test_non_string_message(capsys):
    """A message that isn't a string is printed as its text form."""
    logger.warning(3)
    assert capsys.readouterr().out == "::warning::3\n"


# ---------------------------------------------------------------- escaping ---


def test_message_escaping(capsys):
    """In a message, '%', carriage returns and newlines become %25, %0D and %0A.

    GitHub decodes these back, so the annotation shows the original text.
    """
    logger.warning("50% of files\r\nchecked")
    assert capsys.readouterr().out == "::warning::50%25 of files%0D%0Achecked\n"


def test_percent_escaped_first(capsys):
    """'%' is escaped before newlines, so text that already looks like an escape
    code stays literal: '%0A' in a message shows as '%0A', not as a line break.
    """
    logger.warning("literal %0A")
    assert capsys.readouterr().out == "::warning::literal %250A\n"


def test_message_keeps_colons_and_commas(capsys):
    """':' and ',' are only special in properties, so a message keeps them as they are."""
    logger.notice("Checked: 3 files, 2 folders")
    assert capsys.readouterr().out == "::notice::Checked: 3 files, 2 folders\n"


def test_property_escaping(capsys):
    """In properties, ':' and ',' are escaped too (%3A, %2C), as well as '%' and newlines.

    Unescaped, a ',' would start a new property and a ':' could end the
    properties early.
    """
    logger.error("Message", file="dir,name:1.c", title="50%\nfail")
    assert capsys.readouterr().out == "::error file=dir%2Cname%3A1.c,title=50%25%0Afail::Message\n"


def test_message_cannot_inject_a_command(capsys):
    """A message containing a newline and '::error::' can't start a second command.

    Tool output often ends up in messages; escaping keeps it all on one line,
    so GitHub sees exactly one annotation.
    """
    logger.warning("tool said:\n::error::fake")
    output = capsys.readouterr().out
    assert output == "::warning::tool said:%0A::error::fake\n"
    assert output.count("\n") == 1


def test_property_cannot_end_properties_early(capsys):
    """A title containing '::' can't end the properties and change the message."""
    logger.warning("Real message", title="x::fake message")
    assert capsys.readouterr().out == "::warning title=x%3A%3Afake message::Real message\n"


# ------------------------------------------------------------------ group() ---


def test_group_wraps_lines(capsys):
    """Lines printed inside 'with group(...)' sit between ::group:: and ::endgroup::,
    which GitHub shows as one collapsible block.
    """
    with logger.group("Build"):
        logger.info("compiling")
    assert capsys.readouterr().out == "::group::Build\ncompiling\n::endgroup::\n"


def test_group_closed_after_exception(capsys):
    """If the code inside the group fails, ::endgroup:: is still printed and the
    error still reaches the caller, so the rest of the log isn't swallowed by
    a group that never closes.
    """
    with pytest.raises(RuntimeError, match="tool crashed"):
        with logger.group("Build"):
            raise RuntimeError("tool crashed")
    assert capsys.readouterr().out == "::group::Build\n::endgroup::\n"


def test_group_name_escaped(capsys):
    """The group name is escaped like a message, so a newline can't break the command."""
    with logger.group("Step 1\nStep 2"):
        pass
    assert capsys.readouterr().out == "::group::Step 1%0AStep 2\n::endgroup::\n"


# ------------------------------------------------------------- golden file ---


def test_output_matches_golden_file(capsys):
    """A realistic run, using every function, matches expected/Annotations.txt line for line."""
    logger.info("Starting build report")
    logger.debug("Build path: dist")
    with logger.group("Findings"):
        logger.warning("Coverage is low", file="src/app.py", line=12)
        logger.error(
            "Possible null dereference",
            file="src/main.c", line=10, col=5, end_line=12, end_col=8,
            title="cppcheck: nullPointer",
        )
        logger.notice("2 findings in 1 file")
    logger.info("Report written to build-report.json")

    assert capsys.readouterr().out == EXPECTED_ANNOTATIONS.read_text(encoding="utf-8")


# --------------------------------------------------------------- platforms ---


@pytest.mark.parametrize("platform_env", ["github_env", "gitlab_env", "local_env"])
def test_same_output_on_every_platform(request, capsys, platform_env):
    """logger prints GitHub's command format whatever the platform.

    GitLab doesn't read these commands, so on GitLab they appear in the job
    log as plain text. This test documents that current behaviour; update
    it if logger learns to write GitLab's own format.

    request.getfixturevalue() runs the named fixture, so one test covers all
    three platforms.
    """
    request.getfixturevalue(platform_env)
    logger.warning("Coverage is low", file="src/app.py", line=12)
    assert capsys.readouterr().out == "::warning file=src/app.py,line=12::Coverage is low\n"
    