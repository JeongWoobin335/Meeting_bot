from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.models import DelegateSession
from local_meeting_ai_runtime.service import DelegateService
from local_meeting_ai_runtime.storage import RunnerQueueStore, SessionStore


class _DummyAiClient:
    def release_quality_runtime_resources(self) -> None:
        return

    def quality_readiness(self) -> dict[str, object]:
        return {}


class SessionStoreArchitectureTest(unittest.TestCase):
    def test_save_session_migrates_to_per_session_file_and_overrides_legacy_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy_path = base / "delegate_sessions.json"
            session = DelegateSession(
                session_id="legacy-session",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="planned",
            )
            legacy_path.write_text(
                '{"legacy-session": ' + json.dumps(session.to_dict(), ensure_ascii=False) + "}",
                encoding="utf-8",
            )

            store = SessionStore(path=str(legacy_path))
            loaded = store.get_session("legacy-session")
            self.assertIsNotNone(loaded)
            assert loaded is not None

            loaded.status = "active"
            store.save_session(loaded)

            migrated_path = base / "delegate_sessions.d" / "legacy-session.json"
            self.assertTrue(migrated_path.exists())
            refreshed = store.get_session("legacy-session")
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertEqual(refreshed.status, "active")


class SessionFinalizationBoundaryTest(unittest.IsolatedAsyncioTestCase):
    def _build_service(self, temp_dir: str) -> tuple[DelegateService, SessionStore, RunnerQueueStore]:
        base = Path(temp_dir)
        store = SessionStore(path=str(base / "delegate_sessions.json"))
        runner_store = RunnerQueueStore(path=str(base / "runner_queue.json"))
        service = DelegateService(
            store=store,
            runner_store=runner_store,
            zoom_client=mock.Mock(),
            ai_client=_DummyAiClient(),
            meeting_adapter=mock.Mock(),
            local_observer=mock.Mock(),
            summary_pipeline=mock.Mock(),
            artifact_exporter=mock.Mock(),
            export_dir=base / "exports",
        )
        return service, store, runner_store

    async def test_duplicate_queued_completion_reuses_existing_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, store, runner_store = self._build_service(temp_dir)
            session = DelegateSession(
                session_id="queued-session",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="active",
            )
            store.save_session(session)

            first_session, first_completion = await service.request_session_completion(
                session.session_id,
                mode="queued",
                requested_by="test",
            )
            second_session, second_completion = await service.request_session_completion(
                session.session_id,
                mode="queued",
                requested_by="test",
            )

            jobs = runner_store.list_jobs(session_id=session.session_id)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(first_completion["job_id"], second_completion["job_id"])
            self.assertEqual(first_completion["status"], "queued")
            self.assertEqual(second_completion["status"], "queued")
            self.assertEqual(
                str(dict(second_session.ai_state.get("finalization") or {}).get("status") or "").strip().lower(),
                "queued",
            )
            self.assertEqual(first_session.session_id, second_session.session_id)

    async def test_live_updates_are_ignored_after_finalization_is_queued(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, store, _runner_store = self._build_service(temp_dir)
            session = DelegateSession(
                session_id="finalizing-session",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="active",
                ai_state={
                    "finalization": {
                        "status": "queued",
                        "mode": "queued",
                        "job_id": "job-123",
                    }
                },
            )
            store.save_session(session)

            await service.append_transcript(
                session.session_id,
                {
                    "speaker": "user",
                    "text": "이 문장은 finalize 이후라서 저장되면 안 됩니다.",
                },
            )
            refreshed = store.get_session(session.session_id)
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertEqual(len(refreshed.transcript), 0)

            _, ingest_result = await service.ingest_inputs(
                session.session_id,
                {
                    "inputs": [
                        {
                            "input_type": "spoken_transcript",
                            "speaker": "user",
                            "text": "late input",
                        }
                    ]
                },
            )
            self.assertEqual(ingest_result["processed_count"], 0)
            self.assertEqual(ingest_result["ignored_reason"], "finalization_in_progress")

    async def test_process_finalization_queue_completes_queued_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, store, runner_store = self._build_service(temp_dir)
            session = DelegateSession(
                session_id="finalizer-session",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                status="active",
            )
            store.save_session(session)

            await service.request_session_completion(
                session.session_id,
                mode="queued",
                requested_by="test",
            )

            async def fake_complete_session(session_id: str):
                completed = service.get_session(session_id)
                assert completed is not None
                completed.status = "completed"
                completed.summary = "summary ready"
                completed.summary_exports = [{"format": "pdf", "path": "dummy.pdf"}]
                return service.persist_session(completed)

            service.complete_session = fake_complete_session  # type: ignore[method-assign]

            result = await service.process_finalization_queue(limit=1, runner_id="test-finalizer")

            self.assertEqual(result["processed_count"], 1)
            self.assertEqual(result["failed_count"], 0)
            jobs = runner_store.list_jobs(session_id=session.session_id)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].status, "completed")
            refreshed = store.get_session(session.session_id)
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertEqual(refreshed.status, "completed")
            self.assertEqual(
                str(dict(refreshed.ai_state.get("finalization") or {}).get("status") or "").strip().lower(),
                "completed",
            )


if __name__ == "__main__":
    unittest.main()
