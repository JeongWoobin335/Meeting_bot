from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.models import DelegateSession, MeetingInput, TranscriptChunk
from local_meeting_ai_runtime.summary_pipeline import DelegateSummaryPipeline


def participant_snapshot(
    name: str,
    offset_seconds: float,
    *,
    event: str | None = None,
) -> MeetingInput:
    metadata = {
        "session_offset_seconds": offset_seconds,
        "raw": {"displayName": name},
    }
    if event:
        metadata["event"] = event
    return MeetingInput(
        input_id=f"participant-{name}-{offset_seconds}",
        input_type="participant_state",
        speaker=name,
        source="zoom_participant_state",
        created_at=f"2026-04-08T14:{10 + int(offset_seconds // 60):02d}:00+09:00",
        metadata=metadata,
    )


def transcript_chunk(
    *,
    speaker: str,
    text: str,
    audio_source: str,
    offset_seconds: float,
) -> TranscriptChunk:
    return TranscriptChunk(
        speaker=speaker,
        text=text,
        source="zoom_audio",
        metadata={
            "audio_source": audio_source,
            "session_start_offset_seconds": offset_seconds,
        },
    )


class SummaryPipelineRichnessTest(unittest.TestCase):
    def test_build_briefing_prefers_explicit_executive_summary_over_richer_overview(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-0",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Bellbin local LLM discussion",
            status="completed",
        )

        detailed_summary = (
            "회의는 벨빈 AI 웹앱 결과와 Zoom 회의 요약 PDF 같은 산출물을 어떻게 설명 자료로 활용할지까지 폭넓게 다뤘다. "
            "특히 로컬 LLM과 CLI·스킬 전환 모델을 함께 검토했다."
        )
        executive_summary = (
            "회의는 벨빈 AI 웹앱 결과를 고객 팀의 로컬 LLM과 어떻게 연결할지, "
            "그리고 개념 정의와 시스템 구현의 역할 경계를 어디서 나눌지에 집중됐다."
        )

        briefing = pipeline.build_briefing(
            session,
            packet=pipeline.build(session),
            ai_result={
                "summary": detailed_summary,
                "executive_summary": executive_summary,
                "sections": [
                    {
                        "heading": "스킬·CLI 전환 모델",
                        "summary": (
                            "참석자들은 벨빈 결과를 로컬 LLM이 이해할 수 있는 CLI·스킬 형태로 전달하는 운영 모델을 검토했다. "
                            "다만 외부 참석자에게 설치까지 요구하는 방식은 신중히 봐야 한다는 의견도 나왔다."
                        ),
                        "timestamp_refs": ["14:19.98", "36:15.32"],
                    }
                ],
            },
        )

        self.assertEqual(briefing["executive_summary"], executive_summary)
        self.assertIn("Zoom 회의 요약 PDF", detailed_summary)
        self.assertNotIn("Zoom 회의 요약 PDF", briefing["executive_summary"])

    def test_build_briefing_preserves_ai_section_summary_without_posthoc_injection(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="AI governance framing",
            status="completed",
        )
        session.transcript.extend(
            [
                transcript_chunk(
                    speaker="meeting_output",
                    text="Notion handles the knowledge space, Slack handles communication, and MCP/skills explain the AI role.",
                    audio_source="system",
                    offset_seconds=21.56,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="The team said MCP and skills are the clearest expression of the AI role in the deck.",
                    audio_source="system",
                    offset_seconds=43.66,
                ),
            ]
        )

        ai_summary = (
            "The meeting defined a role-based AI governance structure. "
            "It used Notion and Slack as concrete examples and positioned MCP/skills as the AI execution layer."
        )
        briefing = pipeline.build_briefing(
            session,
            packet=pipeline.build(session),
            ai_result={
                "summary": "The meeting aligned on an AI governance role frame.",
                "sections": [
                    {
                        "heading": "Role frame",
                        "summary": ai_summary,
                        "timestamp_refs": ["00:21.56", "00:43.66"],
                    }
                ],
            },
        )

        self.assertEqual(briefing["sections"][0]["summary"], ai_summary)

    def test_enrich_sections_for_display_keeps_summary_text_intact(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-2",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Speaker display",
            status="completed",
        )
        session.input_timeline.extend(
            [
                participant_snapshot("WooBIN_bot", 0.0),
                participant_snapshot("정우빈", 0.1),
                participant_snapshot("신대현", 0.2),
                participant_snapshot("정우빈", 75.0, event="active-speaker"),
                participant_snapshot("신대현", 78.0, event="active-speaker"),
            ]
        )
        session.transcript.extend(
            [
                transcript_chunk(
                    speaker="meeting_output",
                    text="The team said the company will care more about why the project exists.",
                    audio_source="system",
                    offset_seconds=75.0,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="The difference will come from problem framing and planning.",
                    audio_source="system",
                    offset_seconds=78.0,
                ),
            ]
        )

        original_summary = (
            "참석자들은 구현 속도보다 문제 정의와 기획력이 더 중요해질 수 있다고 봤다. "
            "특히 왜 만들었는지를 설명하는 능력이 차별점이 될 수 있다고 정리했다."
        )
        enriched = pipeline._enrich_sections_for_display(
            session,
            [
                {
                    "heading": "AI 시대의 개발자 평가 기준",
                    "summary": original_summary,
                    "timestamp_refs": ["01:15.00", "01:18.00"],
                }
            ],
        )[0]

        self.assertEqual(enriched["summary"], original_summary)
        self.assertEqual(enriched["raised_by"], "정우빈")

    def test_build_briefing_keeps_written_style_summary_stable(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-3",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Audio issue",
            status="completed",
        )
        session.transcript.extend(
            [
                transcript_chunk(
                    speaker="meeting_output",
                    text="어 왜 마이크가 왜 이렇게 작지?",
                    audio_source="system",
                    offset_seconds=34.77,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="이어폰을 빼고 스피커로 전환해서 문제를 바로 확인해 보자는 의견이 나왔다.",
                    audio_source="system",
                    offset_seconds=79.55,
                ),
            ]
        )

        written_summary = (
            "회의 초반에는 마이크 소리가 작게 들리는 문제가 확인됐다. "
            "참석자들은 이어폰을 빼고 스피커로 전환해 원인을 빠르게 점검하자는 쪽으로 대응 방향을 잡았다."
        )
        briefing = pipeline.build_briefing(
            session,
            packet=pipeline.build(session),
            ai_result={
                "summary": "The meeting started with an audio issue check.",
                "sections": [
                    {
                        "heading": "오디오 문제 점검",
                        "summary": written_summary,
                        "timestamp_refs": ["00:34.77", "01:19.55"],
                    }
                ],
            },
        )

        self.assertEqual(briefing["sections"][0]["summary"], written_summary)
        self.assertNotIn("구체적으로는", briefing["sections"][0]["summary"])

    def test_render_summary_markdown_respects_result_generation_policy_order(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-4",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Result block order",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "design_intent_packet": {
                "cover_align": "center",
            },
            "result_generation_policy": {
                "result_block_order": ["sections", "action_items", "decisions", "memo"],
                "show_open_questions": "never",
                "show_risk_signals": "never",
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot", "정우빈"],
            "briefing": {
                "title": "브리핑 우선 검토",
                "meeting_datetime_label": "2026-04-10 17:00 KST",
                "executive_summary": "회의 전체 요약을 먼저 보여준 뒤 액션 아이템을 강조하는 배치를 검토했다.",
                "sections": [
                    {
                        "heading": "배치 정책",
                        "summary": "참석자들은 액션 아이템을 결정사항보다 앞에 두는 편이 더 읽기 쉽다고 봤다.",
                        "timestamp_refs": ["00:42.50"],
                    }
                ],
                "decisions": ["후속 검토 후 배치 순서를 확정한다."],
                "action_items": ["액션 아이템 우선 배치 초안을 검토한다."],
                "open_questions": ["열린 질문은 필요할 때만 보이게 할까?"],
                "risk_signals": ["강조 순서가 회의 성격과 어긋날 수 있다."],
                "participants": ["WooBIN_bot", "정우빈"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertLess(markdown.index("## 액션 아이템"), markdown.index("## 결정사항"))
        self.assertNotIn("## 열린 질문", markdown)
        self.assertNotIn("## 리스크 신호", markdown)

    def test_render_summary_markdown_respects_exact_order_and_top_block_visibility(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-4-exact",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Exact order",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "design_intent_packet": {
                "cover_align": "center",
            },
            "result_generation_policy": {
                "result_block_order": ["overview", "executive_summary", "action_items"],
                "result_block_order_mode": "exact",
                "show_title": "never",
                "show_overview": "never",
                "show_executive_summary": "never",
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot", "정우빈"],
            "briefing": {
                "title": "정확한 블록만",
                "meeting_datetime_label": "2026-04-10 18:00 KST",
                "executive_summary": "개요와 전체 요약은 이번 결과물에서 숨긴다.",
                "sections": [
                    {
                        "heading": "숨겨질 섹션",
                        "summary": "exact 모드에서는 지정하지 않은 기본 블록이 뒤에 붙지 않아야 한다.",
                        "timestamp_refs": ["00:40.00"],
                    }
                ],
                "decisions": ["숨겨질 결정사항"],
                "action_items": ["사용자가 지정한 액션만 남긴다."],
                "open_questions": ["숨겨질 질문"],
                "risk_signals": ["숨겨질 리스크"],
                "participants": ["WooBIN_bot", "정우빈"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertFalse(markdown.startswith("# 정확한 블록만"))
        self.assertIn("## 액션 아이템", markdown)
        self.assertIn("- 사용자가 지정한 액션만 남긴다.", markdown)
        self.assertNotIn("## 회의 개요", markdown)
        self.assertNotIn("## 회의 전체 요약", markdown)
        self.assertNotIn("## 핵심 논의 주제", markdown)
        self.assertNotIn("## 결정사항", markdown)
        self.assertNotIn("## 메모", markdown)

    def test_render_summary_markdown_allows_plain_section_headings_and_custom_labels(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-4-labels",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Section label freedom",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "result_generation_policy": {
                "result_block_order": ["sections"],
                "result_block_order_mode": "exact",
                "section_numbering": "plain",
                "show_section_raised_by": "always",
                "show_section_speakers": "always",
                "show_section_timestamps": "always",
                "section_raised_by_label": "발의",
                "section_speakers_label": "화자",
                "section_timestamps_label": "근거 시각",
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot", "정우빈"],
            "briefing": {
                "title": "섹션 라벨 검토",
                "meeting_datetime_label": "2026-04-10 18:30 KST",
                "executive_summary": "섹션 보조 라벨을 사용자가 정할 수 있어야 한다.",
                "sections": [
                    {
                        "heading": "자유 섹션",
                        "summary": "번호와 보조 라벨을 사용자 결과물 스타일에 맞춰 바꾼다.",
                        "timestamp_refs": ["00:42.50"],
                        "raised_by": "정우빈",
                        "speakers": ["정우빈"],
                    }
                ],
                "participants": ["WooBIN_bot", "정우빈"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertIn("### 자유 섹션", markdown)
        self.assertNotIn("### 1. 자유 섹션", markdown)
        self.assertIn("- 제기자: 정우빈", markdown)
        self.assertIn("- 주요 화자: 정우빈", markdown)
        self.assertIn("- 타임스탬프: `00:42.50`", markdown)

    def test_render_summary_markdown_respects_heading_policy(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-5",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Heading policy",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "result_generation_policy": {
                "result_block_order": ["sections", "memo"],
                "show_open_questions": "never",
                "show_risk_signals": "never",
                "show_memo": "always",
                "overview_heading": "브리핑 개요",
                "executive_summary_heading": "핵심 브리핑",
                "sections_heading": "핵심 블록",
                "memo_heading": "참고 메모",
                "memo_text": "추가 원문은 별도 파일에서 확인한다.",
                "max_display_sections": 3,
                "empty_executive_summary_message": "요약이 아직 없습니다.",
                "empty_sections_message": "섹션이 아직 없습니다.",
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot"],
            "briefing": {
                "title": "헤더 정책 검토",
                "meeting_datetime_label": "2026-04-10 19:00 KST",
                "executive_summary": "결과물 헤더 문구를 skill에서 바꾸는 정책을 검토했다.",
                "sections": [],
                "decisions": [],
                "action_items": [],
                "open_questions": [],
                "risk_signals": [],
                "participants": ["WooBIN_bot"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertIn("## 브리핑 개요", markdown)
        self.assertIn("## 핵심 브리핑", markdown)
        self.assertIn("## 핵심 블록", markdown)
        self.assertIn("## 참고 메모", markdown)
        self.assertIn("추가 원문은 별도 파일에서 확인한다.", markdown)

    def test_render_summary_markdown_respects_section_display_and_item_limits(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-6",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Section display policy",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "result_generation_policy": {
                "result_block_order": ["sections", "decisions", "action_items", "open_questions", "risk_signals", "memo"],
                "show_decisions": "never",
                "show_open_questions": "always",
                "show_risk_signals": "always",
                "show_memo": "never",
                "show_section_raised_by": "never",
                "show_section_speakers": "never",
                "show_section_timestamps": "never",
                "max_decisions": 1,
                "max_action_items": 1,
                "max_open_questions": 1,
                "max_risk_signals": 1,
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot", "정우빈", "신대현"],
            "briefing": {
                "title": "표시 정책 검토",
                "meeting_datetime_label": "2026-04-10 20:00 KST",
                "executive_summary": "섹션 보조 정보와 결과물 개수 제한을 skill에서 제어하는 방식을 검토했다.",
                "sections": [
                    {
                        "heading": "표시 정책",
                        "summary": "보조 정보는 필요할 때만 노출하고, 최종 결과물은 과하게 길어지지 않게 제한한다.",
                        "timestamp_refs": ["00:42.50", "01:12.00"],
                        "raised_by": "정우빈",
                        "speakers": ["정우빈", "신대현"],
                    }
                ],
                "decisions": ["첫 번째 결정사항", "두 번째 결정사항"],
                "action_items": ["첫 번째 액션", "두 번째 액션"],
                "open_questions": ["첫 번째 열린 질문", "두 번째 열린 질문"],
                "risk_signals": ["첫 번째 리스크", "두 번째 리스크"],
                "participants": ["WooBIN_bot", "정우빈", "신대현"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertIn("- 제기자:", markdown)
        self.assertIn("- 주요 화자:", markdown)
        self.assertIn("- 타임스탬프:", markdown)
        self.assertNotIn("## 결정사항", markdown)
        self.assertNotIn("- 첫 번째 결정사항", markdown)
        self.assertIn("- 첫 번째 액션", markdown)
        self.assertNotIn("- 두 번째 액션", markdown)
        self.assertIn("- 첫 번째 열린 질문", markdown)
        self.assertNotIn("- 두 번째 열린 질문", markdown)
        self.assertIn("- 첫 번째 리스크", markdown)
        self.assertNotIn("- 두 번째 리스크", markdown)
        self.assertNotIn("## 메모", markdown)

    def test_render_summary_markdown_can_render_postprocess_requests_block(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-7",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Postprocess block",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "result_generation_policy": {
                "result_block_order": ["sections", "postprocess_requests", "memo"],
                "show_open_questions": "never",
                "show_risk_signals": "never",
                "show_postprocess_requests": "always",
                "postprocess_requests_heading": "후속 결과물",
                "max_postprocess_requests": 1,
                "show_memo": "never",
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot", "정우빈"],
            "briefing": {
                "title": "후속 처리 블록 검토",
                "meeting_datetime_label": "2026-04-10 20:30 KST",
                "executive_summary": "결과물 이후에 붙일 이미지 브리프와 렌더링 후속 작업을 검토했다.",
                "sections": [
                    {
                        "heading": "후속 처리",
                        "summary": "회의는 최종 PDF에 붙일 후속 결과물도 같이 설계할 수 있는지 검토했다.",
                        "timestamp_refs": ["00:42.50"],
                    }
                ],
                "postprocess_requests": [
                    {
                        "kind": "image_brief",
                        "title": "기후 흐름 일러스트",
                        "instruction": "기후 논의 흐름을 1장의 시각 자료로 요약한다.",
                        "tool_hint": "nano-banana",
                        "caption": "회의 핵심 흐름 요약용 이미지",
                    },
                    {
                        "kind": "appendix_note",
                        "title": "부록 노트",
                        "instruction": "두 번째 요청",
                    },
                ],
                "participants": ["WooBIN_bot", "정우빈"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertIn("## 후속 결과물", markdown)
        self.assertIn("**기후 흐름 일러스트**", markdown)
        self.assertIn("`nano-banana`", markdown)
        self.assertIn("회의 핵심 흐름 요약용 이미지", markdown)
        self.assertNotIn("부록 노트", markdown)

    def test_build_briefing_keeps_all_ai_sections_without_default_caps(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-7-auto-limit",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Long-form strategy lecture",
            status="completed",
            created_at="2026-04-10T15:00:00+09:00",
            updated_at="2026-04-10T16:35:00+09:00",
        )
        session.transcript.extend(
            [
                transcript_chunk(
                    speaker="meeting_output",
                    text=f"Long-form topic {index}",
                    audio_source="system",
                    offset_seconds=480.0 * index,
                )
                for index in range(1, 15)
            ]
        )

        sections = [
            {
                "heading": f"핵심 주제 {index}",
                "summary": f"긴 회의에서는 {index}번째 주제도 별도 섹션으로 보존해야 한다.",
                "timestamp_refs": [f"{index:02d}:00.00"],
            }
            for index in range(1, 14)
        ]

        briefing = pipeline.build_briefing(
            session,
            packet=pipeline.build(session),
            ai_result={
                "summary": "긴 전략 회의의 전체 흐름을 요약했다.",
                "executive_summary": "긴 회의일수록 핵심 논의 덩어리를 더 많이 살려야 한다.",
                "sections": sections,
            },
        )

        self.assertEqual(len(briefing["sections"]), 13)

    def test_render_summary_markdown_hides_empty_optional_blocks_in_default_state(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-7-default-auto",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Default openness",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "result_generation_policy": {
                "decisions_heading": "Decisions",
                "action_items_heading": "Action Items",
                "open_questions_heading": "Open Questions",
                "risk_signals_heading": "Risk Signals",
                "memo_heading": "Memo",
                "overview_participants_label": "Participants",
                "overview_author_label": "Author",
                "overview_session_id_label": "Session ID",
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot", "정우빈"],
            "briefing": {
                "title": "기본 상태 점검",
                "meeting_datetime_label": "2026-04-10 21:00 KST",
                "executive_summary": "비어 있는 블록은 기본 상태에서 억지로 드러나지 않아야 한다.",
                "sections": [
                    {
                        "heading": "기본 결과물",
                        "summary": "실제 내용이 없는 블록은 자동으로 빠지고, 본문 중심으로 읽혀야 한다.",
                        "timestamp_refs": ["00:42.50"],
                    }
                ],
                "decisions": [],
                "action_items": [],
                "open_questions": [],
                "risk_signals": [],
                "postprocess_requests": [],
                "participants": ["WooBIN_bot", "정우빈"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertNotIn("## 결정사항", markdown)
        self.assertNotIn("## 액션 아이템", markdown)
        self.assertNotIn("## 열린 질문", markdown)
        self.assertNotIn("## 리스크 신호", markdown)
        self.assertNotIn("## 메모", markdown)

        self.assertNotIn("## Decisions", markdown)
        self.assertNotIn("## Action Items", markdown)
        self.assertNotIn("## Open Questions", markdown)
        self.assertNotIn("## Risk Signals", markdown)
        self.assertNotIn("## Memo", markdown)
        self.assertIn("Participants", markdown)
        self.assertNotIn("WooBIN_bot", markdown)
        self.assertNotIn("Author", markdown)
        self.assertNotIn("Session ID", markdown)
        self.assertNotIn(session.session_id, markdown)

    def test_render_summary_markdown_auto_trace_details_follow_semantics_not_section_count(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-7-trace-auto",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Trace detail density",
            status="completed",
            created_at="2026-04-10T19:00:00+09:00",
            updated_at="2026-04-10T20:30:00+09:00",
        )
        session.ai_state["meeting_output_skill"] = {
            "result_generation_policy": {
                "show_section_raised_by": "auto",
                "show_section_speakers": "auto",
                "show_section_timestamps": "auto",
                "section_raised_by_label": "Raised By",
                "section_speakers_label": "Speakers",
                "section_timestamps_label": "Timestamps",
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot", "정우빈", "신대현"],
            "briefing": {
                "title": "장문 브리핑",
                "meeting_datetime_label": "2026-04-10 21:15 KST",
                "executive_summary": "섹션이 많아지면 근거 라인은 본문보다 뒤로 빠져야 한다.",
                "sections": [
                    {
                        "heading": f"주제 {index}",
                        "summary": f"{index}번째 주제는 해설형으로 길게 보존한다.",
                        "timestamp_refs": [f"{index:02d}:42.50"],
                        "raised_by": "정우빈",
                        "speakers": ["정우빈", "신대현"],
                    }
                    for index in range(1, 10)
                ],
                "participants": ["WooBIN_bot", "정우빈", "신대현"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertIn("- 제기자:", markdown)
        self.assertIn("- 주요 화자:", markdown)
        self.assertIn("- 타임스탬프:", markdown)

    def test_render_summary_markdown_auto_raised_by_shows_when_it_distinguishes_sections(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-7-trace-raised-by",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Trace distinction",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "result_generation_policy": {
                "show_section_raised_by": "auto",
                "show_section_speakers": "auto",
                "show_section_timestamps": "auto",
                "section_raised_by_label": "Raised By",
                "section_speakers_label": "Speakers",
                "section_timestamps_label": "Timestamps",
            }
        }
        session.summary_packet = {
            "participants": ["WooBIN_bot", "Alice", "Bob"],
            "briefing": {
                "title": "Trace distinction",
                "meeting_datetime_label": "2026-04-10 21:20 KST",
                "executive_summary": "Different raisers across sections should surface in auto mode.",
                "sections": [
                    {
                        "heading": "Topic 1",
                        "summary": "Alice opened the first topic.",
                        "timestamp_refs": ["01:05.00"],
                        "raised_by": "Alice",
                    },
                    {
                        "heading": "Topic 2",
                        "summary": "Bob opened the second topic.",
                        "timestamp_refs": ["09:10.00"],
                        "raised_by": "Bob",
                    },
                ],
                "participants": ["WooBIN_bot", "Alice", "Bob"],
            },
        }

        markdown = pipeline.render_summary_markdown(session)

        self.assertIn("- 제기자: Alice", markdown)
        self.assertIn("- 제기자: Bob", markdown)
        self.assertIn("- 타임스탬프:", markdown)

    def test_build_briefing_preserves_rendering_policy_for_export(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-8",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Renderer theme",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "design_intent_packet": {
                "cover_align": "center",
            },
            "result_generation_policy": {
                "renderer_theme_name": "kakao-like",
                "renderer_primary_color": "FEE500",
                "renderer_accent_color": "3C1E1E",
                "renderer_neutral_color": "4A4A4A",
                "renderer_cover_align": "center",
                "renderer_surface_tint_color": "FFF8CC",
                "renderer_cover_kicker": "COLLABORATION PROPOSAL",
                "postprocess_image_width_inches": "6.2",
                "overview_heading": "맞춤 개요",
                "overview_datetime_label": "일시",
                "section_raised_by_label": "발의",
                "section_speakers_label": "화자",
                "section_timestamps_label": "근거 시각",
            }
        }

        briefing = pipeline.build_briefing(
            session,
            packet=pipeline.build(session),
            ai_result={
                "summary": "렌더러 테마와 결과물 색감 방향을 검토했다.",
                "executive_summary": "회의는 결과물 렌더링 방향도 skill이 잡을 수 있는지 검토했다.",
                "sections": [
                    {
                        "heading": "렌더링",
                        "summary": "브랜드 느낌을 주는 색감과 분위기를 별도 renderer 정책으로 담는 방식을 논의했다.",
                        "timestamp_refs": ["00:55.10"],
                    }
                ],
            },
        )

        self.assertEqual(briefing["renderer_profile"], "default")
        self.assertEqual(briefing["design_intent_packet"]["cover_align"], "center")
        self.assertEqual(briefing["rendering_policy"]["renderer_theme_name"], "kakao-like")
        self.assertEqual(briefing["rendering_policy"]["renderer_primary_color"], "FEE500")
        self.assertEqual(briefing["rendering_policy"]["renderer_cover_align"], "center")
        self.assertEqual(briefing["rendering_policy"]["renderer_surface_tint_color"], "FFF8CC")
        self.assertEqual(briefing["rendering_policy"]["renderer_cover_kicker"], "COLLABORATION PROPOSAL")
        self.assertEqual(briefing["rendering_policy"]["postprocess_image_width_inches"], "6.2")
        self.assertEqual(briefing["rendering_policy"]["overview_heading"], "맞춤 개요")
        self.assertEqual(briefing["rendering_policy"]["overview_datetime_label"], "일시")
        self.assertEqual(briefing["rendering_policy"]["section_raised_by_label"], "제기자")
        self.assertEqual(briefing["rendering_policy"]["section_speakers_label"], "주요 화자")
        self.assertEqual(briefing["rendering_policy"]["section_timestamps_label"], "타임스탬프")
        self.assertEqual(briefing["rendering_policy"]["postprocess_requests_heading"], "추가 결과물 제안")


    def test_build_briefing_carries_visibility_flags_for_html_export(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="richness-9",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Visibility policy carry-through",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "result_generation_policy": {
                "show_risk_signals": "never",
                "show_decisions": "always",
                "show_action_items": "always",
                "risk_signals_heading": "리스크 신호",
                "decisions_heading": "결정사항",
                "action_items_heading": "액션 아이템",
            }
        }

        briefing = pipeline.build_briefing(
            session,
            packet=pipeline.build(session),
            ai_result={
                "summary": "A summary.",
                "decisions": ["{'decision': '기본 방향을 유지한다.'}"],
                "action_items": ["{'item': '초안 문서를 작성한다.'}"],
                "risk_signals": ["{'signal': '이 항목은 숨겨져야 한다.'}"],
            },
        )

        rendering_policy = dict(briefing.get("rendering_policy") or {})
        self.assertEqual(rendering_policy.get("show_risk_signals"), "never")
        self.assertEqual(rendering_policy.get("show_decisions"), "always")
        self.assertEqual(rendering_policy.get("show_action_items"), "always")
        self.assertEqual(rendering_policy.get("risk_signals_heading"), "리스크 신호")
        self.assertEqual(rendering_policy.get("decisions_heading"), "결정사항")
        self.assertEqual(rendering_policy.get("action_items_heading"), "액션 아이템")


if __name__ == "__main__":
    unittest.main()
