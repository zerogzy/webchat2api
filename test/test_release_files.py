import unittest
from pathlib import Path


class ReleaseFileTests(unittest.TestCase):
    def test_entrypoint_uses_lf_line_endings(self) -> None:
        content = Path("scripts/entrypoint.sh").read_bytes()

        self.assertTrue(content.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", content)
