"""Implementation of JSON schema for resolve vulnerability definitions"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from resolve.sbom.schema.nist_api import CveId


class VulnerabilityDocument(BaseModel):
    vulnerabilities: list[Vulnerability]


class Vulnerability(BaseModel):
    # vulnerabilities.json uses hyphenated keys (e.g. "cve-id"), which is what
    # reach, remediate, and CVEAssert read. Serialize with by_alias=True.
    model_config = ConfigDict(
        alias_generator=lambda name: name.replace("_", "-"),
        populate_by_name=True,
    )

    cve_id: CveId
    cve_description: str
    package_name: str
    package_version: str
    cwe_id: Annotated[str, Field(pattern=r"^(?:\d+)|UNKNOWN$")]
    cwe_name: str
    affected_file: str
    affected_function: str
