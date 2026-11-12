# sbom-textkit

The SBOM from RESOLVE's own SBOM example (`textkit`, linking fontconfig and zlib).

| | |
|---|---|
| Source | Copied unchanged from `examples/sbom/example.spdx.json` |
| SBOM | `sbom.spdx.json` (SPDX 3.0.1): fontconfig 2.2.1, ZLIB 1.3.1 |
| Expected | Includes **CVE-2016-5384** (fontconfig) and **CVE-2023-6992** (zlib), where CVE-2023-6992 has two CWEs (126 and 787) in one per-CVE file |
| Tests | Several packages, and one CVE with several CWE entries |

Checked against the real NVD on 2026-11-12 with resolve v0.1.0: 5 CVEs, 7 CVE/CWE entries. That exact output is saved in `tests/sbom/recorded/textkit-vulnerabilities.json` for the offline tests.
