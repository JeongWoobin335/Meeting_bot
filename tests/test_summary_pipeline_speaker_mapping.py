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
        created_at=f"2026-04-08T11:{5 + int(offset_seconds // 60):02d}:00+09:00",
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


class SummaryPipelineSpeakerMappingTest(unittest.TestCase):
    def test_preferred_named_speakers_does_not_force_remaining_remote_name(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="speaker-map-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Speaker Mapping",
            status="completed",
        )
        session.input_timeline.extend(
            [
                participant_snapshot("WooBIN_bot", 0.0),
                participant_snapshot("정우빈", 0.1),
                participant_snapshot("신대현", 0.2),
                participant_snapshot("신대현", 10.0, event="active-speaker"),
                participant_snapshot("신대현", 20.0, event="active-speaker"),
                participant_snapshot("신대현", 30.0, event="active-speaker"),
                participant_snapshot("신대현", 40.0, event="active-speaker"),
                participant_snapshot("신대현", 50.0, event="active-speaker"),
                participant_snapshot("정우빈", 52.0, event="active-speaker"),
                participant_snapshot("신대현", 60.0, event="active-speaker"),
                participant_snapshot("신대현", 70.0, event="active-speaker"),
            ]
        )
        session.transcript.extend(
            [
                transcript_chunk(speaker="local_user", text="첫 번째 안내입니다.", audio_source="microphone", offset_seconds=10.0),
                transcript_chunk(speaker="local_user", text="두 번째 안내입니다.", audio_source="microphone", offset_seconds=20.0),
                transcript_chunk(speaker="local_user", text="세 번째 안내입니다.", audio_source="microphone", offset_seconds=30.0),
                transcript_chunk(speaker="meeting_output", text="회의 정리 시작합니다.", audio_source="system", offset_seconds=40.0),
                transcript_chunk(speaker="meeting_output", text="핵심 논의를 이어갑니다.", audio_source="system", offset_seconds=50.0),
                transcript_chunk(speaker="meeting_output", text="짧은 맞장구입니다.", audio_source="system", offset_seconds=52.0),
                transcript_chunk(speaker="meeting_output", text="정리 계속합니다.", audio_source="system", offset_seconds=60.0),
                transcript_chunk(speaker="meeting_output", text="마무리합니다.", audio_source="system", offset_seconds=70.0),
            ]
        )

        self.assertEqual(pipeline._preferred_named_speakers(session), ("신대현", None))

    def test_speaker_display_name_uses_active_speaker_before_static_remote_alias(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="speaker-map-2",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Speaker Display Name",
            status="completed",
        )
        session.input_timeline.extend(
            [
                participant_snapshot("WooBIN_bot", 0.0),
                participant_snapshot("정우빈", 0.1),
                participant_snapshot("신대현", 0.2),
                participant_snapshot("정우빈", 10.0, event="active-speaker"),
                participant_snapshot("정우빈", 20.0, event="active-speaker"),
                participant_snapshot("신대현", 100.0, event="active-speaker"),
                participant_snapshot("신대현", 110.0, event="active-speaker"),
                participant_snapshot("정우빈", 200.0, event="active-speaker"),
            ]
        )
        session.transcript.extend(
            [
                transcript_chunk(speaker="local_user", text="로컬 화자입니다.", audio_source="microphone", offset_seconds=10.0),
                transcript_chunk(speaker="local_user", text="로컬 설명입니다.", audio_source="microphone", offset_seconds=20.0),
                transcript_chunk(speaker="meeting_output", text="원격 설명입니다.", audio_source="system", offset_seconds=100.0),
                transcript_chunk(speaker="meeting_output", text="원격 설명 이어집니다.", audio_source="system", offset_seconds=110.0),
            ]
        )

        self.assertEqual(pipeline._preferred_named_speakers(session), ("정우빈", "신대현"))
        display_name = pipeline._speaker_display_name(
            "meeting_output",
            {"audio_source": "system", "session_start_offset_seconds": 200.0},
            session=session,
        )
        self.assertEqual(display_name, "정우빈")

    def test_build_briefing_prefers_active_speaker_for_raised_by(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="speaker-map-3",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Raised By",
            status="completed",
        )
        session.input_timeline.extend(
            [
                participant_snapshot("WooBIN_bot", 0.0),
                participant_snapshot("정우빈", 0.1),
                participant_snapshot("신대현", 0.2),
                participant_snapshot("신대현", 228.0, event="active-speaker"),
                participant_snapshot("신대현", 242.0, event="active-speaker"),
                participant_snapshot("신대현", 252.0, event="active-speaker"),
                participant_snapshot("정우빈", 255.0, event="active-speaker"),
            ]
        )
        session.transcript.extend(
            [
                transcript_chunk(
                    speaker="meeting_output",
                    text="오늘 준비한 1차 데모 범위를 먼저 확인해보겠습니다.",
                    audio_source="system",
                    offset_seconds=230.38,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="현재 결과물을 먼저 보여드리고 이상이 없으면 다음 일정까지 연결하겠습니다.",
                    audio_source="system",
                    offset_seconds=243.28,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="이상 여부를 오늘 1차로 점검하는 방향입니다.",
                    audio_source="system",
                    offset_seconds=252.86,
                ),
            ]
        )

        briefing = pipeline.build_briefing(
            session,
            packet=pipeline.build(session),
            ai_result={
                "sections": [
                    {
                        "heading": "1차 데모 범위",
                        "summary": "오늘은 준비된 결과물을 보여주는 1차 데모 중심으로 진행한다.",
                        "timestamp_refs": ["03:50.38", "04:03.28", "04:12.86"],
                    }
                ]
            },
        )
        self.assertEqual(briefing["sections"][0]["raised_by"], "신대현")

    def test_enrich_sections_hides_minor_second_speaker_in_two_person_meeting(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="speaker-map-4",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Speaker Weighting",
            status="completed",
        )
        session.input_timeline.extend(
            [
                participant_snapshot("WooBIN_bot", 0.0),
                participant_snapshot("정우빈", 0.1),
                participant_snapshot("신대현", 0.2),
                participant_snapshot("신대현", 297.0, event="active-speaker"),
                participant_snapshot("정우빈", 329.0, event="active-speaker"),
                participant_snapshot("신대현", 331.0, event="active-speaker"),
                participant_snapshot("신대현", 351.0, event="active-speaker"),
                participant_snapshot("신대현", 359.0, event="active-speaker"),
                participant_snapshot("신대현", 364.0, event="active-speaker"),
            ]
        )
        session.transcript.extend(
            [
                transcript_chunk(
                    speaker="meeting_output",
                    text="AI하고 인간의 협업에 맞는 경영 철학이 ESG인 것 같아요.",
                    audio_source="system",
                    offset_seconds=297.84,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="그런데 ESG에 관련된 문서를 더 찾아봤는데 얼추 맞는 것 같아요.",
                    audio_source="system",
                    offset_seconds=329.40,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="E가 environment로 환경 맞죠?",
                    audio_source="system",
                    offset_seconds=350.92,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="환경 안의 효율 요소로 다시 설명하는 겁니다.",
                    audio_source="system",
                    offset_seconds=358.96,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="제가 헷갈려서 다시 확인하는 겁니다.",
                    audio_source="system",
                    offset_seconds=363.96,
                ),
            ]
        )

        enriched = pipeline._enrich_sections_for_display(
            session,
            [
                {
                    "heading": "ESG 해석 정리",
                    "summary": "AI와 인간의 협업을 ESG 관점에서 설명하려 했다.",
                    "timestamp_refs": ["04:57.84", "05:29.40", "05:50.92", "05:58.96", "06:03.96"],
                }
            ],
        )[0]
        self.assertEqual(enriched["raised_by"], "신대현")
        self.assertNotIn("speakers", enriched)


    def test_build_briefing_recovers_timestamp_refs_when_ai_leaves_section_refs_empty(self) -> None:
        pipeline = DelegateSummaryPipeline()
        session = DelegateSession(
            session_id="speaker-map-5",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Timestamp recovery",
            status="completed",
        )
        session.input_timeline.extend(
            [
                participant_snapshot("WooBIN_bot", 0.0),
                participant_snapshot("Alice", 0.1),
                participant_snapshot("Bob", 0.2),
                participant_snapshot("Alice", 90.0, event="active-speaker"),
                participant_snapshot("Bob", 94.0, event="active-speaker"),
                participant_snapshot("Bob", 101.0, event="active-speaker"),
            ]
        )
        session.transcript.extend(
            [
                transcript_chunk(
                    speaker="local_user",
                    text="We should expand ESG into AI infrastructure planning and governance work.",
                    audio_source="microphone",
                    offset_seconds=90.0,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="That means data centers, operations, and the governance review all need to be included.",
                    audio_source="system",
                    offset_seconds=94.0,
                ),
                transcript_chunk(
                    speaker="meeting_output",
                    text="The governance review also needs a clear owner before the next draft.",
                    audio_source="system",
                    offset_seconds=101.0,
                ),
            ]
        )

        briefing = pipeline.build_briefing(
            session,
            packet=pipeline.build(session),
            ai_result={
                "summary": "The meeting reframed ESG so it also covers AI infrastructure and governance work.",
                "sections": [
                    {
                        "heading": "AI infrastructure expansion",
                        "summary": "The discussion extended ESG into AI infrastructure, operations, and governance review ownership.",
                        "timestamp_refs": [],
                    }
                ],
            },
        )

        section = briefing["sections"][0]
        self.assertEqual(section["timestamp_refs"], ["01:30.00", "01:34.00", "01:41.00"])
        self.assertEqual(section["raised_by"], "Alice")
        self.assertCountEqual(section["speakers"], ["Alice", "Bob"])


if __name__ == "__main__":
    unittest.main()
