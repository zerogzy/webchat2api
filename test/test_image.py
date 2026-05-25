from __future__ import annotations

import unittest
from typing import Any

openai_v1_image_generations: Any
save_image: Any

try:
    from services.protocol import openai_v1_image_generations
    from test.utils import save_image
except ImportError as exc:
    openai_v1_image_generations = None
    save_image = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skip("script-like image smoke helper requires live image generation backend")
class ImageSmokeTests(unittest.TestCase):
    def test_image_generation_smoke(self) -> None:
        prompt = "一只橘猫坐在窗台上，午后阳光，写实摄影"
        data = openai_v1_image_generations.handle({"prompt": prompt, "model": "gpt-5-3", "n": 1})
        for index, item in enumerate(data.get("data") or [], start=1):
            print(save_image(item["b64_json"], f"image_{index}"))


def main() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ImageSmokeTests)
    unittest.TextTestRunner().run(suite)


if __name__ == "__main__":
    main()
