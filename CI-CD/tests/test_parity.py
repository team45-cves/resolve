"""Parity tests between each action.yml and the script it runs.

Every folder in CI-CD/actions/ holds an action.yml, and the folder name
names its script: actions/sbom/action.yml runs scripts/sbom.py. These tests
check that the two agree, so a renamed or forgotten input or output fails
here instead of silently doing nothing in CI:

    1. Inputs -> globals      every input has a module-level global, read
                              with get_input("<NAME>"), and vice versa
    2. Reserved names         no input global shadows a variable the OS,
                              Python or a CI platform already sets (PATH, ...)
    3. Inputs passed in       the step that runs the script hands each input
                              over as INPUT_<NAME>
    4. Outputs                every set_output("<name>", ...) in the script
                              is an output of the action, and vice versa
    5. Dispatcher             scripts/start_action.sh has an entry for the
                              script, and the action runs it through that
                              entry ("$IMAGE" <action>), never the .py directly

Input names map to globals by upper-casing and swapping "-" for "_":
the input sbom-path becomes the global SBOM_PATH.

The scripts are read as text and never imported: importing one runs its
get_input() calls and its sys.path setup, which these tests don't need.
"""

import ast
import re
from pathlib import Path

import pytest
import yaml


CI_CD_DIR = Path(__file__).resolve().parent.parent
ACTIONS_DIR = CI_CD_DIR / "actions"
SCRIPTS_DIR = CI_CD_DIR / "scripts"
DISPATCHER = SCRIPTS_DIR / "start_action.sh"

IGNORED_ACTIONS = {
    "input-synthesis",  # no scripts/input-synthesis.py
}

COMMON_ACTION_ONLY_INPUTS = {"image"}


# Extra per-action exceptions, by action folder. Usually empty.
ACTION_ONLY_INPUTS = {
    # "example-action": {"cache-dir"},
}


# Variables the environment already sets. Locally and on GitLab, get_input()
# reads <NAME> with no INPUT_ prefix, so a global named PATH would pick up
# the system PATH instead of the action's input.
RESERVED_NAMES = {
    # POSIX / shells
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "OLDPWD", "TERM",
    "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP", "HOSTNAME", "IFS",
    "PS1", "PS2", "EDITOR", "PAGER", "DISPLAY", "MAIL", "UID", "EUID",
    "SHLVL", "LD_LIBRARY_PATH", "LD_PRELOAD",
    # Windows
    "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "USERPROFILE", "APPDATA",
    "LOCALAPPDATA", "PROGRAMFILES", "USERNAME", "COMPUTERNAME",
    # Docker / proxies / TLS
    "DOCKER_HOST", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE",
    # Generic CI
    "CI",
}
RESERVED_PREFIXES = (
    "PYTHON",   # PYTHONPATH, PYTHONHOME, PYTHONUNBUFFERED, ...
    "PIP_",
    "LC_",      # locale
    "GITHUB_",  # GitHub Actions
    "RUNNER_",  # GitHub runner
    "INPUT_",   # would become INPUT_INPUT_<NAME> on GitHub
    "GITLAB_",  # GitLab CI
    "CI_",      # GitLab CI, and ci_env's own CI_ENV_OUTPUT_FILE
)

# set_output("name", ...) or env.set_output('name', ...): group 2 is the name.
SET_OUTPUT_RE = re.compile(r"""\bset_output\(\s*(["'])([^"']+)\1""")

# Any set_output( call, to catch ones whose name isn't a plain string literal. Used to count the number of calls.
ANY_SET_OUTPUT_RE = re.compile(r"\bset_output\(")

# ${{ steps.<id>.outputs.<name> }} in an action output's value. Grabs the step and the name.
STEP_OUTPUT_RE = re.compile(r"\$\{\{\s*steps\.([\w-]+)\.outputs\.([\w-]+)\s*\}\}")

# ${{ inputs.<name> }} in a step's env value. Grabs the input name.
INPUT_REF_RE = re.compile(r"^\$\{\{\s*inputs\.([\w-]+)\s*\}\}$")

# A case entry in start_action.sh, e.g. `sbom)  script="sbom.py"`, where the
# script line may also be on the next line. Group 1 is the dispatcher's
# action name, group 2 the script it runs.
DISPATCH_ENTRY_RE = re.compile(r'^\s*([\w-]+)\)\s*script="([^"]+)"', re.MULTILINE)


# ------------------------------------------------------------- helpers ---


def _find_actions():
    """Every action folder with an action.yml, sorted alphabetically by name."""
    return sorted(
        path.parent.name
        for pattern in ("*/action.yml", "*/action.yaml")
        for path in ACTIONS_DIR.glob(pattern)
        if path.parent.name not in IGNORED_ACTIONS
    )


ACTIONS = _find_actions()


def _read_dispatcher():
    """start_action.sh's mapping as {script file: dispatcher action name}."""
    if not DISPATCHER.is_file():
        return {}
    text = DISPATCHER.read_text(encoding="utf-8")
    return {script: action for action, script in DISPATCH_ENTRY_RE.findall(text)}


DISPATCH_NAMES = _read_dispatcher()


#the run text without its comment lines, so a comment never counts as a call
def _code(run):
    return "\n".join(line for line in run.splitlines() if not line.lstrip().startswith("#"))


#whether the run text calls the dispatcher with this action name: "$IMAGE" sbom
def _calls_dispatcher(run, dispatch_name):
    pattern = rf'"?\$\{{?IMAGE\}}?"?(?:\s|\\)+{re.escape(dispatch_name)}(?![\w-])'
    return re.search(pattern, _code(run)) is not None

#Given a folder name, it returns the path to that folder's action.yml/.yaml
def _action_file(name):
    folder = ACTIONS_DIR / name
    return folder / "action.yml" if (folder / "action.yml").is_file() else folder / "action.yaml"

#turns an input name into its global name.
def _to_global(input_name):
    """sbom-path -> SBOM_PATH."""
    return input_name.upper().replace("-", "_")

#returns the inputs the input checks should skip for this action
def _skipped_inputs(name):
    return COMMON_ACTION_ONLY_INPUTS | ACTION_ONLY_INPUTS.get(name, set())

#returns whether a name is off limits
def _is_reserved(name):
    return name in RESERVED_NAMES or name.startswith(RESERVED_PREFIXES)


def _get_input_key(node):
    """The "NAME" in the first get_input("NAME", ...) call inside node, or None.

    Finds the call even when it's wrapped, as in
    get_input("CVE_IDS", "").split() or get_input("X", "true") == "true".
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if called == "get_input" and child.args and isinstance(child.args[0], ast.Constant):
            return child.args[0].value
    return None

#pytest runs once per action folder
@pytest.fixture(params=ACTIONS)
def action(request):
    """One action: its folder name, parsed action.yml, and script source."""
    name = request.param
    script_path = SCRIPTS_DIR / f"{name}.py"
    if not script_path.is_file():
        pytest.fail(
            f"actions/{name}/ has no matching script: expected scripts/{name}.py"
        )

    #Finds the action's action.yml and reads it into a Python dict.
    action_path = _action_file(name)
    spec = yaml.safe_load(action_path.read_text(encoding="utf-8")) or {}
    source = script_path.read_text(encoding="utf-8")

    # Module-level globals that read an input: {global name: get_input key}.
    input_globals = {}
    for node in ast.parse(source, filename=str(script_path)).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name):
            key = _get_input_key(value)
            if key is not None:
                input_globals[target.id] = key

    # The dispatcher's name for this script (None if start_action.sh has no entry).
    dispatch_name = DISPATCH_NAMES.get(f"{name}.py")
    steps = (spec.get("runs") or {}).get("steps") or []

    # The step that runs the script through the dispatcher: "$IMAGE" <dispatch_name>.
    script_steps = [
        step for step in steps
        if dispatch_name and _calls_dispatcher(step.get("run") or "", dispatch_name)
    ]

    # Steps that call the .py file themselves, skipping the dispatcher.
    direct_steps = [
        step for step in steps
        if re.search(rf"\b{re.escape(name)}\.py\b", _code(step.get("run") or ""))
    ]

    return {
        "name": name,
        "action_file": action_path.relative_to(CI_CD_DIR),
        "script_file": script_path.relative_to(CI_CD_DIR),
        "inputs": spec.get("inputs") or {},
        "outputs": spec.get("outputs") or {},
        "source": source,
        "input_globals": input_globals,
        "dispatch_name": dispatch_name,
        "script_steps": script_steps,
        "direct_steps": direct_steps,
    }


# --------------------------------------------------------------- tests ---


def test_actions_found():
    """At least one action was discovered, so the tests below actually ran."""
    assert ACTIONS, f"No action.yml found under {ACTIONS_DIR}"


# 1. Inputs -> globals ------------------------------------------------------


def test_every_input_has_a_global(action):
    """Each action input has a global of the same name that reads it.

    Catches an input that was added to action.yml but never wired up in the
    script, or one that was renamed on only one side.
    """
    skipped = _skipped_inputs(action["name"])
    problems = []
    for input_name in action["inputs"]:
        if input_name in skipped:
            continue
        expected = _to_global(input_name)
        key = action["input_globals"].get(expected)
        if key is None:
            problems.append(
                f"input {input_name!r} has no global {expected} = get_input({expected!r}, ...)"
            )
        elif key != expected:
            problems.append(
                f"global {expected} reads get_input({key!r}); expected get_input({expected!r})"
            )
    assert not problems, f"{action['action_file']} vs {action['script_file']}:\n  " + "\n  ".join(problems)


def test_every_global_has_an_input(action):
    """Each global that reads an input matches an input in action.yml.

    A global with no matching input can never be set from a workflow, so
    it would always fall back to its default.
    """
    expected = {_to_global(name) for name in action["inputs"]}
    orphans = sorted(set(action["input_globals"]) - expected)
    assert not orphans, (
        f"{action['script_file']} reads inputs that {action['action_file']} doesn't declare: "
        + ", ".join(orphans)
    )


def test_action_only_inputs_exist(action):
    """Every name in ACTION_ONLY_INPUTS is still an input, so the list doesn't go stale."""
    stale = sorted(ACTION_ONLY_INPUTS.get(action["name"], set()) - set(action["inputs"]))
    assert not stale, f"ACTION_ONLY_INPUTS[{action['name']!r}] lists unknown inputs: {stale}"


# 2. Reserved names ---------------------------------------------------------


def test_input_globals_are_not_reserved(action):
    """No input global uses a name the environment already sets.

    Outside GitHub, get_input("PATH") reads the variable PATH itself, so
    such an input would quietly take the system's value. Checks both the
    global's name and the name passed to get_input().
    """
    clashes = sorted(
        {name for name in action["input_globals"] if _is_reserved(name)}
        | {key for key in action["input_globals"].values() if _is_reserved(key)}
    )
    assert not clashes, (
        f"{action['script_file']} uses reserved environment names for inputs: "
        + ", ".join(clashes)
    )


# 3. Inputs passed in -------------------------------------------------------


def test_inputs_reach_the_script(action):
    """The step that runs the script passes each input in as INPUT_<NAME>.

    Composite actions don't export inputs on their own, so each one needs
    INPUT_<NAME>: ${{ inputs.<name> }} in the step's env, and a -e
    INPUT_<NAME> flag if the step runs the script with docker run.
    """
    steps = action["script_steps"]
    assert len(steps) == 1, (
        f"Expected exactly one step in {action['action_file']} to run "
        f'"$IMAGE" {action["dispatch_name"] or "<action>"}, found {len(steps)} '
        "(see test_action_uses_the_dispatcher)"
    )
    step = steps[0]
    env = step.get("env") or {}
    run = step.get("run") or ""

    skipped = _skipped_inputs(action["name"])
    problems = []
    for input_name in action["inputs"]:
        if input_name in skipped:
            continue
        variable = f"INPUT_{_to_global(input_name)}"
        match = INPUT_REF_RE.match(str(env.get(variable, "")).strip())
        if not match:
            problems.append(f"env is missing {variable}: ${{{{ inputs.{input_name} }}}}")
        elif match.group(1) != input_name:
            problems.append(f"env {variable} reads inputs.{match.group(1)}, not inputs.{input_name}")
        if "docker run" in run and not re.search(rf"-e\s+{variable}\b", run):
            problems.append(f"docker run is missing -e {variable}")
    assert not problems, f"{action['action_file']} step {step.get('id', step.get('name'))!r}:\n  " + "\n  ".join(problems)


# 4. Outputs ----------------------------------------------------------------


def test_set_output_names_are_literals(action):
    """Every set_output() call names its output with a plain string.

    The output tests find names with a regex, so a name built at run time
    (an f-string or a variable) would be missed.
    """
    total = len(ANY_SET_OUTPUT_RE.findall(action["source"]))
    found = len(SET_OUTPUT_RE.findall(action["source"]))
    assert total == found, (
        f"{action['script_file']} has {total - found} set_output() call(s) whose "
        "name isn't a string literal"
    )


def test_outputs_match(action):
    """The script's set_output() names and the action's outputs are the same set.

    Each action output that reads the script's step must name the same
    output it is declared as (cve-count -> steps.scan.outputs.cve-count),
    and that name must be one the script sets. Every name the script sets
    must also be exposed as an action output.
    """
    if len(action["script_steps"]) != 1:
        pytest.fail(
            f"Can't match outputs: no single step in {action['action_file']} runs the "
            "script through the dispatcher (see test_action_uses_the_dispatcher)"
        )
    script_outputs = {m.group(2) for m in SET_OUTPUT_RE.finditer(action["source"])}
    step_ids = {step.get("id") for step in action["script_steps"]}

    problems = []
    exposed = set()
    for output_name, spec in action["outputs"].items():
        match = STEP_OUTPUT_RE.search(str((spec or {}).get("value", "")))
        if not match:
            problems.append(f"output {output_name!r} has no ${{{{ steps.<id>.outputs.<name> }}}} value")
            continue
        step_id, step_output = match.groups()
        if step_id not in step_ids:
            continue  # comes from another step, not the script
        exposed.add(step_output)
        if step_output != output_name:
            problems.append(
                f"output {output_name!r} reads steps.{step_id}.outputs.{step_output}; names must match"
            )
        if step_output not in script_outputs:
            problems.append(f"output {output_name!r}: the script never calls set_output({step_output!r}, ...)")

    for name in sorted(script_outputs - exposed):
        problems.append(f"set_output({name!r}, ...) is not exposed as an output in action.yml")

    assert not problems, f"{action['action_file']} vs {action['script_file']}:\n  " + "\n  ".join(problems)


# 5. Dispatcher -------------------------------------------------------------


def test_dispatcher_has_an_entry(action):
    """start_action.sh maps an action name to this script.

    Without an entry, the container rejects the action at run time with
    "unknown action" (exit code 2).
    """
    assert action["dispatch_name"], (
        f"{DISPATCHER.relative_to(CI_CD_DIR)} has no entry for {action['script_file']}; "
        f'add a line like  my-action)  script="{action["name"]}.py" ;;'
    )


def test_action_uses_the_dispatcher(action):
    """The action runs its script through start_action.sh, never directly.

    Exactly one step must call the image with the dispatcher's name
    ("$IMAGE" <action>), and no step may run the .py file itself, which
    would skip the dispatcher's allow-list and its non-root handling.
    """
    problems = []
    if action["direct_steps"]:
        names = [step.get("id", step.get("name")) for step in action["direct_steps"]]
        problems.append(f"step(s) {names} run {action['name']}.py directly")
    if action["dispatch_name"] and len(action["script_steps"]) != 1:
        problems.append(
            f'expected exactly one step to run "$IMAGE" {action["dispatch_name"]}, '
            f"found {len(action['script_steps'])}"
        )
    assert not problems, (
        f"{action['action_file']} should run its script through "
        f"{DISPATCHER.relative_to(CI_CD_DIR)}:\n  " + "\n  ".join(problems)
    )
