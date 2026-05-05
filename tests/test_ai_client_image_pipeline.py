from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai_runtime.ai_client import AiDelegateClient
from local_meeting_ai_runtime.models import DelegateSession


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0ioAAAAASUVORK5CYII="
)


class AiClientImagePipelineTest(unittest.TestCase):
    def _session(self) -> DelegateSession:
        session = DelegateSession(
            session_id="image-pipeline-1",
            delegate_mode="answer_on_ask",
            bot_display_name="WooBIN_bot",
            meeting_topic="AI governance framework",
            status="completed",
        )
        session.ai_state["meeting_output_skill"] = {
            "body": "# Visuals\n\n- Create structurally meaningful visuals tied to the meeting.\n",
            "metadata": {},
            "result_generation_policy": {},
        }
        return session

    def test_build_result_image_prompt_includes_structured_context(self) -> None:
        client = AiDelegateClient()
        session = self._session()

        prompt = client._build_result_image_prompt(
            session,
            title="AI governance framework",
            request={
                "title": "Governance structure visual",
                "instruction": "Show the governance structure in one image.",
                "prompt": "Create a clean responsibility and approval diagram.",
                "caption": "Governance structure",
                "target_heading": "AI Governance Structure",
                "placement_notes": "Place directly after the governance section",
                "agenda_context": "회의 전체는 책임 구조와 검토 게이트를 정리한다.",
                "block_focus": "이 블록은 책임 주체와 승인 순서를 묶는다.",
                "core_message": "AI 결과물은 명시적 책임과 검토 없이 운영되면 안 된다.",
                "visual_archetype": "responsibility_map",
                "visual_center": "책임 주체와 승인 게이트가 연결된 중심 구조",
                "key_entities": ["책임 주체", "검토 게이트", "승인 흐름"],
                "key_relationships": ["생성 후 검토", "검토 후 승인"],
                "must_include_labels": ["책임", "검토", "승인"],
                "avoid_elements": ["generic 사무실 장면", "무관한 도시 전경"],
                "composition_notes": "중심 구조가 먼저 보이게 배치한다.",
                "style_notes": "차분한 업무 문서 톤으로 정리한다.",
                "review_feedback": "이전 시도는 generic 장면처럼 보였다.",
            },
            rendering_policy={},
        )

        self.assertIn("회의 전체 맥락:", prompt)
        self.assertIn("이 블록의 역할:", prompt)
        self.assertIn("핵심 메시지:", prompt)
        self.assertIn("권장 시각 구조: responsibility_map", prompt)
        self.assertIn("핵심 엔티티:", prompt)
        self.assertIn("보여줘야 할 관계:", prompt)
        self.assertIn("피해야 할 요소:", prompt)
        self.assertIn("이전 시도에서 보완해야 할 점:", prompt)

    def test_materialize_result_generation_enriches_request_before_attachment(self) -> None:
        client = AiDelegateClient()
        session = self._session()
        captured_request: dict[str, object] = {}

        client._build_result_image_structure_plan = lambda *_args, **_kwargs: {
            "agenda_context": "회의 전체는 책임 구조와 승인 흐름을 정리한다.",
            "block_focus": "이 블록은 운영 게이트와 책임 주체를 묶는다.",
            "core_message": "승인 없는 생성물은 운영 단계로 넘어가면 안 된다.",
            "visual_archetype": "responsibility_map",
            "visual_center": "책임 주체와 승인 게이트가 연결된 구조",
            "key_entities": ["책임 주체", "검토", "승인"],
            "key_relationships": ["생성 후 검토", "검토 후 승인"],
            "must_include_labels": ["책임", "검토", "승인"],
            "avoid_elements": ["generic 장면"],
            "composition_notes": "중앙 구조를 먼저 보여준다.",
            "style_notes": "업무 문서형 도식으로 정리한다.",
        }

        async def fake_generate(_session, *, request, count, output_dir, **_kwargs):
            captured_request.update(dict(request))
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "visual-1.png"
            path.write_bytes(_PNG_1X1)
            self.assertEqual(count, 1)
            return [path]

        async def fake_review(_session, *, candidate_paths, **_kwargs):
            return (list(candidate_paths), [])

        client._generate_result_images = fake_generate
        client._review_result_image_candidates = fake_review

        with tempfile.TemporaryDirectory() as temp_dir:
            result = asyncio.run(
                client.materialize_result_generation(
                    session,
                    {
                        "title": "AI governance framework",
                        "executive_summary": "The meeting defined responsibility owners and approval gates.",
                        "sections": [
                            {
                                "heading": "AI Governance Structure",
                                "summary": "The session defined responsibility owners, approval gates, and escalation flow.",
                            }
                        ],
                        "postprocess_requests": [
                            {
                                "kind": "image_brief",
                                "title": "Governance structure visual",
                                "instruction": "Show the governance structure in one image.",
                                "prompt": "Create a clear responsibility and approval diagram.",
                                "tool_hint": "nano-banana-2",
                                "caption": "Governance structure",
                                "count": "1",
                                "placement_notes": "Place directly after the governance section",
                                "target_heading": "AI Governance Structure",
                            }
                        ],
                    },
                    output_dir=Path(temp_dir),
                )
            )

        self.assertEqual(captured_request.get("visual_archetype"), "responsibility_map")
        self.assertEqual(captured_request.get("agenda_context"), "회의 전체는 책임 구조와 승인 흐름을 정리한다.")
        self.assertEqual(captured_request.get("review_status"), "")
        self.assertEqual(len(result["postprocess_requests"]), 1)
        self.assertTrue(str(result["postprocess_requests"][0]["image_path"]).endswith(".png"))
        self.assertEqual(result["postprocess_requests"][0]["review_status"], "approved")
        timing = dict(session.ai_state.get("performance_timing") or {}).get("result_generation")
        self.assertIsInstance(timing, dict)
        assert isinstance(timing, dict)
        self.assertEqual(timing.get("status"), "completed")
        self.assertEqual(timing.get("image_request_count"), 1)
        self.assertEqual(len(list(timing.get("requests") or [])), 1)
        request_timing = dict(list(timing.get("requests") or [])[0])
        self.assertEqual(request_timing.get("status"), "approved")
        self.assertGreaterEqual(float(request_timing.get("preparation_seconds") or 0.0), 0.0)
        self.assertEqual(len(list(request_timing.get("attempts") or [])), 1)

    def test_materialize_result_generation_keeps_request_but_skips_rejected_image(self) -> None:
        client = AiDelegateClient()
        client._result_image_review_attempts = 1
        session = self._session()

        client._build_result_image_structure_plan = lambda *_args, **_kwargs: {
            "agenda_context": "회의 전체는 책임 구조와 승인 흐름을 정리한다.",
            "block_focus": "이 블록은 운영 게이트와 책임 주체를 묶는다.",
            "core_message": "승인 없는 생성물은 운영 단계로 넘어가면 안 된다.",
            "visual_archetype": "responsibility_map",
            "visual_center": "책임 주체와 승인 게이트가 연결된 구조",
            "key_entities": ["책임 주체", "검토", "승인"],
            "key_relationships": ["생성 후 검토", "검토 후 승인"],
            "must_include_labels": ["책임", "검토", "승인"],
            "avoid_elements": ["generic 장면"],
            "composition_notes": "중앙 구조를 먼저 보여준다.",
            "style_notes": "업무 문서형 도식으로 정리한다.",
        }

        async def fake_generate(_session, *, output_dir, **_kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "visual-1.png"
            path.write_bytes(_PNG_1X1)
            return [path]

        async def fake_review(_session, *, candidate_paths, **_kwargs):
            return (
                [],
                [
                    {
                        "path": str(candidate_paths[0]),
                        "decision": "reject",
                        "note": "generic 장면이라 의미 연결이 약합니다.",
                        "revision_hint": "장면 대신 책임 관계 구조를 선명하게 보여주세요.",
                    }
                ],
            )

        client._generate_result_images = fake_generate
        client._review_result_image_candidates = fake_review

        with tempfile.TemporaryDirectory() as temp_dir:
            result = asyncio.run(
                client.materialize_result_generation(
                    session,
                    {
                        "title": "AI governance framework",
                        "executive_summary": "The meeting defined responsibility owners and approval gates.",
                        "sections": [
                            {
                                "heading": "AI Governance Structure",
                                "summary": "The session defined responsibility owners, approval gates, and escalation flow.",
                            }
                        ],
                        "postprocess_requests": [
                            {
                                "kind": "image_brief",
                                "title": "Governance structure visual",
                                "instruction": "Show the governance structure in one image.",
                                "prompt": "Create a clear responsibility and approval diagram.",
                                "tool_hint": "nano-banana-2",
                                "caption": "Governance structure",
                                "count": "1",
                                "placement_notes": "Place directly after the governance section",
                                "target_heading": "AI Governance Structure",
                            }
                        ],
                    },
                    output_dir=Path(temp_dir),
                )
            )

        self.assertEqual(len(result["postprocess_requests"]), 1)
        self.assertEqual(result["postprocess_requests"][0]["image_path"], "")
        self.assertEqual(result["postprocess_requests"][0]["review_status"], "rejected")
        self.assertIn("책임 관계 구조", str(result["postprocess_requests"][0]["review_note"]))
        self.assertIn("result_generation_errors", session.ai_state)
        timing = dict(session.ai_state.get("performance_timing") or {}).get("result_generation")
        self.assertIsInstance(timing, dict)
        assert isinstance(timing, dict)
        self.assertEqual(timing.get("status"), "completed")
        request_timing = dict(list(timing.get("requests") or [])[0])
        self.assertEqual(request_timing.get("status"), "rejected")
        self.assertEqual(request_timing.get("approved_count"), 0)
        self.assertIn("책임 관계 구조", str(request_timing.get("review_note") or ""))

    def test_materialize_result_generation_runs_two_image_requests_concurrently_and_keeps_result_order(self) -> None:
        client = AiDelegateClient()
        client._result_image_review_attempts = 1
        session = self._session()

        client._build_result_image_structure_plan = lambda *_args, **_kwargs: {
            "agenda_context": "회의 전체는 책임 구조와 승인 흐름을 정리한다.",
            "block_focus": "이 블록은 운영 게이트와 책임 주체를 묶는다.",
            "core_message": "승인 없는 생성물은 운영 단계로 넘어가면 안 된다.",
            "visual_archetype": "responsibility_map",
            "visual_center": "책임 주체와 승인 게이트가 연결된 구조",
            "key_entities": ["책임 주체", "검토", "승인"],
            "key_relationships": ["생성 후 검토", "검토 후 승인"],
            "must_include_labels": ["책임", "검토", "승인"],
            "avoid_elements": ["generic 장면"],
            "composition_notes": "중앙 구조를 먼저 보여준다.",
            "style_notes": "업무 문서형 도식으로 정리한다.",
        }

        active_generations = 0
        max_active_generations = 0
        both_started = asyncio.Event()
        release_first_batch = asyncio.Event()

        async def fake_generate(_session, *, request, output_dir, **_kwargs):
            nonlocal active_generations, max_active_generations
            title = str(request.get("title") or "").strip()
            active_generations += 1
            max_active_generations = max(max_active_generations, active_generations)
            if active_generations == 2:
                both_started.set()
            if title in {"Visual 1", "Visual 2"}:
                await both_started.wait()
                await release_first_batch.wait()
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / f"{title.replace(' ', '-').lower()}.png"
            path.write_bytes(_PNG_1X1)
            if title == "Visual 1":
                await asyncio.sleep(0.05)
            active_generations -= 1
            return [path]

        async def fake_review(_session, *, candidate_paths, **_kwargs):
            return (list(candidate_paths), [])

        client._generate_result_images = fake_generate
        client._review_result_image_candidates = fake_review

        async def run_pipeline() -> dict[str, object]:
            with tempfile.TemporaryDirectory() as temp_dir:
                task = asyncio.create_task(
                    client.materialize_result_generation(
                        session,
                        {
                            "title": "AI governance framework",
                            "executive_summary": "The meeting defined responsibility owners and approval gates.",
                            "sections": [
                                {"heading": "Section 1", "summary": "Summary 1"},
                                {"heading": "Section 2", "summary": "Summary 2"},
                                {"heading": "Section 3", "summary": "Summary 3"},
                            ],
                            "postprocess_requests": [
                                {
                                    "kind": "image_brief",
                                    "title": "Visual 1",
                                    "instruction": "Show the first governance structure.",
                                    "prompt": "Create the first diagram.",
                                    "tool_hint": "nano-banana-2",
                                    "caption": "Visual 1",
                                    "count": "1",
                                    "placement_notes": "Place after section 1",
                                    "target_heading": "Section 1",
                                },
                                {
                                    "kind": "image_brief",
                                    "title": "Visual 2",
                                    "instruction": "Show the second governance structure.",
                                    "prompt": "Create the second diagram.",
                                    "tool_hint": "nano-banana-2",
                                    "caption": "Visual 2",
                                    "count": "1",
                                    "placement_notes": "Place after section 2",
                                    "target_heading": "Section 2",
                                },
                                {
                                    "kind": "image_brief",
                                    "title": "Visual 3",
                                    "instruction": "Show the third governance structure.",
                                    "prompt": "Create the third diagram.",
                                    "tool_hint": "nano-banana-2",
                                    "caption": "Visual 3",
                                    "count": "1",
                                    "placement_notes": "Place after section 3",
                                    "target_heading": "Section 3",
                                },
                            ],
                        },
                        output_dir=Path(temp_dir),
                    )
                )
                await asyncio.wait_for(both_started.wait(), timeout=1.0)
                release_first_batch.set()
                return await task

        result = asyncio.run(run_pipeline())

        self.assertEqual(max_active_generations, 2)
        self.assertEqual(
            [str(item.get("title") or "") for item in result["postprocess_requests"]],
            ["Visual 1", "Visual 2", "Visual 3"],
        )
        timing = dict(session.ai_state.get("performance_timing") or {}).get("result_generation")
        self.assertIsInstance(timing, dict)
        assert isinstance(timing, dict)
        self.assertEqual(len(list(timing.get("requests") or [])), 3)
        self.assertEqual(
            [str(item.get("title") or "") for item in list(timing.get("requests") or [])],
            ["Visual 1", "Visual 2", "Visual 3"],
        )

    def test_build_codex_command_keeps_schema_on_resume(self) -> None:
        client = AiDelegateClient()
        client._codex_path = "codex"
        client._codex_resume_supports_output_schema = True

        command = client._build_codex_command(
            output_path=Path("C:/tmp/output.json"),
            thread_id="thread-123",
            schema_path=Path("C:/tmp/schema.json"),
        )

        self.assertIn("--output-schema", command)
        self.assertIn(str(Path("C:/tmp/schema.json")), command)

    def test_normalize_codex_schema_adds_additional_properties_false_recursively(self) -> None:
        client = AiDelegateClient()

        normalized = client._normalize_codex_schema(
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                    },
                    "meta": {
                        "properties": {
                            "ok": {"type": "boolean"},
                        }
                    },
                },
            }
        )

        self.assertFalse(normalized["additionalProperties"])
        self.assertEqual(normalized["required"], ["items", "meta"])
        self.assertFalse(normalized["properties"]["items"]["items"]["additionalProperties"])
        self.assertEqual(
            normalized["properties"]["items"]["items"]["required"],
            ["title"],
        )
        self.assertFalse(normalized["properties"]["meta"]["additionalProperties"])
        self.assertEqual(normalized["properties"]["meta"]["required"], ["ok"])

    def test_extract_codex_error_message_prefers_structured_stdout_error(self) -> None:
        client = AiDelegateClient()
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps(
                    {
                        "type": "error",
                        "message": json.dumps(
                            {
                                "type": "error",
                                "error": {
                                    "type": "invalid_request_error",
                                    "code": "invalid_json_schema",
                                    "message": "Nested objects must set additionalProperties=false.",
                                },
                            }
                        ),
                    }
                ),
            ]
        )

        message = client._extract_codex_error_message(stdout, "very noisy stderr")

        self.assertEqual(
            message,
            "invalid_json_schema: Nested objects must set additionalProperties=false.",
        )

    def test_generate_result_images_prefers_direct_mcp_route(self) -> None:
        client = AiDelegateClient()
        client._result_image_direct_mcp = True
        client._result_image_mcp_server_config = {
            "command": "uvx",
            "args": ["nanobanana-mcp-server@latest"],
            "env": {},
            "cwd": str(Path.cwd()),
            "source": "test",
        }
        called: dict[str, object] = {}

        async def fake_direct(_session, *, count, output_dir, **_kwargs):
            path = output_dir / "visual-1.png"
            called["count"] = count
            called["path"] = path
            return [path]

        client._generate_result_images_with_nanobanana_mcp = fake_direct
        client._generate_result_images_with_codex = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy codex image route should not be used")
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = asyncio.run(
                client._generate_result_images(
                    self._session(),
                    title="AI governance framework",
                    request={"title": "Visual", "tool_hint": "nano-banana-2"},
                    count=1,
                    output_dir=Path(temp_dir),
                    rendering_policy={},
                )
            )

        self.assertEqual(called["count"], 1)
        self.assertEqual(len(result), 1)
        self.assertTrue(str(result[0]).endswith(".png"))

    def test_generate_result_images_serializes_direct_mcp_calls_by_default(self) -> None:
        client = AiDelegateClient()
        client._result_image_direct_mcp = True
        client._result_image_mcp_server_config = {
            "command": "uvx",
            "args": ["nanobanana-mcp-server@latest"],
            "env": {},
            "cwd": str(Path.cwd()),
            "source": "test",
        }
        client._result_image_mcp_max_concurrency = 1
        client._result_image_mcp_concurrency_loop = None
        client._result_image_mcp_concurrency_semaphore = None

        active_direct_calls = 0
        max_active_direct_calls = 0

        async def fake_direct(_session, *, request, output_dir, **_kwargs):
            nonlocal active_direct_calls, max_active_direct_calls
            active_direct_calls += 1
            max_active_direct_calls = max(max_active_direct_calls, active_direct_calls)
            await asyncio.sleep(0.05)
            active_direct_calls -= 1
            return [output_dir / f"{str(request.get('title') or 'visual').strip()}.png"]

        client._generate_result_images_with_nanobanana_mcp = fake_direct

        async def run_pair() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                output_dir = Path(temp_dir)
                await asyncio.gather(
                    client._generate_result_images(
                        self._session(),
                        title="AI governance framework",
                        request={"title": "Visual 1", "tool_hint": "nano-banana-2"},
                        count=1,
                        output_dir=output_dir,
                        rendering_policy={},
                    ),
                    client._generate_result_images(
                        self._session(),
                        title="AI governance framework",
                        request={"title": "Visual 2", "tool_hint": "nano-banana-2"},
                        count=1,
                        output_dir=output_dir,
                        rendering_policy={},
                    ),
                )

        asyncio.run(run_pair())

        self.assertEqual(max_active_direct_calls, 1)

    def test_generate_result_images_requires_nanobanana_mcp_when_direct_enabled(self) -> None:
        client = AiDelegateClient()
        client._result_image_direct_mcp = True
        client._result_image_mcp_server_config = None
        client._codex_path = "codex"

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(RuntimeError) as exc_info:
                asyncio.run(
                    client._generate_result_images(
                        self._session(),
                        title="AI governance framework",
                        request={"title": "Visual", "tool_hint": "nano-banana-2"},
                        count=1,
                        output_dir=Path(temp_dir),
                        rendering_policy={},
                    )
                )

        self.assertIn("nanobanana MCP", str(exc_info.exception))

    def test_result_image_mcp_env_override_is_parsed(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "DELEGATE_RESULT_IMAGE_MCP_COMMAND": "uvx",
                "DELEGATE_RESULT_IMAGE_MCP_ARGS": '["nanobanana-mcp-server@latest"]',
                "DELEGATE_RESULT_IMAGE_MCP_ENV": '{"TEST_FLAG":"1"}',
                "DELEGATE_RESULT_IMAGE_MCP_CWD": str(Path.cwd()),
            },
            clear=False,
        ):
            client = AiDelegateClient()

        self.assertIsNotNone(client._result_image_mcp_server_config)
        self.assertEqual(client._result_image_mcp_server_config["command"], "uvx")
        self.assertEqual(
            client._result_image_mcp_server_config["args"],
            ["nanobanana-mcp-server@latest"],
        )
        self.assertEqual(client._result_image_mcp_server_config["env"]["TEST_FLAG"], "1")


if __name__ == "__main__":
    unittest.main()
