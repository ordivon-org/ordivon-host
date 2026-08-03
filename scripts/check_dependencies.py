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

    audited = [
        line.strip()
        for line in AUDIT_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if sorted(audited) != sorted(third_party):
        print(
            "dependencies: requirements-audit.txt must exactly list third-party runtime dependencies",
            file=sys.stderr,
        )
        print(f"project={third_party!r} audit={audited!r}", file=sys.stderr)
        return 1

    build_requires = data.get("build-system", {}).get("requires")
    if build_requires != ["setuptools>=68"]:
        print("dependencies: build-system requirements changed without review", file=sys.stderr)
        return 1

    print(
        "dependency contract: valid "
        f"protocol={protocol_pins[0]} third_party_runtime={len(third_party)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
