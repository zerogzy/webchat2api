from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class RuntimePathTests(unittest.TestCase):
    def test_source_runtime_uses_project_root_for_resources_and_writes(self) -> None:
        from services import runtime_paths

        self.assertEqual(runtime_paths.resource_base_dir(), Path(runtime_paths.__file__).resolve().parents[1])
        self.assertEqual(runtime_paths.writable_base_dir(), Path(runtime_paths.__file__).resolve().parents[1])

    def test_frozen_runtime_separates_resource_and_writable_roots(self) -> None:
        from services import runtime_paths

        fake_sys = types.SimpleNamespace(
            frozen=True,
            _MEIPASS="/tmp/webchat2api-bundle",
            executable="/opt/webchat2api/webchat2api-linux-amd64",
        )

        with patch.object(runtime_paths, "sys", fake_sys):
            self.assertEqual(runtime_paths.resource_base_dir(), Path("/tmp/webchat2api-bundle").resolve())
            self.assertEqual(runtime_paths.writable_base_dir(), Path("/opt/webchat2api").resolve())


if __name__ == "__main__":
    unittest.main()
