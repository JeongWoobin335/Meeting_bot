from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.meeting_output_skill import (
    build_generated_meeting_output_skill_path,
    build_interactive_meeting_output_skill_path,
    load_meeting_output_skill,
    resolve_result_generation_policy,
    write_generated_meeting_output_skill,
)
from zoom_meeting_bot_cli.config import build_default_config
from zoom_meeting_bot_cli.runtime_env import build_runtime_env


class MeetingOutputSkillTest(unittest.TestCase):
    def test_load_meeting_output_skill_reads_body_without_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = Path(temp_dir) / "SKILL.md"
            skill_path.write_text(
                "---\n"
                "name: custom-meeting-output\n"
                "description: custom skill\n"
                "result_block_order: sections, action_items, decisions, memo\n"
                "show_open_questions: never\n"
                "---\n\n"
                "# Custom Meeting Output\n\n"
                "- Keep the summary focused on decisions.\n",
                encoding="utf-8",
            )

            loaded = load_meeting_output_skill(skill_path)

            self.assertEqual(loaded["name"], "custom-meeting-output")
            self.assertEqual(loaded["description"], "custom skill")
            self.assertEqual(dict(loaded["metadata"]).get("show_open_questions"), "never")
            self.assertNotIn("description:", loaded["body"])
            self.assertIn("Keep the summary focused on decisions.", loaded["body"])

    def test_load_meeting_output_skill_reads_nested_metadata_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = Path(temp_dir) / "SKILL.md"
            skill_path.write_text(
                "---\n"
                "name: custom-meeting-output\n"
                "description: custom skill\n"
                "metadata:\n"
                "  result_block_order: overview, action_items, memo\n"
                "  result_block_order_mode: exact\n"
                "  show_overview: never\n"
                "---\n\n"
                "# Custom Meeting Output\n\n"
                "- Keep only the requested blocks.\n",
                encoding="utf-8",
            )

            loaded = load_meeting_output_skill(skill_path)
            policy = resolve_result_generation_policy(loaded)

            self.assertEqual(dict(loaded["metadata"]).get("show_overview"), "never")
            self.assertEqual(policy["result_block_order"], ["overview", "action_items", "memo"])
            self.assertEqual(policy["result_block_order_mode"], "exact")
            self.assertEqual(policy["show_overview"], "never")

    def test_resolve_result_generation_policy_extracts_renderer_custom_css_from_skill_body(self) -> None:
        skill = {
            "metadata": {
                "renderer_cover_kicker": "INTERNAL REPORT",
            },
            "body": (
                "# Custom Skill\n\n"
                "```css\n"
                ".block-name-executive_summary.panel-block {\n"
                "  border: 0;\n"
                "  padding: 0;\n"
                "}\n"
                "```\n"
            ),
        }

        policy = resolve_result_generation_policy(skill)

        self.assertEqual(policy["renderer_cover_kicker"], "INTERNAL REPORT")
        self.assertIn(".block-name-executive_summary.panel-block", policy["renderer_custom_css"])
        self.assertIn("border: 0;", policy["renderer_custom_css"])

    def test_default_skill_keeps_section_metadata_visible(self) -> None:
        loaded = load_meeting_output_skill()

        policy = resolve_result_generation_policy(loaded)

        self.assertEqual(policy["show_section_raised_by"], "always")
        self.assertEqual(policy["show_section_speakers"], "always")
        self.assertEqual(policy["show_section_timestamps"], "always")

    def test_resolve_result_generation_policy_preserves_direct_renderer_controls(self) -> None:
        policy = resolve_result_generation_policy(
            {
                "metadata": {
                    "renderer_cover_align": "center",
                    "renderer_cover_layout": "split",
                    "renderer_cover_background_style": "solid",
                    "renderer_panel_style": "sharp",
                    "renderer_heading_style": "underline",
                    "renderer_overview_layout": "inline",
                    "renderer_section_style": "divider",
                    "renderer_list_style": "minimal",
                    "renderer_cover_fill_color": "#F7F1D0",
                    "renderer_section_panel_fill_color": "#F9F5E8",
                    "renderer_overview_panel_fill_color": "#F4EFE1",
                    "renderer_panel_radius_pt": "9",
                }
            }
        )

        self.assertEqual(policy["renderer_cover_align"], "center")
        self.assertEqual(policy["renderer_cover_layout"], "split")
        self.assertEqual(policy["renderer_cover_background_style"], "solid")
        self.assertEqual(policy["renderer_panel_style"], "sharp")
        self.assertEqual(policy["renderer_heading_style"], "underline")
        self.assertEqual(policy["renderer_overview_layout"], "inline")
        self.assertEqual(policy["renderer_section_style"], "divider")
        self.assertEqual(policy["renderer_list_style"], "minimal")
        self.assertEqual(policy["renderer_cover_fill_color"], "F7F1D0")
        self.assertEqual(policy["renderer_section_panel_fill_color"], "F9F5E8")
        self.assertEqual(policy["renderer_overview_panel_fill_color"], "F4EFE1")
        self.assertEqual(policy["renderer_panel_radius_pt"], "9")

    def test_resolve_result_generation_policy_merges_base_and_override(self) -> None:
        base_skill = {
            "metadata": {
                "result_block_order": "sections, decisions, action_items, open_questions, memo",
                "renderer_theme_name": "base-theme",
                "show_sections": "always",
                "show_decisions": "always",
                "show_open_questions": "always",
                "show_risk_signals": "never",
                "show_postprocess_requests": "never",
                "show_memo": "always",
                "max_display_sections": "4",
                "max_decisions": "5",
                "max_postprocess_requests": "2",
            }
        }
        override_skill = {
            "metadata": {
                "result_block_order": "sections, action_items, decisions, postprocess_requests, memo",
                "result_block_order_mode": "exact",
                "renderer_theme_name": "kakao-like",
                "renderer_primary_color": "#FEE500",
                "renderer_accent_color": "3C1E1E",
                "renderer_neutral_color": "4A4A4A",
                "renderer_cover_align": "center",
                "renderer_surface_tint_color": "#FFF8CC",
                "renderer_cover_kicker": "COLLABORATION PROPOSAL",
                "postprocess_image_width_inches": "6.4",
                "show_overview": "never",
                "show_executive_summary": "auto",
                "show_overview_participants": "never",
                "show_decisions": "never",
                "show_memo": "never",
                "show_open_questions": "never",
                "show_risk_signals": "auto",
                "show_postprocess_requests": "always",
                "max_action_items": "3",
                "max_postprocess_requests": "5",
                "section_numbering": "plain",
                "overview_heading": "브리핑 개요",
                "section_raised_by_label": "발의",
                "section_speakers_label": "참여 화자",
                "section_timestamps_label": "근거 시각",
                "postprocess_requests_heading": "후속 결과물",
            }
        }

        policy = resolve_result_generation_policy(base_skill, override_skill)

        self.assertEqual(policy["result_block_order"], ["sections", "action_items", "decisions", "postprocess_requests", "memo"])
        self.assertEqual(policy["result_block_order_mode"], "exact")
        self.assertEqual(policy["renderer_theme_name"], "kakao-like")
        self.assertEqual(policy["renderer_primary_color"], "FEE500")
        self.assertEqual(policy["renderer_accent_color"], "3C1E1E")
        self.assertEqual(policy["renderer_neutral_color"], "4A4A4A")
        self.assertEqual(policy["renderer_cover_align"], "center")
        self.assertEqual(policy["renderer_surface_tint_color"], "FFF8CC")
        self.assertEqual(policy["renderer_cover_kicker"], "COLLABORATION PROPOSAL")
        self.assertEqual(policy["postprocess_image_width_inches"], "6.4")
        self.assertEqual(policy["show_overview"], "never")
        self.assertEqual(policy["show_executive_summary"], "auto")
        self.assertEqual(policy["show_overview_participants"], "never")
        self.assertEqual(policy["show_open_questions"], "never")
        self.assertEqual(policy["show_risk_signals"], "auto")
        self.assertEqual(policy["show_postprocess_requests"], "always")
        self.assertEqual(policy["show_decisions"], "never")
        self.assertEqual(policy["show_memo"], "never")
        self.assertEqual(policy["max_display_sections"], 4)
        self.assertEqual(policy["max_decisions"], 5)
        self.assertEqual(policy["max_action_items"], 3)
        self.assertEqual(policy["max_postprocess_requests"], 5)
        self.assertEqual(policy["show_section_raised_by"], "always")
        self.assertEqual(policy["show_section_speakers"], "always")
        self.assertEqual(policy["show_section_timestamps"], "always")
        self.assertEqual(policy["max_section_timestamp_refs"], 4)
        self.assertEqual(policy["section_numbering"], "plain")
        self.assertEqual(policy["overview_heading"], "브리핑 개요")
        self.assertEqual(policy["section_raised_by_label"], "제기자")
        self.assertEqual(policy["section_speakers_label"], "주요 화자")
        self.assertEqual(policy["section_timestamps_label"], "타임스탬프")
        self.assertEqual(policy["postprocess_requests_heading"], "후속 결과물")

    def test_build_runtime_env_exports_meeting_output_skill_path(self) -> None:
        config = build_default_config()

        env = build_runtime_env(config)

        exported_path = Path(env["DELEGATE_MEETING_OUTPUT_SKILL_PATH"])
        self.assertEqual(exported_path.name, "SKILL.md")
        self.assertEqual(exported_path.parent.name, "meeting-output-default")

    def test_build_runtime_env_exports_override_skill_path(self) -> None:
        config = build_default_config()
        config["skills"]["meeting_output_override_path"] = "skills/generated/demo/SKILL.md"

        env = build_runtime_env(config)

        exported_path = Path(env["DELEGATE_MEETING_OUTPUT_OVERRIDE_PATH"])
        self.assertEqual(exported_path.name, "SKILL.md")
        self.assertEqual(exported_path.parent.name, "demo")

    def test_generated_skill_path_uses_skill_folder_layout(self) -> None:
        generated_path = build_generated_meeting_output_skill_path(
            "회의 전체 요약을 더 강조해줘",
            output_dir="skills/generated",
            base_signature="meeting-output-default",
        )

        self.assertEqual(generated_path.name, "SKILL.md")
        self.assertEqual(generated_path.parent.parent.name, "generated")

    def test_interactive_skill_path_uses_skill_folder_layout(self) -> None:
        generated_path = build_interactive_meeting_output_skill_path(
            label="decision-focused",
            output_dir="skills/generated",
        )

        self.assertEqual(generated_path.name, "SKILL.md")
        self.assertEqual(generated_path.parent.parent.name, "generated")

    def test_write_generated_meeting_output_skill_persists_valid_skill_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "generated" / "custom" / "SKILL.md"
            write_generated_meeting_output_skill(
                output_path=target_path,
                name="meeting-output-generated",
                description="생성된 회의 결과물 스킬",
                metadata={
                    "result_block_order": "sections, action_items, decisions, memo",
                    "show_open_questions": "never",
                    "renderer_theme_name": "public-report",
                    "renderer_primary_color": "#245D91",
                    "show_postprocess_requests": "always",
                },
                body="# 생성된 스킬\n\n- 결정사항을 먼저 보여준다.\n",
            )

            loaded = load_meeting_output_skill(target_path)

            self.assertEqual(loaded["name"], "meeting-output-generated")
            self.assertEqual(loaded["description"], "생성된 회의 결과물 스킬")
            self.assertEqual(dict(loaded["metadata"]).get("show_open_questions"), "never")
            self.assertEqual(dict(loaded["metadata"]).get("renderer_theme_name"), "public-report")
            self.assertEqual(dict(loaded["metadata"]).get("renderer_primary_color"), "#245D91")
            self.assertEqual(dict(loaded["metadata"]).get("show_postprocess_requests"), "always")
            self.assertIn("결정사항을 먼저 보여준다.", loaded["body"])


if __name__ == "__main__":
    unittest.main()
