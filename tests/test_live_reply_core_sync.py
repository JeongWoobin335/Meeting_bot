from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.ai_client import AiDelegateClient
from local_meeting_ai_runtime.models import DelegateSession
from local_meeting_ai_runtime.service import DelegateService
from local_meeting_ai_runtime.storage import RunnerQueueStore, SessionStore


class AiClientLiveReplyContractTest(unittest.TestCase):
    def test_respond_to_live_turn_uses_shared_live_core_prompt(self) -> None:
        client = AiDelegateClient()
        client._live_knowledge_mcp_server_config = None
        session = DelegateSession(
            session_id="live-reply-contract",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="Notion memory sync",
            status="active",
            requested_by="jung",
            instructions="Past meeting memory should stay available.",
        )
        captured: dict[str, str] = {}
        client._shared_tool_layer_payload = lambda: {  # type: ignore[method-assign]
            "codex_exec": {
                "available": True,
                "workdir": "C:/workspace",
                "reachable_mcp_routes": [
                    {"name": "notion-bot", "source": "codex_config", "enabled": True},
                    {"name": "nanobanana", "source": "codex_config", "enabled": True},
                ],
            },
            "runtime_direct_mcp_routes": [],
            "other_local_mcp_catalog": {"cursor_config_routes": []},
        }

        def fake_codex_json_response(_session, request_text, **_kwargs):
            captured["request_text"] = request_text
            return {
                "draft": "지난 회의 결정 사항을 찾아보고 정리해드릴게요.",
                "confidence_note": "meeting memory available",
                "grounding_summary": "summary packet + recent transcript",
                "tool_usage_summary": "notion_mcp lookup",
            }

        client._codex_json_response = fake_codex_json_response  # type: ignore[method-assign]

        result = asyncio.run(
            client.respond_to_live_turn(
                session,
                speaker="김대리",
                text="지난 회의에서 결정된 거 뭐였죠?",
                source="zoom_chat_message",
                direct_question=True,
                metadata={"id": "msg-1"},
            )
        )

        request_text = str(captured.get("request_text") or "")
        self.assertIn("You are not a Zoom-only mini bot.", request_text)
        self.assertIn("Use the same local Codex capabilities, configured tools, and connected MCP routes", request_text)
        self.assertIn("Tool layer payload:", request_text)
        self.assertIn('"name": "notion-bot"', request_text)
        self.assertIn('"name": "nanobanana"', request_text)
        self.assertIn('"speaker": "김대리"', request_text)
        self.assertIn('"source": "zoom_chat_message"', request_text)
        self.assertIn('"direct_question": true', request_text)
        self.assertEqual(result["response_mode"], "shared_live_core")
        self.assertEqual(result["tool_usage_summary"], "notion_mcp lookup")

    def test_respond_to_live_turn_uses_live_knowledge_mcp_answer_without_codex(self) -> None:
        client = AiDelegateClient()
        client._live_knowledge_mcp_enabled = True
        client._live_knowledge_mcp_server_config = {"command": "python", "args": [], "cwd": "C:/notion"}
        session = DelegateSession(
            session_id="live-reply-mcp",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_id="zoom-123",
            status="active",
        )

        async def fake_call(_session, *, speaker, text, metadata):
            self.assertEqual(speaker, "참석자")
            self.assertEqual(text, "이전 회의 결정사항이 뭐였죠?")
            self.assertEqual(metadata["id"], "msg-knowledge")
            return {
                "status": "ok",
                "should_reply": True,
                "reply_text": "이전 회의 결정사항은 4개입니다.",
                "evidence_cards": [{"knowledge_id": "decision-001", "summary": "결정 1"}],
                "judgment": {"reason": "저장된 지식이 필요한 질문입니다."},
            }

        client._call_live_knowledge_mcp_turn = fake_call  # type: ignore[method-assign]

        def fail_codex(*_args, **_kwargs):
            raise AssertionError("Codex should not be called when the MCP returns a final reply.")

        client._codex_json_response = fail_codex  # type: ignore[method-assign]

        with (
            mock.patch("local_meeting_ai_runtime.ai_client.ClientSession", object()),
            mock.patch("local_meeting_ai_runtime.ai_client.StdioServerParameters", object()),
            mock.patch("local_meeting_ai_runtime.ai_client.stdio_client", object()),
        ):
            result = asyncio.run(
                client.respond_to_live_turn(
                    session,
                    speaker="참석자",
                    text="이전 회의 결정사항이 뭐였죠?",
                    source="zoom_chat_message",
                    direct_question=False,
                    metadata={"id": "msg-knowledge"},
                )
            )

        self.assertTrue(result["should_reply"])
        self.assertEqual(result["draft"], "이전 회의 결정사항은 4개입니다.")
        self.assertEqual(result["provider"], "notion-bot-knowledge")
        self.assertEqual(result["response_mode"], "shared_live_core_mcp")
        self.assertEqual(result["trigger"], "semantic_tool_reply")

    def test_shared_tool_layer_payload_sanitizes_mcp_catalog(self) -> None:
        client = AiDelegateClient()
        client._configured_codex_mcp_routes = lambda: [  # type: ignore[method-assign]
            {
                "name": "metheus-governance-mcp",
                "source": "codex_config",
                "enabled": True,
                "command": "node",
                "args_preview": ["cli.mjs", "proxy"],
            }
        ]
        client._configured_cursor_mcp_routes = lambda: [  # type: ignore[method-assign]
            {
                "name": "memory",
                "source": "cursor_config",
                "enabled": True,
                "command": "npx",
                "args_preview": ["-y", "@modelcontextprotocol/server-memory"],
            }
        ]
        client._result_image_mcp_server_name = "nanobanana"
        client._result_image_direct_mcp = True
        client._result_image_mcp_server_config = {"command": "uvx"}

        payload = client._shared_tool_layer_payload()

        self.assertTrue(payload["codex_exec"]["available"])
        self.assertEqual(
            payload["codex_exec"]["reachable_mcp_routes"][0]["name"],
            "metheus-governance-mcp",
        )
        self.assertEqual(
            payload["other_local_mcp_catalog"]["cursor_config_routes"][0]["name"],
            "memory",
        )
        self.assertEqual(payload["runtime_direct_mcp_routes"][0]["name"], "nanobanana")


class _ReplyAiClient:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.response = response

    def quality_readiness(self) -> dict[str, object]:
        return {}

    def release_quality_runtime_resources(self) -> None:
        return

    async def respond_to_live_turn(
        self,
        session: DelegateSession,
        *,
        speaker: str,
        text: str,
        source: str,
        direct_question: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "session_id": session.session_id,
                "speaker": speaker,
                "text": text,
                "source": source,
                "direct_question": direct_question,
                "metadata": dict(metadata or {}),
            }
        )
        if self.response is not None:
            response = dict(self.response)
            response.setdefault("request_text", f"Current participant message from {speaker}: {text}")
            return response
        return {
            "request_text": f"Current participant message from {speaker}: {text}",
            "draft": "지난 회의 기준으로는 1안으로 정리됐습니다.",
            "confidence_note": "recent meeting memory available",
            "grounding_summary": "summary packet + live chat history",
            "tool_usage_summary": "same local MCP stack available",
            "provider": "codex_exec",
            "response_mode": "shared_live_core",
        }

    async def draft_reply(self, *_args, **_kwargs) -> dict[str, object]:
        raise AssertionError("draft_reply should not be used anymore.")


class ServiceLiveReplyCoreSyncTest(unittest.IsolatedAsyncioTestCase):
    def _build_service(self, temp_dir: str, ai_client: _ReplyAiClient) -> tuple[DelegateService, SessionStore]:
        base = Path(temp_dir)
        store = SessionStore(path=str(base / "delegate_sessions.json"))
        runner_store = RunnerQueueStore(path=str(base / "runner_queue.json"))
        artifact_exporter = mock.Mock()
        artifact_exporter.export_summary_bundle.return_value = []
        local_observer = mock.Mock()
        local_observer.audio_quality_readiness.return_value = {}
        local_observer.meeting_output_device_name = ""
        summary_pipeline = mock.Mock()
        summary_pipeline.build.return_value = {"briefing": {}, "meeting_intelligence": {}}
        summary_pipeline.build_briefing.return_value = {}
        summary_pipeline.render_summary_markdown.return_value = "# summary\n"
        summary_pipeline.render_transcript_markdown.return_value = "# transcript\n"
        summary_pipeline._display_title.return_value = "회의 요약"
        service = DelegateService(
            store=store,
            runner_store=runner_store,
            zoom_client=mock.Mock(),
            ai_client=ai_client,
            meeting_adapter=mock.Mock(),
            local_observer=local_observer,
            summary_pipeline=summary_pipeline,
            artifact_exporter=artifact_exporter,
            export_dir=base / "exports",
        )
        return service, store

    async def test_handle_chat_message_uses_shared_live_core_reply_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ai_client = _ReplyAiClient()
            service, store = self._build_service(temp_dir, ai_client)
            session = DelegateSession(
                session_id="live-reply-ready",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                meeting_topic="회의 메모리 확인",
                status="active",
            )
            store.save_session(session)

            refreshed, reply = await service.handle_chat_message(
                session.session_id,
                {
                    "role": "participant",
                    "speaker": "김대리",
                    "text": "@WooBIN_bot 지난 회의에서 결정된 거 뭐였죠?",
                    "source": "zoom_chat_message",
                    "direct_question": True,
                    "metadata": {"id": "msg-1"},
                },
            )

            self.assertEqual(len(ai_client.calls), 1)
            self.assertEqual(ai_client.calls[0]["speaker"], "김대리")
            self.assertEqual(ai_client.calls[0]["source"], "zoom_chat_message")
            self.assertTrue(bool(ai_client.calls[0]["direct_question"]))
            self.assertEqual(reply["status"], "ready_to_publish")
            self.assertEqual(reply["response_mode"], "shared_live_core")
            self.assertEqual(reply["tool_usage_summary"], "same local MCP stack available")
            self.assertEqual(refreshed.chat_history[-1].text, "@WooBIN_bot 지난 회의에서 결정된 거 뭐였죠?")

    async def test_non_direct_meeting_question_can_be_answered_by_shared_tool_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ai_client = _ReplyAiClient(
                response={
                    "request_text": "Current participant message from participant: previous decisions?",
                    "draft": "The previous meeting had four decisions.",
                    "confidence_note": "notion_bot found matching knowledge cards",
                    "grounding_summary": "notion_bot evidence: decision-001",
                    "tool_usage_summary": "notion-bot-knowledge.handle_zoom_ax_turn",
                    "provider": "notion-bot-knowledge",
                    "response_mode": "shared_live_core_mcp",
                    "trigger": "semantic_tool_reply",
                    "should_reply": True,
                }
            )
            service, store = self._build_service(temp_dir, ai_client)
            session = DelegateSession(
                session_id="live-reply-semantic",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                meeting_topic="Notion memory check",
                status="active",
            )
            store.save_session(session)

            _refreshed, reply = await service.handle_chat_message(
                session.session_id,
                {
                    "role": "participant",
                    "speaker": "participant",
                    "text": "What decisions did we make last time?",
                    "source": "zoom_chat_message",
                    "direct_question": False,
                    "metadata": {"id": "msg-semantic"},
                },
            )

            self.assertEqual(len(ai_client.calls), 1)
            self.assertFalse(bool(ai_client.calls[0]["direct_question"]))
            self.assertEqual(reply["status"], "ready_to_publish")
            self.assertEqual(reply["trigger"], "semantic_tool_reply")
            self.assertEqual(reply["provider"], "notion-bot-knowledge")
            self.assertEqual(reply["draft"], "The previous meeting had four decisions.")

    async def test_non_direct_meeting_chatter_is_ignored_when_tool_layer_declines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ai_client = _ReplyAiClient(
                response={
                    "request_text": "Current participant message from participant: okay",
                    "draft": "",
                    "confidence_note": "notion_bot ignored casual chatter",
                    "grounding_summary": "",
                    "tool_usage_summary": "notion-bot-knowledge.handle_zoom_ax_turn: no reply",
                    "provider": "notion-bot-knowledge",
                    "response_mode": "shared_live_core_mcp",
                    "should_reply": False,
                    "ignore_reason": "The live knowledge MCP decided not to answer this turn.",
                }
            )
            service, store = self._build_service(temp_dir, ai_client)
            session = DelegateSession(
                session_id="live-reply-semantic-ignore",
                delegate_mode="answer_on_ask",
                bot_display_name="WooBIN_bot",
                meeting_topic="Notion memory check",
                status="active",
            )
            store.save_session(session)

            _refreshed, reply = await service.handle_chat_message(
                session.session_id,
                {
                    "role": "participant",
                    "speaker": "participant",
                    "text": "Okay.",
                    "source": "zoom_chat_message",
                    "direct_question": False,
                    "metadata": {"id": "msg-ignore"},
                },
            )

            self.assertEqual(len(ai_client.calls), 1)
            self.assertEqual(reply["status"], "ignored")
            self.assertEqual(reply["provider"], "notion-bot-knowledge")
            self.assertIn("not to answer", reply["reason"])

    async def test_approval_required_reply_persists_shared_live_core_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ai_client = _ReplyAiClient()
            service, store = self._build_service(temp_dir, ai_client)
            session = DelegateSession(
                session_id="live-reply-approval",
                delegate_mode="approval_required",
                bot_display_name="WooBIN_bot",
                meeting_topic="회의 메모리 확인",
                status="active",
            )
            store.save_session(session)

            refreshed, reply = await service.handle_chat_message(
                session.session_id,
                {
                    "role": "participant",
                    "speaker": "김대리",
                    "text": "@WooBIN_bot 지난 회의에서 결정된 거 뭐였죠?",
                    "source": "zoom_chat_message",
                    "direct_question": True,
                    "metadata": {"id": "msg-2"},
                },
            )

            self.assertEqual(reply["status"], "pending_approval")
            self.assertEqual(len(refreshed.approvals), 1)
            self.assertEqual(len(refreshed.draft_replies), 1)
            self.assertEqual(refreshed.draft_replies[0]["response_mode"], "shared_live_core")
            self.assertEqual(refreshed.draft_replies[0]["grounding_summary"], "summary packet + live chat history")
            self.assertEqual(refreshed.draft_replies[0]["tool_usage_summary"], "same local MCP stack available")
            self.assertEqual(len(refreshed.workspace_events), 1)
            self.assertEqual(
                dict(refreshed.workspace_events[0].metadata).get("response_mode"),
                "shared_live_core",
            )


if __name__ == "__main__":
    unittest.main()
