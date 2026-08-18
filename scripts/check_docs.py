#!/usr/bin/env python3
"""Validate canonical Host documents and repository contract markers."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / ".ordivon/project.yaml"
PYPROJECT = ROOT / "pyproject.toml"
HOST_MCP_SERVER = ROOT / "src/ordivon_host/mcp_server.py"
HOST_SCHEMA = ROOT / "src/ordivon_host/journal/_schema.py"

REQUIRED_FRONTMATTER = {
    "schema_version",
    "id",
    "title",
    "type",
    "profile",
    "lifecycle",
    "source_role",
    "visibility",
    "owners",
    "audience",
    "updated",
    "summary",
    "evidence_status",
    "readiness",
    "applies_to",
}
REQUIRED_README_HEADINGS = {
    "Purpose",
    "Responsibility boundary",
    "Status",
    "Identity meaning",
    "External-owner references",
    "Requirements",
    "Quick start",
    "Operations",
    "Documentation map",
    "Security and data",
    "License",
}
FRONTMATTER_END = "\n---\n"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
PROTOCOL_PIN = re.compile(
    r"ordivon-computing\.git@([0-9a-f]{40})#subdirectory=packages/ordivon-protocol"
)


class DocumentError(ValueError):
    pass


def parse_frontmatter(path: Path) -> dict[str, object] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find(FRONTMATTER_END, 4)
    if end < 0:
        raise DocumentError(f"{path.relative_to(ROOT)} has unterminated frontmatter")
    values: dict[str, object] = {}
    active_list: str | None = None
    for line_number, raw in enumerate(text[4:end].splitlines(), start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  - "):
            if active_list is None:
                raise DocumentError(
                    f"{path.relative_to(ROOT)}:{line_number} has a list item without a key"
                )
            current = values.setdefault(active_list, [])
            if not isinstance(current, list):
                raise DocumentError(
                    f"{path.relative_to(ROOT)}:{line_number} mixes scalar and list"
                )
            current.append(raw[4:].strip())
            continue
        match = re.fullmatch(r"([a-z][a-z0-9_]*)\s*:\s*(.*)", raw)
        if not match:
            raise DocumentError(
                f"{path.relative_to(ROOT)}:{line_number} has unsupported frontmatter"
            )
        key, scalar = match.groups()
        if key in values:
            raise DocumentError(
                f"{path.relative_to(ROOT)}:{line_number} repeats {key}"
            )
        active_list = key if not scalar else None
        values[key] = scalar if scalar else []
    return values


def managed_paths() -> list[Path]:
    paths: list[Path] = []
    active = False
    for line in PROJECT.read_text(encoding="utf-8").splitlines():
        if line == "managed_paths:":
            active = True
            continue
        if active and re.match(r"^[a-zA-Z_]", line):
            break
        if active and line.startswith("  - "):
            paths.append(ROOT / line[4:].strip())
    if not paths:
        raise DocumentError(".ordivon/project.yaml defines no managed_paths")
    return paths


def validate_frontmatter() -> list[str]:
    errors: list[str] = []
    ids: dict[str, Path] = {}
    managed = managed_paths()
    for path in managed:
        if not path.is_file():
            errors.append(f"managed path is missing: {path.relative_to(ROOT)}")
            continue
        values = parse_frontmatter(path)
        if values is None:
            errors.append(f"managed Markdown lacks frontmatter: {path.relative_to(ROOT)}")
            continue
        missing = sorted(REQUIRED_FRONTMATTER - values.keys())
        if missing:
            errors.append(
                f"{path.relative_to(ROOT)} lacks frontmatter keys: {', '.join(missing)}"
            )
        identifier = values.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{path.relative_to(ROOT)} has no scalar id")
        elif identifier in ids:
            errors.append(
                f"duplicate document id {identifier}: "
                f"{ids[identifier].relative_to(ROOT)} and {path.relative_to(ROOT)}"
            )
        else:
            ids[identifier] = path
        updated = values.get("updated")
        if not isinstance(updated, str) or not DATE_PATTERN.fullmatch(updated):
            errors.append(f"{path.relative_to(ROOT)} has invalid updated date")
        if values.get("source_role") != "canonical":
            errors.append(f"managed document is not canonical: {path.relative_to(ROOT)}")

    for path in sorted(ROOT.rglob("*.md")):
        if ".git" in path.parts or "build" in path.parts or "dist" in path.parts:
            continue
        values = parse_frontmatter(path)
        if values is None:
            continue
        identifier = values.get("id")
        if isinstance(identifier, str) and identifier in ids and ids[identifier] != path:
            errors.append(
                f"duplicate document id {identifier}: "
                f"{ids[identifier].relative_to(ROOT)} and {path.relative_to(ROOT)}"
            )
        elif isinstance(identifier, str):
            ids[identifier] = path
    return errors


def validate_links() -> list[str]:
    errors: list[str] = []
    for path in sorted(ROOT.rglob("*.md")):
        if ".git" in path.parts or "build" in path.parts or "dist" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            raw_target = match.group(1).strip()
            if raw_target.startswith("<") and raw_target.endswith(">"):
                raw_target = raw_target[1:-1]
            target = raw_target.split(maxsplit=1)[0]
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = target.split("#", 1)[0].split("?", 1)[0]
            resolved = (path.parent / relative).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                errors.append(f"{path.relative_to(ROOT)} links outside repository: {target}")
                continue
            if not resolved.exists():
                errors.append(f"{path.relative_to(ROOT)} has broken local link: {target}")
    return errors


def validate_public_contracts() -> list[str]:
    errors: list[str] = []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^## (.+)$", readme, re.MULTILINE))
    missing = sorted(REQUIRED_README_HEADINGS - headings)
    if missing:
        errors.append("README lacks public headings: " + ", ".join(missing))

    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    if "/security/advisories/new" not in security:
        errors.append("SECURITY.md lacks the private advisory route")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## Unreleased" not in changelog:
        errors.append("CHANGELOG.md lacks an Unreleased section")
    project = PROJECT.read_text(encoding="utf-8")
    if "enforcement: strict" not in project:
        errors.append("project manifest is not strict")

    pyproject = PYPROJECT.read_text(encoding="utf-8")
    pin = PROTOCOL_PIN.search(pyproject)
    if pin is None:
        errors.append("pyproject.toml does not pin ordivon-protocol to an exact commit")
    if 'requires-python = ">=3.12,<3.13"' not in pyproject:
        errors.append("pyproject.toml Python support boundary changed without review")

    host_mcp = HOST_MCP_SERVER.read_text(encoding="utf-8")
    host_mcp_markers = (
        'name="task.list"',
        'name="task.resume"',
        'name="task.adopt"',
        'name="task.checkpoint"',
        "stateless_http=True",
        "json_response=True",
        "hmac.compare_digest",
        'DEFAULT_HOST_MCP_BIND = "127.0.0.1"',
        "TransportSecuritySettings",
        "ORDIVON_HOST_MCP_PUBLIC_ORIGIN",
        "ORDIVON_HOST_MCP_TRUST_CF_ACCESS",
        "cf-access-jwt-assertion",
    )
    for marker in host_mcp_markers:
        if marker not in host_mcp:
            errors.append(f"Host MCP lacks transport boundary marker: {marker}")
    if '[project.optional-dependencies]' not in pyproject or 'mcp = ["mcp==2.0.0"]' not in pyproject:
        errors.append("pyproject.toml does not isolate and pin the reviewed Host MCP SDK extra")
    project_dependencies = pyproject.split("[project.optional-dependencies]", 1)[0]
    if '"mcp==2.0.0"' in project_dependencies:
        errors.append("Host MCP SDK leaked back into base Host dependencies")
    for relative in (
        "packaging/systemd/ordivon-host-mcp.service",
        "packaging/systemd/ordivon-host-mcp.env.example",
    ):
        if not (ROOT / relative).is_file():
            errors.append(f"Host MCP packaging file is missing: {relative}")

    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    if "Host has not migrated to that transport profile" in architecture:
        errors.append("ARCHITECTURE.md still claims modern Runtime migration is incomplete")
    for stale in (
        "Harness owns Agent Assignment and Run semantics",
        "Task Attempt and Assignment semantics, Agent Runs",
    ):
        if stale in architecture:
            errors.append(f"ARCHITECTURE.md retains stale Harness ownership: {stale}")

    readme_stale = "| Assignment, Agent Run, Provider adapter, model–Tool loop"
    if readme_stale in readme:
        errors.append("README.md still advertises the removed Host-backed Assignment boundary")

    quickstart = (ROOT / "docs/QUICKSTART.md").read_text(encoding="utf-8")
    current_identity_docs = {
        "README.md": readme,
        "ARCHITECTURE.md": architecture,
        "docs/QUICKSTART.md": quickstart,
    }
    forbidden_current_claims = (
        "Goal-scoped coordination over Task revisions",
        "CognitionWorkRequest",
        "DecisionRequest",
        "ActionProposal",
        "DeterministicReadHost",
        "Runtime clients",
        "Host may call Runtime for workloads",
        "live Task progress requires Runtime",
        "## Deterministic Runtime read slice",
        "## Semantic cognition request and admission",
        "## Guarded mutation and uncertain delivery",
        "RecoveryAction",
        "RecoveryAssessment",
        "assess_recovery",
        "task assess",
    )
    for relative, text in current_identity_docs.items():
        for stale in forbidden_current_claims:
            if stale in text:
                errors.append(f"{relative} retains removed Host ownership claim: {stale}")

    identity_markers = (
        "Host is a product name, not a claim that Ordivon has one universal Host ontology",
        "Compatibility is not ontology",
        "There is no product Runtime client",
    )
    combined_identity = readme + "\n" + architecture
    for marker in identity_markers:
        if marker not in combined_identity:
            errors.append(f"canonical identity docs lack contraction marker: {marker}")

    schema_source = HOST_SCHEMA.read_text(encoding="utf-8")
    schema_match = re.search(r"(?m)^SCHEMA_VERSION = (\d+)$", schema_source)
    if schema_match is None:
        errors.append("Host schema source has no SCHEMA_VERSION")
    else:
        current_schema = int(schema_match.group(1))
        operations = (ROOT / "docs/OPERATIONS.md").read_text(encoding="utf-8")
        if f"host.sqlite3   schema v{current_schema} " not in operations:
            errors.append(
                f"OPERATIONS.md does not describe current Host schema v{current_schema}"
            )
        if current_schema > 1 and f"v{current_schema - 1} → v{current_schema}" not in operations:
            errors.append(
                f"OPERATIONS.md omits migration v{current_schema - 1} → v{current_schema}"
            )
    return errors


def main() -> int:
    errors: list[str] = []
    for validator in (validate_frontmatter, validate_links, validate_public_contracts):
        try:
            errors.extend(validator())
        except (DocumentError, OSError, UnicodeError) as error:
            errors.append(str(error))
    if errors:
        for error in sorted(set(errors)):
            print(f"docs: {error}", file=sys.stderr)
        return 1
    print("documentation contract: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
