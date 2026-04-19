from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.ai_client import AiDelegateClient
from local_meeting_ai_runtime.models import DelegateSession


class AiClientSummaryContractTest(unittest.TestCase):
    def test_codex_summarize_prompt_uses_text_only_skill_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = Path(temp_dir) / "SKILL.md"
            skill_path.write_text(
                "---\n"
                "name: custom-meeting-output\n"
                "description: custom skill\n"
                "---\n\n"
                "# Custom Meeting Output\n\n"
                "- Keep the summary grounded in the actual meeting agenda.\n\n"
                "## Visuals\n\n"
                "- Use nano-banana 2 for one supporting image per core topic.\n",
                encoding="utf-8",
            )
            previous_skill_path = os.environ.get("DELEGATE_MEETING_OUTPUT_SKILL_PATH")
            os.environ["DELEGATE_MEETING_OUTPUT_SKILL_PATH"] = str(skill_path)
            try:
                client = AiDelegateClient()
            finally:
                if previous_skill_path is None:
                    os.environ.pop("DELEGATE_MEETING_OUTPUT_SKILL_PATH", None)
                else:
                    os.environ["DELEGATE_MEETING_OUTPUT_SKILL_PATH"] = previous_skill_path

        session = DelegateSession(
            session_id="ai-summary-skill-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Skill prompt injection",
            status="completed",
        )

        captured_request: dict[str, str] = {}

        def fake_codex_json_response(_session, request_text, **_kwargs):
            captured_request["text"] = request_text
            return {
                "title": "Skill prompt injection check",
                "executive_summary": "The summary path should include the raw skill block.",
                "summary": "The summary path should include the raw skill block.",
                "action_items": [],
                "decisions": [],
                "open_questions": [],
                "risk_signals": [],
                "postprocess_requests": [
                    {
                        "kind": "image_brief",
                        "title": "Skill visual",
                        "instruction": "Create one supporting visual.",
                    },
                    {
                        "kind": "appendix_note",
                        "title": "Appendix note",
                        "instruction": "Keep one non-visual appendix note.",
                    },
                ],
                "sections": [
                    {
                        "heading": "Skill linkage",
                        "summary": "The base skill should flow into summary instructions.",
                        "timestamp_refs": ["00:42.50"],
                    }
                ],
            }

        client._codex_json_response = fake_codex_json_response

        result = client._codex_summarize(session)

        self.assertIn("Base result-generation skill:", captured_request["text"])
        self.assertIn("Keep the summary grounded in the actual meeting agenda.", captured_request["text"])
        self.assertNotIn("Use nano-banana 2 for one supporting image per core topic.", captured_request["text"])
        self.assertIn("Do not emit image briefs", captured_request["text"])
        self.assertEqual(
            result["postprocess_requests"],
            [
                {
                    "kind": "appendix_note",
                    "title": "Appendix note",
                    "instruction": "Keep one non-visual appendix note.",
                    "prompt": "",
                    "tool_hint": "",
                    "caption": "",
                    "image_path": "",
                    "count": "1",
                    "placement_notes": "",
                    "target_heading": "",
                    "agenda_context": "",
                    "block_focus": "",
                    "core_message": "",
                    "visual_archetype": "",
                    "visual_center": "",
                    "composition_notes": "",
                    "style_notes": "",
                    "review_feedback": "",
                    "review_status": "",
                    "review_note": "",
                    "key_entities": [],
                    "key_relationships": [],
                    "must_include_labels": [],
                    "avoid_elements": [],
                }
            ],
        )
        removed_layer_key = "execution" + "_plan"
        self.assertNotIn(removed_layer_key, captured_request["text"])

    def test_record_meeting_output_skill_state_does_not_store_removed_plan_layer(self) -> None:
        client = AiDelegateClient()
        session = DelegateSession(
            session_id="ai-skill-state-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Skill state",
            status="completed",
        )

        skill = {
            "name": "meeting-output-generated-demo",
            "description": "generated skill",
            "metadata": {
                "renderer_cover_fill_color": "#F7F1D0",
            },
            "body": "# Visuals\n\n- Put the image directly inside the document.\n",
            "resolved_path": "skills/generated/demo/SKILL.md",
        }

        client._generated_meeting_output_skill = skill
        client._record_meeting_output_skill_state(session, skill, source="generated_new")

        state = dict(session.ai_state.get("meeting_output_skill") or {})
        removed_layer_key = "execution" + "_plan"
        self.assertNotIn(removed_layer_key, state)
        self.assertEqual(state.get("source"), "generated_new")
        self.assertEqual(
            dict(state.get("result_generation_policy") or {}).get("renderer_cover_fill_color"),
            "F7F1D0",
        )
        self.assertIn(
            "- Put the image directly inside the document.",
            list(dict(state.get("design_intent_packet") or {}).get("directive_lines") or []),
        )

    def test_materialize_result_generation_keeps_non_visual_requests_and_adds_synthesized_images(self) -> None:
        client = AiDelegateClient()
        session = DelegateSession(
            session_id="ai-summary-merge-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Merged postprocess flow",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "body": "# Visuals\n\n- Create one nano-banana 2 image tied to the core section.\n",
            "metadata": {},
            "result_generation_policy": {},
        }

        async def fake_generate_result_images(_session, *, count, output_dir, **_kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "merged-1.png"
            path.write_bytes(b"fake-image")
            return [path]

        async def fake_review_result_images(_session, *, candidate_paths, **_kwargs):
            return (list(candidate_paths), [])

        def fake_codex_json_response(_session, _request_text, **_kwargs):
            return {
                "requests": [
                    {
                        "kind": "image_brief",
                        "title": "Core frame visual",
                        "instruction": "Create one supporting image.",
                        "prompt": "Create a clean concept visual.",
                        "tool_hint": "nano-banana",
                        "caption": "Core frame visual",
                        "count": "1",
                        "placement_notes": "Place after the core section",
                        "target_heading": "Core frame",
                    }
                ]
            }

        client._generate_result_images = fake_generate_result_images
        client._review_result_image_candidates = fake_review_result_images
        client._codex_json_response = fake_codex_json_response
        client._build_result_image_structure_plan = lambda *_args, **_kwargs: {
            "agenda_context": "Core meeting context",
            "block_focus": "Core section meaning",
            "core_message": "One visual should support the core section.",
            "visual_archetype": "concept_frame",
            "visual_center": "A centered concept frame",
            "key_entities": ["core frame"],
            "key_relationships": ["supporting explanation"],
            "must_include_labels": ["core frame"],
            "avoid_elements": ["generic stock art"],
            "composition_notes": "Keep the focal structure centered.",
            "style_notes": "Keep the visual clean.",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            result = asyncio.run(
                client.materialize_result_generation(
                    session,
                    {
                        "title": "Merged postprocess flow",
                        "executive_summary": "The text result should survive before images are added.",
                        "sections": [
                            {
                                "heading": "Core frame",
                                "summary": "The meeting defined the core frame.",
                            }
                        ],
                        "postprocess_requests": [
                            {
                                "kind": "appendix_note",
                                "title": "Appendix note",
                                "instruction": "Preserve this non-visual request.",
                            }
                        ],
                    },
                    output_dir=Path(temp_dir),
                )
            )

        self.assertEqual(len(result["postprocess_requests"]), 2)
        self.assertEqual(result["postprocess_requests"][0]["kind"], "appendix_note")
        self.assertEqual(result["postprocess_requests"][1]["kind"], "image_brief")
        self.assertTrue(str(result["postprocess_requests"][1]["image_path"] or "").endswith(".png"))

    def test_apply_resolved_renderer_theme_uses_codex_font_resolution_for_abstract_request_without_keyword_gate(self) -> None:
        client = AiDelegateClient()
        client._codex_path = "codex"
        session = DelegateSession(
            session_id="ai-font-mood-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Rounded font mood",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "name": "meeting-output-soft-round",
            "description": "臾몄꽌 ?꾩껜 ?몄긽?????좎뿰?섍퀬 怨쇳븯寃?李④컩吏 ?딄쾶 ?뺣━?쒕떎.",
            "body": "?ъ슜?먯뿉寃?遺???놁씠 ?쏀엳???몄긽?쇰줈 ?뺣━?섍퀬 ?꾩껜 ?먮쫫???덈Т 媛곸?吏 ?딄쾶 ?〓뒗??",
            "metadata": {},
            "result_generation_policy": {},
        }

        captured_request: dict[str, str] = {}

        def fake_codex_json_response(_session, request_text, **_kwargs):
            captured_request["text"] = request_text
            return {
                "renderer_title_font": "Pretendard",
                "renderer_heading_font": "Pretendard",
                "renderer_body_font": "Noto Sans KR",
                "note": "?좎뿰?섏?留?怨쇱옣?섏? ?딆? 臾몄꽌 ?ㅼ뿉 留욌뒗 議고빀?낅땲??",
            }

        client._codex_json_response = fake_codex_json_response
        client._apply_resolved_renderer_theme(session)

        policy = dict(dict(session.ai_state.get("meeting_output_skill") or {}).get("result_generation_policy") or {})
        self.assertEqual(policy.get("renderer_title_font"), "Pretendard")
        self.assertEqual(policy.get("renderer_heading_font"), "Pretendard")
        self.assertEqual(policy.get("renderer_body_font"), "Noto Sans KR")
        self.assertIn("full user intent, document tone, and design context", captured_request["text"])
        self.assertIn("?ъ슜?먯뿉寃?遺???놁씠 ?쏀엳???몄긽", captured_request["text"])

    def test_apply_resolved_renderer_theme_does_not_infer_fonts_locally_when_codex_is_unavailable(self) -> None:
        client = AiDelegateClient()
        client._prefer_codex = False
        session = DelegateSession(
            session_id="ai-font-no-codex-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="No codex font inference",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "name": "meeting-output-no-codex",
            "description": "臾몄꽌 ?꾩껜 ?몄긽?????좎뿰?섍쾶 ?뺣━?쒕떎.",
            "body": "?ъ슜???섎룄??議댁옱?섏?留?濡쒖뺄 洹쒖튃?쇰줈 ?고듃瑜?李띿뼱?댁? ?딅뒗??",
            "metadata": {},
            "result_generation_policy": {},
        }

        client._apply_resolved_renderer_theme(session)

        policy = dict(dict(session.ai_state.get("meeting_output_skill") or {}).get("result_generation_policy") or {})
        self.assertEqual(policy.get("renderer_title_font"), None)
        self.assertEqual(policy.get("renderer_heading_font"), None)
        self.assertEqual(policy.get("renderer_body_font"), None)

    def test_apply_resolved_renderer_theme_uses_codex_font_resolution_for_brand_request(self) -> None:
        client = AiDelegateClient()
        client._codex_path = "codex"
        session = DelegateSession(
            session_id="ai-font-brand-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Brand font mood",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "name": "meeting-output-naver-brand",
            "description": "?ㅼ씠踰??뚯궗 遺꾩쐞湲곕? 李멸퀬??臾몄꽌 ?고듃瑜??〓뒗??",
            "body": "?ㅼ씠踰꾩쓽 怨듦컻 釉뚮옖??遺꾩쐞湲곕? 李멸퀬???뚯궗 臾대뱶??留욌뒗 ?고듃瑜??좏깮?쒕떎.",
            "metadata": {},
            "resolved_renderer_theme": {
                "renderer_theme_name": "NAVER",
            },
            "result_generation_policy": {
                "renderer_theme_name": "NAVER",
            },
        }

        captured_request: dict[str, str] = {}

        def fake_codex_json_response(_session, request_text, **_kwargs):
            captured_request["text"] = request_text
            return {
                "renderer_title_font": "SUIT",
                "renderer_heading_font": "SUIT",
                "renderer_body_font": "Noto Sans KR",
                "note": "?ㅼ씠踰꾩쓽 ?뺣룉???쒕퉬?ㅽ삎 ?몄긽??留욌뒗 議고빀?낅땲??",
            }

        client._codex_json_response = fake_codex_json_response

        client._apply_resolved_renderer_theme(session)

        policy = dict(dict(session.ai_state.get("meeting_output_skill") or {}).get("result_generation_policy") or {})
        self.assertEqual(policy.get("renderer_title_font"), "SUIT")
        self.assertEqual(policy.get("renderer_heading_font"), "SUIT")
        self.assertEqual(policy.get("renderer_body_font"), "Noto Sans KR")
        self.assertIn("first use web search", captured_request["text"])
        self.assertIn("renderer_theme_name", captured_request["text"])

    def test_synthesize_postprocess_requests_from_raw_skill_block(self) -> None:
        client = AiDelegateClient()
        session = DelegateSession(
            session_id="ai-summary-inline-targets-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Inline section visuals",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "body": (
                "# Visuals\n\n"
                "- Use nano-banana 2 for a supporting image.\n"
                "- Put it directly inside the document.\n"
                "- Place it inside the Role Balance section.\n"
            ),
            "metadata": {},
            "result_generation_policy": {
                "renderer_theme_name": "Belbin Korea",
            },
        }

        def fake_codex_json_response(_session, request_text, **_kwargs):
            self.assertIn("Binding skill directives", request_text)
            self.assertIn("Role Balance", request_text)
            removed_layer_key = "execution" + "_plan"
            self.assertNotIn(removed_layer_key, request_text)
            return {
                "requests": [
                    {
                        "kind": "image_brief",
                        "title": "Role Balance visual",
                        "instruction": "Summarize the Role Balance discussion with one supporting image.",
                        "prompt": "Create a clean supporting visual tied directly to the Role Balance section.",
                        "tool_hint": "nano-banana",
                        "caption": "Role Balance discussion visual",
                        "count": "1",
                        "placement_notes": "Place directly inside the Role Balance section",
                        "target_heading": "Role Balance",
                    }
                ]
            }

        client._codex_json_response = fake_codex_json_response

        requests = client._synthesize_postprocess_requests_from_skill(
            session,
            ai_result={
                "title": "Belbin Korea briefing",
                "executive_summary": "Role balance and teamwork structure.",
                "sections": [
                    {
                        "heading": "Role Balance",
                        "summary": "Connect the section visual directly to the role-balance discussion.",
                    }
                ],
            },
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["target_heading"], "Role Balance")
        self.assertEqual(requests[0]["placement_notes"], "Place directly inside the Role Balance section")

    def test_materialize_result_generation_synthesizes_and_materializes_images(self) -> None:
        client = AiDelegateClient()
        session = DelegateSession(
            session_id="ai-summary-synth-images-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Belbin Korea style visuals",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "body": "# Visuals\n\n- Create one nano-banana 2 image.\n- Tie it directly to the core topic.\n",
            "metadata": {},
            "result_generation_policy": {
                "show_postprocess_requests": "auto",
                "postprocess_image_width_inches": "5.9",
            },
        }

        async def fake_generate_result_images(_session, *, request, count, output_dir, **_kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            paths = []
            for index in range(1, count + 1):
                path = output_dir / f"synth-{index}.png"
                path.write_bytes(b"fake-image")
                paths.append(path)
            return paths

        def fake_codex_json_response(_session, _request_text, **_kwargs):
            return {
                "requests": [
                    {
                        "kind": "image_brief",
                        "title": "Core frame visual",
                        "instruction": "Summarize the meeting's core frame with one supporting image.",
                        "prompt": "Create a clean concept visual for the core meeting frame.",
                        "tool_hint": "nano-banana",
                        "caption": "Core topic support visual",
                        "count": "1",
                        "placement_notes": "Place inside the core topics section",
                        "target_heading": "Role-based collaboration frame",
                    }
                ]
            }

        client._generate_result_images = fake_generate_result_images
        client._codex_json_response = fake_codex_json_response
        client._build_result_image_structure_plan = lambda *_args, **_kwargs: {
            "agenda_context": "The meeting organized responsibility owners and approval gates.",
            "block_focus": "This block explains the responsibility map and approval sequence.",
            "core_message": "AI outputs need explicit responsibility owners and approval gates.",
            "visual_archetype": "responsibility_map",
            "visual_center": "A centered responsibility map with approval gates",
            "key_entities": ["responsibility owner", "approval gate", "review flow"],
            "key_relationships": ["generation to review", "review to approval"],
            "must_include_labels": ["responsibility", "review", "approval"],
            "avoid_elements": ["generic office photo", "empty abstract background"],
            "composition_notes": "Show the responsibility path before decorative details.",
            "style_notes": "Keep the image clean and document-like.",
        }
        client._build_result_image_structure_plan = lambda *_args, **_kwargs: {
            "agenda_context": "The meeting organized responsibility owners and approval gates.",
            "block_focus": "This block explains the responsibility map and approval sequence.",
            "core_message": "AI outputs need explicit responsibility owners and approval gates.",
            "visual_archetype": "responsibility_map",
            "visual_center": "A centered responsibility map with approval gates",
            "key_entities": ["responsibility owner", "approval gate", "review flow"],
            "key_relationships": ["generation to review", "review to approval"],
            "must_include_labels": ["responsibility", "review", "approval"],
            "avoid_elements": ["generic office photo", "empty abstract background"],
            "composition_notes": "Show the responsibility path before decorative details.",
            "style_notes": "Keep the image clean and document-like.",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            result = asyncio.run(
                client.materialize_result_generation(
                    session,
                    {
                        "title": "Belbin Korea collaboration structure",
                        "executive_summary": "The meeting organized a role-based collaboration frame.",
                        "sections": [
                            {
                                "heading": "Role-based collaboration frame",
                                "summary": "The meeting defined role balance and collaboration structure.",
                            }
                        ],
                        "postprocess_requests": [],
                    },
                    output_dir=Path(temp_dir),
                )
            )

        self.assertEqual(len(result["postprocess_requests"]), 1)
        self.assertEqual(result["postprocess_requests"][0]["tool_hint"], "nano-banana")
        self.assertEqual(result["postprocess_requests"][0]["target_heading"], "Role-based collaboration frame")
        self.assertTrue(str(result["postprocess_requests"][0]["image_path"] or "").endswith(".png"))

    def test_materialize_result_generation_passes_section_context_into_image_request(self) -> None:
        client = AiDelegateClient()
        session = DelegateSession(
            session_id="ai-summary-synth-context-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="AI governance framework",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "body": "# Visuals\n\n- Create one nano-banana 2 image tied to the target section.\n",
            "metadata": {},
            "result_generation_policy": {},
        }

        captured_request: dict[str, str] = {}

        async def fake_generate_result_images(_session, *, request, count, output_dir, **_kwargs):
            captured_request.update({key: str(value) for key, value in dict(request).items()})
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "synth-1.png"
            path.write_bytes(b"fake-image")
            return [path]

        def fake_codex_json_response(_session, _request_text, **_kwargs):
            return {
                "requests": [
                    {
                        "kind": "image_brief",
                        "title": "Governance structure visual",
                        "instruction": "Show the governance structure in one image.",
                        "prompt": "Create a clear responsibility and approval diagram.",
                        "tool_hint": "nano-banana",
                        "caption": "Governance structure",
                        "count": "1",
                        "placement_notes": "Place directly after the governance section",
                        "target_heading": "AI 嫄곕쾭?뚯뒪 援ъ“",
                    }
                ]
            }

        client._generate_result_images = fake_generate_result_images
        client._codex_json_response = fake_codex_json_response

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(
                client.materialize_result_generation(
                    session,
                    {
                        "title": "AI governance framework",
                        "executive_summary": "The meeting defined a responsibility and approval framework for AI operations.",
                        "sections": [
                            {
                                "heading": "AI 嫄곕쾭?뚯뒪 援ъ“",
                                "summary": "The session defined responsibility owners, approval gates, review sequence, and escalation flow.",
                                "timestamp_refs": ["00:11.20", "00:18.44"],
                            }
                        ],
                        "postprocess_requests": [],
                    },
                    output_dir=Path(temp_dir),
                )
            )

        self.assertEqual(captured_request.get("target_heading"), "AI 嫄곕쾭?뚯뒪 援ъ“")
        self.assertEqual(captured_request.get("prompt"), "Create a clear responsibility and approval diagram.")
        self.assertEqual(captured_request.get("instruction"), "Show the governance structure in one image.")
        self.assertEqual(captured_request.get("placement_notes"), "Place directly after the governance section")
        self.assertNotIn("agenda_context", captured_request)
        self.assertNotIn("block_focus", captured_request)
        self.assertNotIn("core_message", captured_request)
        self.assertNotIn("visual_center", captured_request)

    def test_build_result_image_prompt_includes_structural_context_guidance(self) -> None:
        client = AiDelegateClient()
        session = DelegateSession(
            session_id="ai-image-prompt-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="AI governance framework",
            status="completed",
        )

        prompt = client._build_result_image_prompt(
            session,
            title="AI governance framework",
            request={
                "title": "Governance structure visual",
                "instruction": "Show the governance structure in one image.",
                "prompt": "Create a clear responsibility and approval diagram.",
                "caption": "Governance structure",
                "target_heading": "AI 嫄곕쾭?뚯뒪 援ъ“",
                "placement_notes": "Place directly after the governance section",
            },
            rendering_policy={},
        )
        self.assertIn("이미지 제목: Governance structure visual", prompt)
        self.assertIn("이미지 목적: Show the governance structure in one image.", prompt)
        self.assertIn("대상 블록: AI 嫄곕쾭?뚯뒪 援ъ“", prompt)
        self.assertIn("캡션 참고: Governance structure", prompt)
        self.assertIn("배치 참고: Place directly after the governance section", prompt)
        self.assertIn("상세 이미지 브리프:", prompt)
        self.assertIn("Create a clear responsibility and approval diagram.", prompt)
        self.assertNotIn("회의 전체 아젠다", prompt)
        self.assertNotIn("핵심 메시지", prompt)
        self.assertNotIn("business briefing document", prompt)

    def test_generate_result_images_uses_codex_local_nanobanana_only(self) -> None:
        client = AiDelegateClient()
        client._codex_path = "codex"
        client._prefer_codex = True
        client._result_image_timeout = 37.0
        session = DelegateSession(
            session_id="ai-image-route-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="AI governance framework",
            status="completed",
        )
        captured: dict[str, object] = {}

        def fake_codex_json_response(_session, request_text, **kwargs):
            captured["request_text"] = request_text
            captured["timeout_seconds"] = kwargs.get("timeout_seconds")
            return {
                "status": "ok",
                "written_paths": [str((Path.cwd() / "tmp-test-image.png").resolve())],
                "note": "ok",
            }

        def fake_resolve_written_result_image_paths(_value, **_kwargs):
            return [Path("C:/tmp/fake-image.png")]

        client._codex_json_response = fake_codex_json_response
        client._resolve_written_result_image_paths = fake_resolve_written_result_image_paths

        result = asyncio.run(
            client._generate_result_images(
                session,
                title="Governance structure visual",
                request={
                    "title": "Governance structure visual",
                    "instruction": "Show the governance structure in one image.",
                    "prompt": "Create a clear responsibility and approval diagram.",
                    "tool_hint": "nano-banana-2",
                },
                count=1,
                output_dir=Path.cwd(),
                rendering_policy={},
            )
        )

        self.assertEqual(result, [Path("C:/tmp/fake-image.png")])
        request_text = str(captured.get("request_text") or "")
        self.assertIn("Use the user's local nanobanana MCP route for image generation.", request_text)
        self.assertIn("There are no alternative image backends for this task.", request_text)
        self.assertIn("Do not use any image tool other than nanobanana.", request_text)
        self.assertNotIn("equivalent local route", request_text)
        self.assertEqual(captured.get("timeout_seconds"), 37.0)

    def test_materialize_result_generation_preserves_generated_visual_path(self) -> None:
        client = AiDelegateClient()
        session = DelegateSession(
            session_id="ai-summary-visual-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="AI-era ESG expansion",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "body": "# Visuals\n\n- Visualize the core topic.\n",
            "metadata": {},
            "result_generation_policy": {
                "renderer_theme_name": "Belbin Korea",
                "renderer_primary_color": "#2E5B4C",
                "renderer_accent_color": "#6CB4A0",
                "renderer_neutral_color": "#EAF2EE",
                "renderer_heading_font": "Malgun Gothic",
                "renderer_body_font": "Malgun Gothic",
            },
        }
        valid_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0ioAAAAASUVORK5CYII="
        )

        async def fake_generate_result_images(_session, *, count, output_dir, **_kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            paths = []
            for index in range(1, count + 1):
                path = output_dir / f"visual-{index}.png"
                path.write_bytes(valid_png)
                paths.append(path)
            return paths

        def fake_codex_json_response(_session, _request_text, **_kwargs):
            return {
                "requests": [
                    {
                        "kind": "image_brief",
                        "title": "ESG expansion direction",
                        "instruction": "Summarize the core ESG discussion in one supporting image.",
                        "prompt": "Create a finished Korean infographic showing AI-era ESG expansion.",
                        "tool_hint": "nano-banana",
                        "caption": "AI-era ESG support visual",
                        "count": "1",
                        "placement_notes": "Place directly inside the core topic section",
                        "target_heading": "ESG expansion direction",
                    }
                ]
            }

        client._generate_result_images = fake_generate_result_images
        client._codex_json_response = fake_codex_json_response

        with tempfile.TemporaryDirectory() as temp_dir:
            result = asyncio.run(
                client.materialize_result_generation(
                    session,
                    {
                        "title": "AI-era ESG expansion direction",
                        "executive_summary": "The meeting discussed expanding ESG for the AI era.",
                        "sections": [
                            {
                                "heading": "ESG expansion direction",
                                "summary": "Expand from environment to digital infrastructure, from society to human-AI collaboration, and from governance to accountable AI operations.",
                            }
                        ],
                        "postprocess_requests": [],
                    },
                    output_dir=Path(temp_dir),
                )
            )

            relative_path = Path(str(result["postprocess_requests"][0]["image_path"] or ""))
            final_image_path = Path(temp_dir) / relative_path
            self.assertTrue(final_image_path.exists())
            self.assertEqual(final_image_path.name, "visual-1.png")


if __name__ == "__main__":
    unittest.main()

