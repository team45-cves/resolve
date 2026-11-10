# RESOLVE — test image
# ----------------------------------------------------------------------------
# Extends the RESOLVE runtime image (docker/pipeline/Dockerfile) with pytest and the
# unit tests, so the tests run on exactly the OS, Python and tools the actions
# get. The runtime image itself is not changed.
#
# Build from the CI-CD/ folder, like the runtime image. Build the runtime image
# first; AI_BACKEND=none keeps it small and needs no network at test time:
#   docker build -f docker/pipeline/Dockerfile --build-arg AI_BACKEND=none -t resolve_ci:latest .
#   docker build -f docker/pipeline_test/Dockerfile -t resolve-test .
#
# To test a runtime image with another tag, pass it as BASE_IMAGE:
#   docker build -f docker/pipeline_test/Dockerfile --build-arg BASE_IMAGE=resolve_ci:abc123 -t resolve-test .
#
# Run (the same on Linux, macOS and Windows):
#   docker run --rm resolve-test                                     # every test
#   docker run --rm -v "${PWD}/test-results:/results" resolve-test   # keep junit.xml
#   docker run --rm -v "${PWD}/tests:/app/tests:ro" resolve-test     # run edited tests without rebuilding
#   docker run -it --rm --entrypoint bash resolve-test               # a shell, for debugging
#   (cmd.exe: use "%cd%" in place of "${PWD}")
#
# pytest arguments go after the image name. Paths are relative to tests/, the
# same as running pytest from CI-CD/tests/ on your machine:
#   docker run --rm resolve-test -k <keyword in test names> -x       # by name, stop at first failure
#   docker run --rm resolve-test <path to folder from CI-CD/tests>   # one folder
#   docker run --rm resolve-test <test file path from CI-CD/tests>::<test name>  # one test
#   docker run --rm resolve-test -v --durations=5                    # any other pytest option
# Or set them in an environment variable, which pytest reads by itself:
#   docker run --rm -e PYTEST_ADDOPTS="<pytest args>" resolve-test
# ----------------------------------------------------------------------------

ARG BASE_IMAGE=resolve_ci:latest
# The tag comes from BASE_IMAGE, which hadolint can't see.
# hadolint ignore=DL3006
FROM ${BASE_IMAGE}

# --- Test tools --------------------------------------------------------------
# Ubuntu's Python has no pip and blocks system-wide installs, so pytest goes in
# its own virtual environment. python3-venv provides the venv module. The
# environment uses the same Python as the actions; only pytest is added, and
# python3 on the PATH is left as the runtime image has it.
# Not version-pinned, like the runtime image's apt packages: pinned Ubuntu
# versions disappear from the archive and break the build.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt is copied on its own, so editing a test doesn't reinstall pytest.
COPY tests/requirements.txt /tmp/test-requirements.txt
RUN python3 -m venv /opt/test-venv \
    && /opt/test-venv/bin/python -m pip install --no-cache-dir -r /tmp/test-requirements.txt \
    && rm /tmp/test-requirements.txt

# --- Layout ------------------------------------------------------------------
# tests/pytest.ini imports the scripts from ../scripts. The runtime image keeps
# them in /app/actions, so /app/scripts links there: /app/tests and
# /app/scripts then sit side by side, as tests/ and scripts/ do in CI-CD/.
RUN ln -s /app/actions /app/scripts \
    && mkdir -p /results

COPY tests/ /app/tests/

# actions is copied for th parity tests
COPY actions/ /app/actions/

# --- Run ---------------------------------------------------------------------
# pytest runs from the tests folder, so paths a tester passes are relative to
# it, and with no paths pytest.ini's "testpaths = ." runs every test.
WORKDIR /app/tests

# Replaces the runtime image's start_action.sh. Anything after the image name
# in "docker run" is passed on to pytest. The JUnit report (with file and line
# numbers, for annotations) goes to /results; mount a folder there to keep it.
# -p no:cacheprovider avoids warnings when tests/ is mounted read-only.
ENTRYPOINT ["/opt/test-venv/bin/python", "-m", "pytest", \
            "-p", "no:cacheprovider", \
            "-o", "junit_family=xunit1", "--junitxml=/results/junit.xml"]
CMD []
