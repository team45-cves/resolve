# SBOM sample projects

Small projects for testing the SBOM action (`CI-CD/scripts/sbom.py`). Each folder has its own README with the expected result.

| Folder | Case | SBOM | Expected |
|---|---|---|---|
| `sbom-c-vuln` | C/C++ with a vulnerability | cJSON 1.7.17 | CVE-2024-31755 found |
| `sbom-c-clean` | C/C++ with no vulnerabilities | No packages | `cve-count` = 0 |
| `sbom-python` | Python only | None | Fails with `SBOM not found` |
| `sbom-mixed` | Python + C++ | pybind11 2.13.6 | `cve-count` = 0 |
| `sbom-bad-format` | Wrong SBOM format | CycloneDX | Should fail (today probably passes with 0) |
| `sbom-dolphin` | Real CMake-generated SBOM (KDE Dolphin plugin) | fontconfig 2.2.1, Qt6Widgets 6.10.2 | CVE-2016-5384 found |
| `sbom-textkit` | RESOLVE's own SBOM example | fontconfig 2.2.1, ZLIB 1.3.1 | CVE-2016-5384 and CVE-2023-6992 (2 CWEs) found |

The SBOMs are hand-written, except `sbom-dolphin` and `sbom-textkit`, which are real files copied from this repo (see their READMEs). The hand-written ones use the SPDX 3 format CMake's `install(SBOM)` produces, because CMake 4.3's SBOM generation is still experimental. Each `CMakeLists.txt` has a TODO where generation would go.

Use one in a pytest test with `sample_project("sbom-c-vuln")`, then point the `SBOM_PATH` input at `sbom.spdx.json` in the workspace.

Results from the real NVD can change over time, so tests should check that specific CVEs are present rather than exact counts. Tests that don't need the network should stub `subprocess.run` (see `tests/README.md`).
