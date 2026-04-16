from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zoom_meeting_bot_cli.config import build_default_config, write_config
from zoom_meeting_bot_cli.skill_manager import (
    activate_meeting_output_override,
    append_skill_compose_message,
    build_interactive_skill_target_path,
    build_session_skill_refinement_prompt,
    build_skill_compose_turn_prompt,
    clear_meeting_output_override,
    describe_skill_state,
    finalize_composed_skill,
    list_generated_skill_assets,
    prepare_skill_compose_workspace,
    resolve_skill_asset_selection,
    write_skill_compose_user_message,
)


class SkillManagerTest(unittest.TestCase):
    def test_build_interactive_skill_target_path_uses_generated_directory(self) -> None:
        config = build_default_config()
        config["skills"]["generated_meeting_output_dir"] = "skills/generated"

        target_path = build_interactive_skill_target_path(config, label="formal-summary")

        self.assertEqual(target_path.name, "SKILL.md")
        self.assertEqual(target_path.parent.parent.name, "generated")

    def test_prepare_skill_compose_workspace_creates_isolated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_skill_path = Path(temp_dir) / "base" / "SKILL.md"
            base_skill_path.parent.mkdir(parents=True, exist_ok=True)
            base_skill_path.write_text(
                "---\nname: base\ndescription: base skill\n---\n\n# Base Skill\n",
                encoding="utf-8",
            )
            final_output_path = Path(temp_dir) / "generated" / "demo" / "SKILL.md"

            workspace = prepare_skill_compose_workspace(
                base_skill_path=base_skill_path,
                final_output_path=final_output_path,
            )

            self.assertTrue(Path(workspace["sandbox_dir"]).exists())
            self.assertEqual((Path(workspace["sandbox_dir"]) / "BASE_SKILL.md").read_text(encoding="utf-8"), base_skill_path.read_text(encoding="utf-8"))
            self.assertTrue((Path(workspace["sandbox_dir"]) / "CONVERSATION.md").exists())
            self.assertTrue((Path(workspace["sandbox_dir"]) / "USER_MESSAGE.md").exists())
            self.assertTrue((Path(workspace["sandbox_dir"]) / "SKILL.md").exists())

    def test_compose_prompt_is_sandbox_only_and_non_repo(self) -> None:
        prompt = build_skill_compose_turn_prompt()

        self.assertIn("BASE_SKILL.md", prompt)
        self.assertIn("CONVERSATION.md", prompt)
        self.assertIn("USER_MESSAGE.md", prompt)
        self.assertIn("SKILL.md", prompt)
        self.assertIn("Do not inspect parent directories", prompt)
        self.assertIn("Do not mention internal implementation details", prompt)
        self.assertIn("do not explain the internal slot mapping", prompt.lower())
        self.assertIn("image briefs", prompt)
        self.assertIn("result post-processing", prompt)
        self.assertIn("web search", prompt)
        self.assertIn("must first use web search", prompt)
        self.assertIn("Do not rely on prior model memory alone", prompt)
        self.assertIn("renderer_title_font", prompt)
        self.assertIn("renderer_cover_kicker", prompt)
        self.assertIn("result_block_order_mode", prompt)
        self.assertIn("show_overview", prompt)
        self.assertIn("section_numbering", prompt)

    def test_compose_workspace_tracks_user_and_assistant_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            (workspace_dir / "CONVERSATION.md").write_text("# Skill Compose Conversation\n", encoding="utf-8")

            write_skill_compose_user_message(workspace_dir=workspace_dir, text="회의 전체 요약을 더 강조해줘")
            append_skill_compose_message(workspace_dir=workspace_dir, role="User", text="회의 전체 요약을 더 강조해줘")
            append_skill_compose_message(workspace_dir=workspace_dir, role="Assistant", text="전체 요약을 더 앞세우는 방향으로 반영할게요.")

            self.assertEqual((workspace_dir / "USER_MESSAGE.md").read_text(encoding="utf-8").strip(), "회의 전체 요약을 더 강조해줘")
            conversation = (workspace_dir / "CONVERSATION.md").read_text(encoding="utf-8")
            self.assertIn("## User", conversation)
            self.assertIn("## Assistant", conversation)

    def test_finalize_composed_skill_copies_valid_skill_to_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sandbox_skill_path = Path(temp_dir) / "sandbox" / "SKILL.md"
            sandbox_skill_path.parent.mkdir(parents=True, exist_ok=True)
            sandbox_skill_path.write_text(
                "---\n"
                "name: meeting-output-brief-first\n"
                "description: brief-first override\n"
                "result_block_order: sections, action_items, decisions, memo\n"
                "show_open_questions: never\n"
                "---\n\n"
                "# 브리핑 우선 오버라이드\n\n"
                "- 회의 전체 요약을 먼저 보여준다.\n",
                encoding="utf-8",
            )
            final_output_path = Path(temp_dir) / "generated" / "brief-first" / "SKILL.md"

            finalized = finalize_composed_skill(
                sandbox_skill_path=sandbox_skill_path,
                final_output_path=final_output_path,
            )

            self.assertIsNotNone(finalized)
            self.assertTrue(Path(finalized or "").exists())
            stored = Path(finalized or "").read_text(encoding="utf-8")
            self.assertIn("meeting-output-brief-first", stored)
            self.assertIn("회의 전체 요약을 먼저 보여준다", stored)
            self.assertIn("metadata:", stored)
            self.assertIn('show_open_questions: "never"', stored)

    def test_activate_meeting_output_override_updates_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "zoom-meeting-bot.config.json"
            config = build_default_config()
            write_config(config_path, config)

            updated = activate_meeting_output_override(
                config=config,
                config_path=config_path,
                skill_path=Path(temp_dir) / "skills" / "generated" / "demo" / "SKILL.md",
            )

            self.assertTrue(str(updated["skills"]["meeting_output_override_path"]).endswith("SKILL.md"))
            stored = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(str(stored["skills"]["meeting_output_override_path"]).endswith("SKILL.md"))
            self.assertEqual(stored["skills"]["meeting_output_customization"], "")

    def test_clear_meeting_output_override_clears_override_and_optional_customization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "zoom-meeting-bot.config.json"
            config = build_default_config()
            config["skills"]["meeting_output_override_path"] = "skills/generated/demo/SKILL.md"
            config["skills"]["meeting_output_customization"] = "회의 전체 요약을 더 강조해줘"
            write_config(config_path, config)

            updated = clear_meeting_output_override(
                config=config,
                config_path=config_path,
                clear_customization=True,
            )

            self.assertEqual(updated["skills"]["meeting_output_override_path"], "")
            self.assertEqual(updated["skills"]["meeting_output_customization"], "")

    def test_describe_skill_state_reports_override_and_customization(self) -> None:
        config = build_default_config()
        config["skills"]["meeting_output_override_path"] = "skills/generated/demo/SKILL.md"
        config["skills"]["meeting_output_customization"] = "전체 요약을 더 짧고 강하게"

        state = describe_skill_state(config)

        self.assertTrue(state["base_skill_path"].endswith("SKILL.md"))
        self.assertTrue(state["override_skill_path"].endswith("SKILL.md"))
        self.assertEqual(state["customization_request"], "전체 요약을 더 짧고 강하게")

    def test_list_generated_skill_assets_reports_active_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_default_config()
            generated_dir = Path(temp_dir) / "skills" / "generated"
            config["skills"]["generated_meeting_output_dir"] = str(generated_dir)
            active_path = generated_dir / "brief-first" / "SKILL.md"
            active_path.parent.mkdir(parents=True, exist_ok=True)
            active_path.write_text(
                "---\n"
                "name: meeting-output-brief-first\n"
                "description: brief first\n"
                "---\n\n"
                "# Brief First\n",
                encoding="utf-8",
            )
            second_path = generated_dir / "action-first" / "SKILL.md"
            second_path.parent.mkdir(parents=True, exist_ok=True)
            second_path.write_text(
                "---\n"
                "name: meeting-output-action-first\n"
                "description: action first\n"
                "---\n\n"
                "# Action First\n",
                encoding="utf-8",
            )
            config["skills"]["meeting_output_override_path"] = str(active_path)

            assets = list_generated_skill_assets(config)

            self.assertEqual(len(assets), 2)
            self.assertTrue(any(asset.is_active for asset in assets))
            self.assertTrue(any(asset.name == "meeting-output-brief-first" for asset in assets))

    def test_resolve_skill_asset_selection_supports_index_and_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_default_config()
            generated_dir = Path(temp_dir) / "skills" / "generated"
            config["skills"]["generated_meeting_output_dir"] = str(generated_dir)
            first_path = generated_dir / "brief-first" / "SKILL.md"
            first_path.parent.mkdir(parents=True, exist_ok=True)
            first_path.write_text(
                "---\n"
                "name: meeting-output-brief-first\n"
                "description: brief first\n"
                "---\n\n"
                "# Brief First\n",
                encoding="utf-8",
            )
            second_path = generated_dir / "action-first" / "SKILL.md"
            second_path.parent.mkdir(parents=True, exist_ok=True)
            second_path.write_text(
                "---\n"
                "name: meeting-output-action-first\n"
                "description: action first\n"
                "---\n\n"
                "# Action First\n",
                encoding="utf-8",
            )

            assets = list_generated_skill_assets(config)
            by_index = resolve_skill_asset_selection(assets, "1")
            by_name = resolve_skill_asset_selection(assets, "meeting-output-action-first")

            self.assertIsNotNone(by_index)
            self.assertIsNotNone(by_name)
            self.assertEqual((by_name or assets[0]).name, "meeting-output-action-first")

    def test_build_session_skill_refinement_prompt_includes_feedback_and_completed_result_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "delegate_sessions.json"
            markdown_path = Path(temp_dir) / "summary.md"
            markdown_path.write_text("# 기존 결과물\n\n## 액션 아이템\n- 너무 뒤에 있었다.\n", encoding="utf-8")
            config = build_default_config()
            config["runtime"]["store_path"] = str(store_path)
            store_path.write_text(
                json.dumps(
                    {
                        "session-refine-1": {
                            "session_id": "session-refine-1",
                            "meeting_topic": "결과물 개선 회의",
                            "status": "completed",
                            "summary": "전체 요약",
                            "summary_packet": {
                                "briefing": {
                                    "title": "기존 브리핑",
                                    "executive_summary": "전체 요약은 너무 길었다.",
                                    "sections": [
                                        {
                                            "heading": "출력 스타일",
                                            "summary": "사용자는 더 짧고 행동 중심인 결과물을 원했다.",
                                        }
                                    ],
                                    "action_items": ["액션 아이템을 앞으로 옮긴다."],
                                }
                            },
                            "summary_exports": [
                                {
                                    "format": "md",
                                    "path": str(markdown_path),
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            prompt = build_session_skill_refinement_prompt(
                config=config,
                session_id="session-refine-1",
                user_feedback="다음부터는 액션 아이템을 맨 위에 두고 더 짧게 써줘.",
            )

        self.assertIn("다음부터는 액션 아이템을 맨 위에 두고 더 짧게 써줘.", prompt)
        self.assertIn("session-refine-1", prompt)
        self.assertIn("결과물 개선 회의", prompt)
        self.assertIn("기존 브리핑", prompt)
        self.assertIn("출력 스타일", prompt)
        self.assertIn("사용자는 더 짧고 행동 중심인 결과물을 원했다.", prompt)
        self.assertIn("# 기존 결과물", prompt)
        self.assertIn("세션 원문을 다시 요약하라는 뜻이 아니라", prompt)


if __name__ == "__main__":
    unittest.main()
