from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.models import DelegateSession
from local_meeting_ai_runtime.models import TranscriptChunk
from local_meeting_ai_runtime.service import DelegateService
from local_meeting_ai_runtime.storage import RunnerQueueStore, SessionStore


class _DummyAiClient:
    def quality_readiness(self) -> dict[str, object]:
        return {
            "blocking_reasons": [],
            "provider": "faster_whisper_cuda",
            "model": "large-v3",
            "gpu_ready": True,
            "faster_whisper_ready": True,
            "pyannote_ready": False,
            "diarization_provider": "",
        }

    def release_quality_runtime_resources(self) -> None:
        return

    def transcribe_audio_path_high_quality(self, **_: object) -> dict[str, object]:
        return {
            "chunks": [],
            "provider": "faster_whisper_cuda",
            "model": "large-v3",
            "compute_type": "float16",
            "diarization_provider": None,
            "quality_pass": "final_offline_archive",
            "dropped_segment_count": 0,
        }


class FullTrackCaptureMergeTest(unittest.TestCase):
    def _build_service(self, temp_dir: str) -> DelegateService:
        base = Path(temp_dir)
        store = SessionStore(path=str(base / "delegate_sessions.json"))
        runner_store = RunnerQueueStore(path=str(base / "runner_queue.json"))
        local_observer = mock.Mock()
        local_observer.audio_quality_readiness.return_value = {
            "blocking_reasons": [],
            "microphone_device_ready": True,
            "meeting_output_device_ready": True,
            "configured_meeting_output_device": "Test Speaker",
        }
        local_observer.meeting_output_device_name = "Test Speaker"
        return DelegateService(
            store=store,
            runner_store=runner_store,
            zoom_client=mock.Mock(),
            ai_client=_DummyAiClient(),
            meeting_adapter=mock.Mock(),
            local_observer=local_observer,
            summary_pipeline=mock.Mock(),
            artifact_exporter=mock.Mock(),
            export_dir=base / "exports",
        )

    def _write_wav(self, path: Path, *, seconds: float, sample_rate: int = 16000, channels: int = 1) -> None:
        frames = max(int(sample_rate * seconds), 1)
        audio = np.zeros((frames,), dtype="float32")
        if channels == 2:
            audio = np.column_stack([audio, audio]).astype("float32")
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), audio, sample_rate, format="WAV")

    def test_merge_audio_archives_prefers_full_track_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service(temp_dir)
            session = DelegateSession(
                session_id="full-track-prefers",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="active",
            )
            audio_dir = Path(temp_dir) / "data" / "audio" / session.session_id
            mic1 = audio_dir / "mic-full-1.wav"
            mic2 = audio_dir / "mic-full-2.wav"
            sys1 = audio_dir / "sys-full-1.wav"
            sys2 = audio_dir / "sys-full-2.wav"
            legacy_mic = audio_dir / "mic-legacy.wav"
            legacy_sys = audio_dir / "sys-legacy.wav"
            self._write_wav(mic1, seconds=1.0)
            self._write_wav(mic2, seconds=1.0)
            self._write_wav(sys1, seconds=1.0)
            self._write_wav(sys2, seconds=1.0)
            self._write_wav(legacy_mic, seconds=0.25)
            self._write_wav(legacy_sys, seconds=0.25)
            session.ai_state["full_track_capture_baseline"] = "2026-04-18T00:00:00+00:00"
            session.ai_state["full_track_archive_paths"] = [
                {
                    "path": str(mic1),
                    "audio_source": "microphone",
                    "capture_sequence": 1,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 1.0,
                    "seconds": 1.0,
                    "created_at": "2026-04-18T00:00:01+00:00",
                },
                {
                    "path": str(mic2),
                    "audio_source": "microphone",
                    "capture_sequence": 2,
                    "session_start_offset_seconds": 1.0,
                    "session_end_offset_seconds": 2.0,
                    "seconds": 1.0,
                    "created_at": "2026-04-18T00:00:02+00:00",
                },
                {
                    "path": str(sys1),
                    "audio_source": "system",
                    "capture_sequence": 3,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 1.0,
                    "seconds": 1.0,
                    "created_at": "2026-04-18T00:00:01+00:00",
                },
                {
                    "path": str(sys2),
                    "audio_source": "system",
                    "capture_sequence": 4,
                    "session_start_offset_seconds": 1.0,
                    "session_end_offset_seconds": 2.0,
                    "seconds": 1.0,
                    "created_at": "2026-04-18T00:00:02+00:00",
                },
            ]
            session.ai_state["audio_capture_baseline"] = "2026-04-18T00:10:00+00:00"
            session.ai_state["audio_archive_paths"] = [
                {
                    "path": str(legacy_mic),
                    "audio_source": "microphone",
                    "capture_sequence": 1,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 0.25,
                    "seconds": 0.25,
                    "created_at": "2026-04-18T00:10:01+00:00",
                },
                {
                    "path": str(legacy_sys),
                    "audio_source": "system",
                    "capture_sequence": 2,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 0.25,
                    "seconds": 0.25,
                    "created_at": "2026-04-18T00:10:01+00:00",
                },
            ]

            merged = service._merge_audio_archives_for_final_pass(session)

            self.assertEqual(merged["archive_strategy"], "rolling_full_track")
            self.assertEqual(merged["baseline_started_at"], "2026-04-18T00:00:00+00:00")
            mic_info = sf.info(str(Path(merged["microphone_path"])))
            sys_info = sf.info(str(Path(merged["meeting_output_path"])))
            self.assertAlmostEqual(mic_info.duration, 2.0, places=2)
            self.assertAlmostEqual(sys_info.duration, 2.0, places=2)

    def test_merge_audio_archives_falls_back_when_full_track_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service(temp_dir)
            session = DelegateSession(
                session_id="full-track-fallback",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="active",
            )
            audio_dir = Path(temp_dir) / "data" / "audio" / session.session_id
            full_mic = audio_dir / "mic-full-1.wav"
            legacy_mic = audio_dir / "mic-legacy.wav"
            legacy_sys = audio_dir / "sys-legacy.wav"
            self._write_wav(full_mic, seconds=2.0)
            self._write_wav(legacy_mic, seconds=1.0)
            self._write_wav(legacy_sys, seconds=1.0)
            session.ai_state["full_track_capture_baseline"] = "2026-04-18T00:00:00+00:00"
            session.ai_state["full_track_archive_paths"] = [
                {
                    "path": str(full_mic),
                    "audio_source": "microphone",
                    "capture_sequence": 1,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 2.0,
                    "seconds": 2.0,
                    "created_at": "2026-04-18T00:00:02+00:00",
                }
            ]
            session.ai_state["audio_capture_baseline"] = "2026-04-18T00:10:00+00:00"
            session.ai_state["audio_archive_paths"] = [
                {
                    "path": str(legacy_mic),
                    "audio_source": "microphone",
                    "capture_sequence": 1,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 1.0,
                    "seconds": 1.0,
                    "created_at": "2026-04-18T00:10:01+00:00",
                },
                {
                    "path": str(legacy_sys),
                    "audio_source": "system",
                    "capture_sequence": 2,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 1.0,
                    "seconds": 1.0,
                    "created_at": "2026-04-18T00:10:01+00:00",
                },
            ]

            merged = service._merge_audio_archives_for_final_pass(session)

            self.assertEqual(merged["archive_strategy"], "segmented_audio_archive")
            self.assertEqual(merged["baseline_started_at"], "2026-04-18T00:10:00+00:00")
            mic_info = sf.info(str(Path(merged["microphone_path"])))
            sys_info = sf.info(str(Path(merged["meeting_output_path"])))
            self.assertAlmostEqual(mic_info.duration, 1.0, places=2)
            self.assertAlmostEqual(sys_info.duration, 1.0, places=2)

    def test_merge_audio_archives_still_builds_merged_tracks_for_long_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service(temp_dir)
            service._final_merged_transcription_max_seconds = 60.0
            session = DelegateSession(
                session_id="full-track-long-session",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="active",
            )
            audio_dir = Path(temp_dir) / "data" / "audio" / session.session_id
            mic = audio_dir / "mic-full.wav"
            sys = audio_dir / "sys-full.wav"
            self._write_wav(mic, seconds=1.0)
            self._write_wav(sys, seconds=1.0)
            session.ai_state["full_track_capture_baseline"] = "2026-04-18T00:00:00+00:00"
            session.ai_state["full_track_capture"] = {"strategy": "rolling_full_track"}
            session.ai_state["full_track_archive_paths"] = [
                {
                    "path": str(mic),
                    "audio_source": "microphone",
                    "capture_sequence": 1,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 120.0,
                    "seconds": 120.0,
                    "created_at": "2026-04-18T00:02:00+00:00",
                },
                {
                    "path": str(sys),
                    "audio_source": "system",
                    "capture_sequence": 2,
                    "session_start_offset_seconds": 0.0,
                    "session_end_offset_seconds": 120.0,
                    "seconds": 120.0,
                    "created_at": "2026-04-18T00:02:00+00:00",
                },
            ]

            merged = service._merge_audio_archives_for_final_pass(session)

            self.assertTrue(bool(merged["microphone_path"]))
            self.assertTrue(bool(merged["meeting_output_path"]))
            self.assertEqual(merged["archive_duration_seconds"], 120.0)

    def test_archive_segment_transcription_splits_long_archive_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service(temp_dir)
            service._final_archive_transcription_chunk_seconds = 1.0
            service._ai.quality_readiness = mock.Mock(  # type: ignore[method-assign]
                return_value={
                    "blocking_reasons": [],
                    "provider": "faster_whisper_cuda",
                    "model": "large-v3",
                    "gpu_ready": True,
                    "faster_whisper_ready": True,
                    "pyannote_ready": True,
                    "diarization_provider": "pyannote/test",
                }
            )
            session = DelegateSession(
                session_id="archive-split-session",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="active",
            )
            audio_dir = Path(temp_dir) / "data" / "audio" / session.session_id
            system_path = audio_dir / "system-long.wav"
            self._write_wav(system_path, seconds=2.4, channels=2)
            calls: list[dict[str, object]] = []

            def _fake_transcribe_audio_path_high_quality(**kwargs: object) -> dict[str, object]:
                calls.append(dict(kwargs))
                base = float(kwargs.get("session_offset_base_seconds") or 0.0)
                return {
                    "chunks": [
                        TranscriptChunk(
                            speaker="remote_participant",
                            text=f"chunk-{len(calls)}",
                            source=str(kwargs.get("source") or "meeting_output_final_offline"),
                            metadata={
                                "session_start_offset_seconds": round(base, 3),
                                "session_end_offset_seconds": round(base + 0.4, 3),
                            },
                        )
                    ],
                    "provider": "faster_whisper_cuda",
                    "model": "large-v3",
                    "compute_type": "float16",
                    "diarization_provider": "pyannote/test" if kwargs.get("enable_diarization") else None,
                    "quality_pass": "final_offline_archive",
                    "dropped_segment_count": 0,
                }

            service._ai.transcribe_audio_path_high_quality = _fake_transcribe_audio_path_high_quality  # type: ignore[method-assign]

            result = service._transcribe_archived_audio_segments_for_final_pass(
                session,
                baseline_started_at="2026-04-18T00:00:00+00:00",
                archives=[
                    {
                        "path": str(system_path),
                        "audio_source": "system",
                        "capture_sequence": 1,
                        "session_start_offset_seconds": 10.0,
                        "session_end_offset_seconds": 12.4,
                        "seconds": 2.4,
                        "created_at": "2026-04-18T00:00:02+00:00",
                    }
                ],
            )

            self.assertGreaterEqual(len(calls), 3)
            self.assertEqual(
                [round(float(call.get("session_offset_base_seconds") or 0.0), 3) for call in calls],
                [10.0, 11.0, 12.0],
            )
            self.assertEqual(
                [bool(call.get("enable_diarization")) for call in calls],
                [True, True, False],
            )
            self.assertEqual(str(result.get("input_strategy") or ""), "archive_segments")
            self.assertGreaterEqual(len(list(result.get("chunks") or [])), 1)

    def test_latest_audio_archive_at_considers_full_track_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service(temp_dir)
            session = DelegateSession(
                session_id="latest-archive-full-track",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="active",
            )
            session.ai_state["full_track_archive_paths"] = [
                {
                    "path": str(Path(temp_dir) / "mic-full.wav"),
                    "audio_source": "microphone",
                    "created_at": "2026-04-19T01:23:45+00:00",
                }
            ]

            latest = service._latest_audio_archive_at(session)

            self.assertEqual(latest, "2026-04-19T01:23:45+00:00")


if __name__ == "__main__":
    unittest.main()
