#!/usr/bin/env python3
"""Validate Host dependency ownership and immutable first-party pins."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
AUDIT_REQUIREMENTS = ROOT / "requirements-audit.txt"
EXPECTED_BUILD_REQUIRES = ["setuptools==84.0.0"]
PROTOCOL = re.compile(
    r"^ordivon-protocol @ git\+https://github\.com/zycxfyh/"
    r"ordivon-computing\.git@([0-9a-f]{40})"
    r"#subdirectory=packages/ordivon-protocol$"
)


def main() -> int:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencies = data.get("project", {}).get("dependencies")
    if not isinstance(dependencies, list):
        print("dependencies: project.dependencies must be a list", file=sys.stderr)
        return 1
    protocol_pins: list[str] = []
    third_party: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, str):
            print("dependencies: non-string project dependency", file=sys.stderr)
            return 1
        match = PROTOCOL.fullmatch(dependency)
        if match:
            protocol_pins.append(match.group(1))
        else:
            third_party.append(dependency)
    if len(protocol_pins) != 1:
        print(
            "dependencies: expected exactly one immutable ordivon-protocol Git pin",
            file=sys.stderr,
        )
        return 1

    optional = data.get("project", {}).get("optional-dependencies", {})
    if not isinstance(optional, dict) or set(optional) != {"mcp"}:
        print("dependencies: Host optional dependencies must contain only the mcp server surface", file=sys.stderr)
        return 1
    mcp_dependencies = optional.get("mcp")
    if not isinstance(mcp_dependencies, list) or not all(
        isinstance(item, str) for item in mcp_dependencies
    ):
        print("dependencies: project.optional-dependencies.mcp must be a string list", file=sys.stderr)
        return 1

    audited = [
        line.strip()
        for line in AUDIT_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    audited_third_party = [*third_party, *mcp_dependencies]
    if sorted(audited) != sorted(audited_third_party):
        print(
            "dependencies: requirements-audit.txt must exactly list base plus optional server third-party dependencies",
            file=sys.stderr,
        )
        print(f"project={audited_third_party!r} audit={audited!r}", file=sys.stderr)
        return 1

    build_requires = data.get("build-system", {}).get("requires")
    if build_requires != EXPECTED_BUILD_REQUIRES:
        print("dependencies: build-system requirements changed without review", file=sys.stderr)
        return 1

    print(
        "dependency contract: valid "
        f"protocol={protocol_pins[0]} base_third_party={len(third_party)} "
        f"mcp_server_third_party={len(mcp_dependencies)} "
        f"build_backend={EXPECTED_BUILD_REQUIRES[0]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
