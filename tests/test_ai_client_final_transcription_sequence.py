from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.ai_client import AiDelegateClient


class FinalTranscriptionSequenceTest(unittest.TestCase):
    def test_final_transcription_releases_gpu_between_whisper_and_diarization(self) -> None:
        client = AiDelegateClient()
        release_calls: list[tuple[bool, bool]] = []
        transcribe_calls: list[str] = []

        with (
            mock.patch.object(
                client,
                "quality_readiness",
                return_value={
                    "blocking_reasons": [],
                    "provider": "faster_whisper_cuda",
                    "compute_types": ["float16"],
                },
            ),
            mock.patch.object(
                client,
                "_transcribe_final_channel_with_faster_whisper",
                side_effect=lambda input_path, **kwargs: (
                    transcribe_calls.append(str(kwargs.get("channel_origin") or "")) or {
                        "chunks": [],
                        "dropped_segment_count": 0,
                        "compute_type": "float16",
                    }
                ),
            ),
            mock.patch.object(
                client,
                "_run_pyannote_diarization",
                return_value=[],
            ),
            mock.patch.object(
                client,
                "_release_quality_runtime_phase",
                side_effect=lambda *, release_faster_whisper=False, release_pyannote=False: release_calls.append(
                    (bool(release_faster_whisper), bool(release_pyannote))
                ),
            ),
        ):
            client.transcribe_final_session_audio(
                microphone_path=Path("mic.wav"),
                meeting_output_path=Path("sys.wav"),
            )

        self.assertEqual(transcribe_calls, ["local_user", "meeting_output"])
        self.assertEqual(
            release_calls,
            [
                (True, False),
                (False, True),
                (True, False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
