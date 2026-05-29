from __future__ import annotations

import unittest
from pathlib import Path


ACCOUNT_PAGE = Path(__file__).resolve().parents[1] / "web" / "src" / "app" / "accounts" / "page.tsx"


class AccountUiTokenPrivacyTests(unittest.TestCase):
    def test_account_page_masks_tokens_instead_of_rendering_or_copying_raw_values(self) -> None:
        source = ACCOUNT_PAGE.read_text()

        self.assertIn("function maskAccountToken", source)
        self.assertIn("if (token) return maskAccountToken(token);", source)
        self.assertNotIn("navigator.clipboard.writeText(token)", source)
        self.assertNotIn("toast.success(\"token 已复制\")", source)
        self.assertNotIn("title={tokenDisplay}", source)
        self.assertIn("完整 token 已隐藏，请使用导出功能获取凭据", source)


if __name__ == "__main__":
    unittest.main()
