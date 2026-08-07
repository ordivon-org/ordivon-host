from __future__ import annotations

import unittest

from ordivon_host.runtime.errors import RuntimeErrorDetail, RuntimeToolRejected
from ordivon_host.runtime.workspaces import is_missing_workspace


def rejected(code: str, *, field: str | None = "workspaceId", commit_state: str = "not_committed") -> RuntimeToolRejected:
    return RuntimeToolRejected(
        "workspace.get",
        RuntimeErrorDetail(
            code=code,
            message="test",
            field=field,
            retryable=False,
            retry_class="never",
            commit_state=commit_state,
            origin="runtime_core",
            trace_id=None,
            raw={},
        ),
    )


class RuntimeWorkspaceCompatibilityTests(unittest.TestCase):
    def test_missing_workspace_accepts_precise_and_legacy_codes(self) -> None:
        self.assertTrue(is_missing_workspace(rejected("WORKSPACE_NOT_FOUND")))
        self.assertTrue(is_missing_workspace(rejected("INVALID_REQUEST")))

    def test_workspace_corruption_is_not_misclassified_as_missing(self) -> None:
        self.assertFalse(is_missing_workspace(rejected("WORKSPACE_METADATA_CORRUPT")))
        self.assertFalse(is_missing_workspace(rejected("WORKSPACE_NOT_FOUND", field="relativePath")))
        self.assertFalse(is_missing_workspace(rejected("WORKSPACE_NOT_FOUND", commit_state="unknown")))


if __name__ == "__main__":
    unittest.main()
