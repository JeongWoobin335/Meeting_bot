from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.design_agent import MeetingOutputDesignAgent


class MeetingOutputDesignAgentTest(unittest.TestCase):
    def test_resolve_preserves_direct_renderer_surface_controls(self) -> None:
        agent = MeetingOutputDesignAgent()

        result = agent.resolve(
            active_skill={
                "name": "meeting-output-freeform-design",
                "description": "자유로운 결과물 디자인 지시",
                "body": "폰트와 레이아웃을 사용자의 의도대로 유지한다.",
                "metadata": {},
            },
            current_policy={
                "renderer_cover_align": "center",
                "renderer_cover_fill_color": "F7F1D0",
                "renderer_section_panel_fill_color": "F9F5E8",
                "renderer_overview_panel_fill_color": "F4EFE1",
            },
            source="generated_new",
        )

        policy = dict(result["resolved_policy"])
        packet = dict(result["intent_packet"])
        self.assertEqual(policy["renderer_cover_align"], "center")
        self.assertEqual(policy["renderer_cover_fill_color"], "F7F1D0")
        self.assertEqual(policy["renderer_section_panel_fill_color"], "F9F5E8")
        self.assertEqual(policy["renderer_overview_panel_fill_color"], "F4EFE1")
        self.assertEqual(packet["cover_align"], "center")

    def test_resolve_applies_direct_renderer_overrides_without_lossy_normalization(self) -> None:
        agent = MeetingOutputDesignAgent()

        result = agent.resolve(
            active_skill={
                "name": "meeting-output-skill-override",
                "description": "스킬 frontmatter가 직접 renderer 힌트를 제공한다.",
                "body": "표지는 정렬만 조정하고 킥커를 유지한다.",
                "metadata": {},
            },
            current_policy={
                "renderer_cover_align": "center",
                "renderer_cover_kicker": "COLLABORATION PROPOSAL",
                "renderer_section_panel_fill_color": "F9F5E8",
            },
            source="generated_new",
        )

        policy = dict(result["resolved_policy"])
        self.assertEqual(policy["renderer_cover_align"], "center")
        self.assertEqual(policy["renderer_cover_kicker"], "COLLABORATION PROPOSAL")
        self.assertEqual(policy["renderer_section_panel_fill_color"], "F9F5E8")

    def test_resolve_keeps_current_policy_when_skill_does_not_override(self) -> None:
        agent = MeetingOutputDesignAgent()

        result = agent.resolve(
            active_skill={
                "name": "meeting-output-default",
                "description": "기본 결과물",
                "body": "기본 흐름을 유지한다.",
                "metadata": {},
            },
            current_policy={
                "renderer_cover_kicker": "USER PICKED",
            },
            source="base_default",
        )

        policy = dict(result["resolved_policy"])
        self.assertEqual(policy["renderer_cover_kicker"], "USER PICKED")

    def test_intent_packet_keeps_raw_skill_lines(self) -> None:
        agent = MeetingOutputDesignAgent()

        result = agent.resolve(
            active_skill={
                "name": "meeting-output-lines",
                "description": "설명 한 줄",
                "body": "# 시각 자료\n\n- 문서 안에 실제로 들어가야 한다.\n- 회의 개요 뒤에 붙인다.\n",
                "metadata": {},
            },
            current_policy={},
            source="generated_new",
        )

        packet = dict(result["intent_packet"])
        self.assertIn("설명 한 줄", list(packet["directive_lines"]))
        self.assertIn("# 시각 자료", list(packet["directive_lines"]))
        self.assertIn("- 문서 안에 실제로 들어가야 한다.", list(packet["directive_lines"]))
        self.assertIn("- 회의 개요 뒤에 붙인다.", list(packet["directive_lines"]))


if __name__ == "__main__":
    unittest.main()
