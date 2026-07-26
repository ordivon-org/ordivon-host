from __future__ import annotations

from copy import deepcopy
from typing import Any
import unittest

from anc_tool_contract import CompletionKind, EffectClass
from ordivon_host.runtime import discover_runtime_catalog


def descriptor(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Description for {name}",
        "annotations": {"title": name},
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "schemaVersion": {"type": "integer", "const": 1},
            },
            "required": ["schemaVersion"],
            "additionalProperties": False,
        },
    }


class FakeCatalogClient:
    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.tools = tools

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self.tools))


class RuntimeCatalogTests(unittest.TestCase):
    def tools(self) -> list[dict[str, Any]]:
        return [
            descriptor("workspace.close"),
            descriptor("workspace.get"),
            descriptor("workspace.open"),
            descriptor("workspace.read"),
        ]

    def test_read_contract_uses_host_semantic_profile(self) -> None:
        catalog = discover_runtime_catalog(FakeCatalogClient(self.tools()))
        self.assertEqual(catalog.read_contract.operation, "workspace.read")
        self.assertEqual(catalog.read_contract.effect_class, EffectClass.OBSERVE)
        self.assertEqual(
            catalog.read_contract.completion,
            CompletionKind.ACCEPTED_VERIFICATION,
        )
        self.assertEqual(catalog.read_contract.revision, catalog.revision)
        self.assertTrue(catalog.revision.startswith("mcp-catalog:"))

    def test_presentation_changes_do_not_change_catalog_identity(self) -> None:
        before = self.tools()
        after = deepcopy(before)
        after[-1]["description"] = "A completely different description"
        after[-1]["annotations"] = {"title": "Renamed"}
        left = discover_runtime_catalog(FakeCatalogClient(before))
        right = discover_runtime_catalog(FakeCatalogClient(after))
        self.assertEqual(left.digest, right.digest)
        self.assertEqual(left.read_contract, right.read_contract)

    def test_schema_change_changes_catalog_identity(self) -> None:
        before = self.tools()
        after = deepcopy(before)
        after[-1]["inputSchema"]["properties"]["offset"] = {
            "type": "integer",
            "minimum": 0,
        }
        left = discover_runtime_catalog(FakeCatalogClient(before))
        right = discover_runtime_catalog(FakeCatalogClient(after))
        self.assertNotEqual(left.digest, right.digest)
        self.assertNotEqual(left.read_contract.revision, right.read_contract.revision)

    def test_missing_operation_fails_closed(self) -> None:
        tools = [tool for tool in self.tools() if tool["name"] != "workspace.get"]
        with self.assertRaisesRegex(ValueError, "missing operations"):
            discover_runtime_catalog(FakeCatalogClient(tools))

    def test_duplicate_operation_fails_closed(self) -> None:
        tools = self.tools()
        tools.append(deepcopy(tools[-1]))
        with self.assertRaisesRegex(ValueError, "repeats operation"):
            discover_runtime_catalog(FakeCatalogClient(tools))


if __name__ == "__main__":
    unittest.main()
