from __future__ import annotations

import base64
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.html_pdf_renderer import HTMLPDFRenderer


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0ioAAAAASUVORK5CYII="
)


class HTMLPDFRendererTest(unittest.TestCase):
    def test_css_font_expands_alias_to_safe_stack(self) -> None:
        renderer = HTMLPDFRenderer()

        css_font = renderer._css_font("SUIT")

        self.assertIn('"SUIT Variable"', css_font)
        self.assertIn('"Noto Sans KR"', css_font)
        self.assertIn("sans-serif", css_font)
        self.assertNotIn('"sans-serif"', css_font)

    def test_render_html_document_includes_sections_lists_and_visuals(self) -> None:
        renderer = HTMLPDFRenderer()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            image_path = base_dir / "visual.png"
            image_path.write_bytes(_PNG_1X1)

            html_text = renderer.render_html_document(
                briefing={
                    "title": "Naver Meeting",
                    "meeting_datetime_label": "2026-04-11 19:52 KST",
                    "participants": ["Alice", "Bob"],
                    "executive_summary": "A concise summary.",
                    "sections": [
                        {
                            "heading": "Platform Shift",
                            "summary": "The team discussed a shift from standalone AI to on-service AI.",
                            "raised_by": "Alice",
                            "speakers": ["Alice", "Bob"],
                            "timestamp_refs": ["00:10.00", "01:20.00"],
                        }
                    ],
                    "decisions": [{"heading": "Keep PDF primary", "summary": "Move toward HTML-first output."}],
                    "action_items": [{"heading": "Prototype renderer", "summary": "Add HTML PDF path."}],
                    "open_questions": [{"heading": "Font strategy", "summary": "Bundle fonts or map fallbacks."}],
                },
                rendering_policy={
                    "renderer_primary_color": "#03C75A",
                    "renderer_title_font": "Malgun Gothic",
                    "renderer_heading_font": "Malgun Gothic",
                    "renderer_body_font": "Malgun Gothic",
                    "executive_summary_heading": "Executive Summary",
                    "sections_heading": "Core Topics",
                    "decisions_heading": "Decisions",
                    "action_items_heading": "Action Items",
                    "open_questions_heading": "Review Items",
                },
                postprocess_requests=[
                    {
                        "title": "Platform Shift Visual",
                        "caption": "Supports Platform Shift",
                        "instruction": "A supporting visual",
                        "target_heading": "Platform Shift",
                        "image_path": str(image_path),
                    }
                ],
                base_dir=base_dir,
            )

        self.assertIn("Naver Meeting", html_text)
        self.assertIn("Executive Summary", html_text)
        self.assertIn("Core Topics", html_text)
        self.assertIn("img", html_text)
        self.assertIn("Decisions", html_text)
        self.assertIn("Action Items", html_text)
        self.assertIn("Review Items", html_text)
        self.assertIn("Alice", html_text)
        self.assertIn("00:10.00", html_text)
        self.assertNotIn("Platform Shift Visual", html_text)
        self.assertNotIn("Supports Platform Shift", html_text)
        self.assertNotIn("A supporting visual", html_text)

    def test_render_html_document_honors_block_order_visibility_and_empty_states(self) -> None:
        renderer = HTMLPDFRenderer()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            html_text = renderer.render_html_document(
                briefing={
                    "title": "Ordered Meeting",
                    "overview_author": "Planner Bot",
                    "overview_session_id": "session-42",
                    "participants": [],
                    "executive_summary": "",
                    "sections": [
                        {
                            "heading": "Hidden Topic",
                            "summary": "This section should not render.",
                        }
                    ],
                    "decisions": [],
                    "action_items": [{"heading": "Hidden Action", "summary": "Should stay hidden."}],
                    "open_questions": [],
                    "risk_signals": [{"heading": "Timeline", "summary": "Delivery dates are compressing."}],
                },
                rendering_policy={
                    "show_title": "never",
                    "show_overview": "always",
                    "show_overview_author": "always",
                    "show_overview_session_id": "always",
                    "show_overview_participants": "never",
                    "show_sections": "never",
                    "show_decisions": "always",
                    "show_action_items": "never",
                    "show_open_questions": "never",
                    "show_risk_signals": "always",
                    "show_memo": "always",
                    "memo_heading": "Operator Note",
                    "memo_text": "Follow the skill exactly.",
                    "risk_signals_heading": "Risks",
                    "decisions_heading": "Decisions",
                    "empty_decisions_message": "No decisions yet.",
                    "result_block_order": ["memo", "risk_signals", "overview", "decisions"],
                    "result_block_order_mode": "exact",
                },
                postprocess_requests=[],
                base_dir=base_dir,
            )

        self.assertLess(html_text.index("Operator Note"), html_text.index("Risks"))
        self.assertLess(html_text.index("Risks"), html_text.index("Overview"))
        self.assertLess(html_text.index("Overview"), html_text.index("Decisions"))
        self.assertIn("Planner Bot", html_text)
        self.assertIn("session-42", html_text)
        self.assertIn("No decisions yet.", html_text)
        self.assertNotIn("Hidden Topic", html_text)
        self.assertNotIn("Hidden Action", html_text)
        self.assertNotIn("Action Items", html_text)

    def test_render_html_document_hides_section_trace_fields_when_disabled(self) -> None:
        renderer = HTMLPDFRenderer()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            html_text = renderer.render_html_document(
                briefing={
                    "title": "Trace Control",
                    "executive_summary": "Summary body.",
                    "sections": [
                        {
                            "heading": "Topic One",
                            "summary": "Trace metadata should disappear.",
                            "raised_by": "Alice",
                            "speakers": ["Alice", "Bob"],
                            "timestamp_refs": ["00:10.00"],
                        }
                    ],
                    "decisions": [],
                    "action_items": [],
                    "open_questions": [],
                },
                rendering_policy={
                    "show_title": "never",
                    "show_section_raised_by": "never",
                    "show_section_speakers": "never",
                    "show_section_timestamps": "never",
                },
                postprocess_requests=[],
                base_dir=base_dir,
            )

        self.assertIn("Topic One", html_text)
        self.assertNotIn("Raised By", html_text)
        self.assertNotIn("Speakers", html_text)
        self.assertNotIn("Timestamps", html_text)
        self.assertNotIn("00:10.00", html_text)

    def test_render_html_document_normalizes_structured_list_items(self) -> None:
        renderer = HTMLPDFRenderer()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            html_text = renderer.render_html_document(
                briefing={
                    "title": "Structured Lists",
                    "executive_summary": "Summary body.",
                    "sections": [],
                    "decisions": [
                        "{'decision': '기존 ESG를 확장 방향으로 삼는다.', 'rationale': '새 용어보다 운영 연결성이 높다.', 'timestamp_refs': ['03:45.48']}"
                    ],
                    "action_items": [
                        "{'item': '확장 ESG 초안 문서를 작성한다.', 'owner': '미정', 'timestamp_refs': ['07:30.06']}",
                        "{'item': '운영 원칙 검토 회의를 예약한다.', 'owner': '정우빈', 'timestamp_refs': ['07:40.00']}"
                    ],
                    "open_questions": [
                        "{'question': '파트너 확장 범위는 어디까지인가?', 'timestamp_refs': ['08:14.01', '08:26.17']}"
                    ],
                    "risk_signals": [
                        {"signal": "라이선스 구조 설명이 더 필요하다.", "timestamp_refs": ["07:10.17"]},
                    ],
                },
                rendering_policy={
                    "show_title": "never",
                    "show_decisions": "always",
                    "show_action_items": "always",
                    "show_open_questions": "always",
                    "show_risk_signals": "always",
                },
                postprocess_requests=[],
                base_dir=base_dir,
            )

        self.assertIn("기존 ESG를 확장 방향으로 삼는다.", html_text)
        self.assertIn("새 용어보다 운영 연결성이 높다.", html_text)
        self.assertIn("확장 ESG 초안 문서를 작성한다.", html_text)
        self.assertIn("운영 원칙 검토 회의를 예약한다. (정우빈)", html_text)
        self.assertNotIn("(미정)", html_text)
        self.assertIn("파트너 확장 범위는 어디까지인가?", html_text)
        self.assertIn("라이선스 구조 설명이 더 필요하다.", html_text)
        self.assertNotIn("timestamp_refs", html_text)
        self.assertNotIn("{&#x27;question&#x27;", html_text)
        self.assertNotIn("{&#x27;signal&#x27;", html_text)
        self.assertNotIn("{&#x27;decision&#x27;", html_text)
        self.assertNotIn("{&#x27;item&#x27;", html_text)

    def test_render_html_document_includes_font_imports_and_surface_variables(self) -> None:
        renderer = HTMLPDFRenderer()
        renderer._prepare_font_resources = lambda **_kwargs: {  # type: ignore[method-assign]
            "stylesheets": ["https://example.com/suit.css"],
            "css": '@font-face { font-family: "SUIT Local"; src: url(".html-font-assets/suit.woff2") format("woff2"); }',
            "assets": [{"source_url": "https://example.com/suit.woff2", "path": "C:/tmp/.html-font-assets/suit.woff2"}],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            html_text = renderer.render_html_document(
                briefing={
                    "title": "Styled Output",
                    "meeting_datetime_label": "2026-04-12 12:00 KST",
                    "participants": ["Alice"],
                    "executive_summary": "Styled summary.",
                    "sections": [],
                    "decisions": [],
                    "action_items": [],
                    "open_questions": [],
                },
                rendering_policy={
                    "renderer_title_font": "SUIT",
                    "renderer_heading_font": "Pretendard",
                    "renderer_body_font": "Noto Sans KR",
                    "renderer_cover_align": "center",
                    "renderer_cover_fill_color": "F7F1D0",
                    "renderer_section_panel_fill_color": "FFF8E1",
                    "renderer_title_divider_size": "5",
                    "renderer_list_line_spacing": "1.9",
                    "postprocess_image_width_inches": "5.2",
                },
                postprocess_requests=[],
                base_dir=base_dir,
            )

        self.assertIn('@font-face { font-family: "SUIT Local"; src: url(".html-font-assets/suit.woff2") format("woff2"); }', html_text)
        self.assertIn("--cover-text-align: center;", html_text)
        self.assertIn("--cover-fill: #F7F1D0;", html_text)
        self.assertIn("--section-panel-fill: #FFF8E1;", html_text)
        self.assertIn("--title-divider-size: 5.00pt;", html_text)
        self.assertIn("--list-line-height: 1.9;", html_text)
        self.assertIn("--postprocess-image-width: 5.20in;", html_text)

    def test_render_html_document_supports_layout_variants(self) -> None:
        renderer = HTMLPDFRenderer()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            html_text = renderer.render_html_document(
                briefing={
                    "title": "Variant Output",
                    "meeting_datetime_label": "2026-04-12 12:00 KST",
                    "participants": ["Alice", "Bob"],
                    "executive_summary": "Expanded summary.",
                    "sections": [
                        {"heading": "Direction", "summary": "Section body."},
                        {"heading": "Execution", "summary": "More body."},
                    ],
                    "decisions": [{"decision": "Lock the internal framing."}],
                    "action_items": [{"item": "Draft the next version.", "owner": "Alice"}],
                    "open_questions": [{"question": "How much structure should vary?"}],
                },
                rendering_policy={
                    "renderer_cover_layout": "split",
                    "renderer_cover_background_style": "solid",
                    "renderer_panel_style": "sharp",
                    "renderer_heading_style": "underline",
                    "renderer_overview_layout": "inline",
                    "renderer_section_style": "divider",
                    "renderer_list_style": "minimal",
                },
                postprocess_requests=[],
                base_dir=base_dir,
            )

        self.assertIn("cover-layout-split", html_text)
        self.assertIn("panel-style-sharp", html_text)
        self.assertIn("heading-style-underline", html_text)
        self.assertIn("overview-layout-inline", html_text)
        self.assertIn("section-style-divider", html_text)
        self.assertIn("list-style-minimal", html_text)
        self.assertIn("overview-inline", html_text)

    def test_render_html_document_includes_block_specific_custom_css_and_selectors(self) -> None:
        renderer = HTMLPDFRenderer()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            html_text = renderer.render_html_document(
                briefing={
                    "title": "Custom CSS Output",
                    "meeting_datetime_label": "2026-04-12 12:00 KST",
                    "participants": ["Alice"],
                    "executive_summary": "Styled summary.",
                    "sections": [{"heading": "Topic", "summary": "Body."}],
                    "decisions": [{"decision": "Keep it open."}],
                    "action_items": [{"item": "Write the next pass."}],
                    "open_questions": [{"question": "Can skill override everything?"}],
                },
                rendering_policy={
                    "renderer_custom_css": (
                        ".block-name-executive_summary.panel-block { border: 0; padding: 0; }\n"
                        ".block-name-decisions .sub-heading { display: none; }"
                    ),
                },
                postprocess_requests=[],
                base_dir=base_dir,
            )

        self.assertIn(".block-name-executive_summary.panel-block { border: 0; padding: 0; }", html_text)
        self.assertIn('data-block="executive_summary"', html_text)
        self.assertIn('data-block="decisions"', html_text)
        self.assertIn("block-name-executive_summary", html_text)
        self.assertIn("block-name-decisions", html_text)

    def test_prepare_font_resources_materializes_local_font_assets(self) -> None:
        renderer = HTMLPDFRenderer()
        css_url = "https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/variable/woff2/SUIT-Variable.css"
        font_url = "https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/variable/woff2/SUIT-Variable.woff2"
        renderer._download_url_text = lambda url: "@font-face { font-family: 'SUIT'; src: url('./SUIT-Variable.woff2') format('woff2'); }" if url == css_url else ""  # type: ignore[method-assign]
        renderer._download_url_bytes = lambda url: b"font-bytes" if url == font_url else b""  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            resources = renderer._prepare_font_resources(
                rendering_policy={
                    "renderer_title_font": "SUIT",
                    "renderer_heading_font": "SUIT",
                    "renderer_body_font": "SUIT",
                },
                base_dir=base_dir,
            )

            self.assertEqual(
                resources["stylesheets"],
                ["https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/variable/woff2/SUIT-Variable.css"],
            )
            self.assertIn('.html-font-assets/', resources["css"])
            self.assertIn("@font-face", resources["css"])
            self.assertEqual(len(resources["assets"]), 1)
            asset_path = Path(resources["assets"][0]["path"])
            self.assertTrue(asset_path.exists())
            self.assertEqual(asset_path.read_bytes(), b"font-bytes")

    def test_render_summary_bundle_writes_html_pdf_and_manifest(self) -> None:
        renderer = HTMLPDFRenderer()
        renderer._browser = "fake-browser"
        renderer._render_pdf_from_html = lambda html_path, pdf_path: pdf_path.write_bytes(b"%PDF-1.4")  # type: ignore[method-assign]
        renderer._prepare_font_resources = lambda **_kwargs: {  # type: ignore[method-assign]
            "stylesheets": [
                "https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/variable/woff2/SUIT-Variable.css",
                "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css",
                "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap",
            ],
            "css": '@font-face { font-family: "SUIT Local"; src: url(".html-font-assets/suit.woff2") format("woff2"); }',
            "assets": [{"source_url": "https://example.com/suit.woff2", "path": "C:/tmp/.html-font-assets/suit.woff2"}],
            "warnings": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            summary_md = base_dir / "summary.md"
            summary_md.write_text("# Summary\n", encoding="utf-8")
            image_path = base_dir / "visual.png"
            image_path.write_bytes(_PNG_1X1)

            exports = renderer.render_summary_bundle(
                summary_md,
                briefing={
                    "title": "HTML PDF Prototype",
                    "meeting_datetime_label": "2026-04-11 20:00 KST",
                    "participants": ["Alice"],
                    "executive_summary": "Summary body",
                    "sections": [],
                    "decisions": [],
                    "action_items": [],
                    "open_questions": [],
                },
                rendering_policy={
                    "renderer_title_font": "SUIT",
                    "renderer_heading_font": "Pretendard",
                    "renderer_body_font": "Noto Sans KR",
                },
                postprocess_requests=[
                    {
                        "title": "Visual",
                        "caption": "Caption",
                        "instruction": "Instruction",
                        "image_path": str(image_path),
                    }
                ],
            )

            export_formats = {item["format"] for item in exports}
            self.assertEqual(export_formats, {"html", "pdf", "render_manifest"})
            manifest_path = next(Path(item["path"]) for item in exports if item["format"] == "render_manifest")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["pdf_generated"])
            self.assertTrue(Path(next(item["path"] for item in exports if item["format"] == "pdf")).exists())
            self.assertEqual(
                manifest["font_stylesheets"],
                [
                    "https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/variable/woff2/SUIT-Variable.css",
                    "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css",
                    "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap",
                ],
            )
            self.assertEqual(
                manifest["font_assets"],
                [{"source_url": "https://example.com/suit.woff2", "path": "C:/tmp/.html-font-assets/suit.woff2"}],
            )

    def test_render_summary_bundle_waits_for_delayed_pdf_output(self) -> None:
        renderer = HTMLPDFRenderer()
        renderer._browser = "fake-browser"
        renderer._pdf_settle_seconds = 1.0

        def delayed_render(_html_path: Path, pdf_path: Path) -> None:
            def writer() -> None:
                time.sleep(0.15)
                pdf_path.write_bytes(b"%PDF-1.4 delayed")

            thread = threading.Thread(target=writer, daemon=True)
            thread.start()

        renderer._render_pdf_from_html = delayed_render  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            summary_md = base_dir / "summary.md"
            summary_md.write_text("# Summary\n", encoding="utf-8")

            exports = renderer.render_summary_bundle(
                summary_md,
                briefing={
                    "title": "Delayed PDF",
                    "meeting_datetime_label": "2026-04-11 20:00 KST",
                    "participants": ["Alice"],
                    "executive_summary": "Summary body",
                    "sections": [],
                    "decisions": [],
                    "action_items": [],
                    "open_questions": [],
                },
                rendering_policy={},
                postprocess_requests=[],
            )

            export_formats = {item["format"] for item in exports}
            self.assertIn("pdf", export_formats)
            pdf_path = Path(next(item["path"] for item in exports if item["format"] == "pdf"))
            self.assertTrue(pdf_path.exists())

    def test_render_pdf_from_html_uses_no_header_footer_flags(self) -> None:
        renderer = HTMLPDFRenderer()
        renderer._browser = "fake-browser"
        captured: dict[str, list[str]] = {}

        def fake_run(args: list[str], *, error_label: str) -> None:
            captured["args"] = args
            captured["label"] = [error_label]

        renderer._run = fake_run  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            html_path = base_dir / "summary.html"
            pdf_path = base_dir / "summary.pdf"
            html_path.write_text("<html></html>", encoding="utf-8")
            renderer._render_pdf_from_html(html_path, pdf_path)

        args = captured["args"]
        self.assertIn("--no-pdf-header-footer", args)
        self.assertIn("--print-to-pdf-no-header", args)
        self.assertTrue(any(arg.startswith("--print-to-pdf=") for arg in args))

    def test_resolve_visual_assets_prefers_raw_image_over_legacy_card_png(self) -> None:
        renderer = HTMLPDFRenderer()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            raw_path = base_dir / "visuals" / "topic-1.png"
            card_path = base_dir / "visuals" / "topic-1-card-1.png"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(_PNG_1X1)
            card_path.write_bytes(_PNG_1X1)

            visuals = renderer._resolve_visual_assets(
                [
                    {
                        "title": "Topic Visual",
                        "image_path": str(card_path),
                    }
                ],
                base_dir=base_dir,
            )

        self.assertEqual(len(visuals), 1)
        self.assertTrue(str(visuals[0]["path"]).endswith("topic-1.png"))


if __name__ == "__main__":
    unittest.main()
