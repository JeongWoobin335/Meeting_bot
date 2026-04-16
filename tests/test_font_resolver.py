from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.font_resolver import expand_css_font_stack, stylesheet_import_urls_for_fonts


class FontResolverTest(unittest.TestCase):
    def test_expand_css_font_stack_keeps_generic_family_unquoted(self) -> None:
        stack = expand_css_font_stack("SUIT")

        self.assertIn('"SUIT Variable"', stack)
        self.assertIn('"Noto Sans KR"', stack)
        self.assertIn("sans-serif", stack)
        self.assertNotIn('"sans-serif"', stack)

    def test_expand_css_font_stack_appends_safe_fallback_for_unknown_font(self) -> None:
        stack = expand_css_font_stack("Custom Brand Sans")

        self.assertTrue(stack.startswith('"Custom Brand Sans"'))
        self.assertIn('"Noto Sans KR"', stack)
        self.assertIn("sans-serif", stack)

    def test_stylesheet_import_urls_for_fonts_returns_supported_web_fonts_without_duplicates(self) -> None:
        urls = stylesheet_import_urls_for_fonts("SUIT, Pretendard", "SUIT", "Noto Sans KR", "Malgun Gothic")

        self.assertEqual(
            urls,
            [
                "https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/variable/woff2/SUIT-Variable.css",
                "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css",
                "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap",
            ],
        )


if __name__ == "__main__":
    unittest.main()
