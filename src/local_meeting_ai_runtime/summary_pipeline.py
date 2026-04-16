"""Session summarization helpers for meeting delegate sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from .models import DelegateSession

KST = timezone(timedelta(hours=9), name="KST")


class DelegateSummaryPipeline:
    def __init__(self) -> None:
        self._participant_state_labels_cache: dict[tuple[str, str], list[str]] = {}
        self._zoom_active_speaker_events_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._interaction_records_cache: dict[tuple[str, str], list[dict[str, str]]] = {}
        self._preferred_named_speakers_cache: dict[tuple[str, str], tuple[str | None, str | None]] = {}

    def build(self, session: DelegateSession) -> dict[str, Any]:
        transcript_participants: set[str] = set()
        unresolved_local_speaker = False
        unresolved_remote_speaker = False
        action_candidates: list[str] = []
        decision_candidates: list[str] = []
        open_questions: list[str] = []
        risk_signals: list[str] = []
        spoken_preview: list[str] = []
        chat_preview: list[str] = []
        interaction_timeline: list[str] = []
        source_breakdown: dict[str, int] = {}

        for chunk in session.transcript:
            raw_speaker = self._normalize(getattr(chunk, "speaker", "") or "")
            speaker = self._speaker_display_name(
                chunk.speaker,
                getattr(chunk, "metadata", {}),
                session=session,
                created_at=getattr(chunk, "created_at", None),
            )
            text = self._normalize(chunk.text)
            if not text:
                continue
            if self._is_local_placeholder_label(raw_speaker):
                unresolved_local_speaker = True
            if self._is_remote_placeholder_label(raw_speaker):
                unresolved_remote_speaker = True
            if self._is_local_placeholder_label(speaker):
                unresolved_local_speaker = True
            if self._is_remote_placeholder_label(speaker):
                unresolved_remote_speaker = True
            if not self._is_placeholder_participant_label(speaker):
                transcript_participants.add(speaker)
            source = self._classify_source(chunk.source)
            source_breakdown[source] = source_breakdown.get(source, 0) + 1
            line = f"{self._time_label_from_chunk(chunk)}{speaker}: {text}"
            interaction_timeline.append(f"[spoken] {line}")
            if source == "spoken_transcript":
                spoken_preview.append(line)
            else:
                chat_preview.append(line)
            if self._should_collect_transcript_intelligence(session, chunk, text):
                self._collect_meeting_intelligence(
                    text=text,
                    action_candidates=action_candidates,
                    decision_candidates=decision_candidates,
                    open_questions=open_questions,
                    risk_signals=risk_signals,
                )

        for turn in session.chat_history:
            speaker = self._speaker_display_name(
                turn.speaker or turn.role or "participant",
                session=session,
                created_at=getattr(turn, "created_at", None),
            )
            text = self._normalize(turn.text)
            if not text:
                continue
            if not self._is_placeholder_participant_label(speaker):
                transcript_participants.add(speaker)
            line = f"{self._time_label(turn.created_at)}{speaker}: {text}"
            interaction_timeline.append(f"[chat] {line}")
            if line not in chat_preview:
                chat_preview.append(line)
            if turn.role != "bot" and len(text) <= 280:
                self._collect_meeting_intelligence(
                    text=text,
                    action_candidates=action_candidates,
                    decision_candidates=decision_candidates,
                    open_questions=open_questions,
                    risk_signals=risk_signals,
                )

        participants = self._session_participants(
            session,
            transcript_participants=transcript_participants,
            unresolved_local_speaker=unresolved_local_speaker,
            unresolved_remote_speaker=unresolved_remote_speaker,
        )
        packet = {
            "meeting": {
                "session_id": session.session_id,
                "meeting_id": session.meeting_id,
                "meeting_uuid": session.meeting_uuid,
                "meeting_number": session.meeting_number,
                "meeting_topic": session.meeting_topic,
                "delegate_mode": session.delegate_mode,
                "status": session.status,
            },
            "participants": participants,
            "counts": {
                "input_events": len(session.input_timeline),
                "transcript_lines": len(session.transcript),
                "chat_turns": len(session.chat_history),
                "workspace_event_count": len(session.workspace_events),
            },
            "source_breakdown": source_breakdown,
            "meeting_intelligence": {
                "decisions": decision_candidates[:10],
                "open_questions": open_questions[:10],
                "risk_signals": risk_signals[:10],
            },
            "spoken_transcript_preview": spoken_preview[-10:],
            "chat_preview": chat_preview[-10:],
            "interaction_timeline_preview": interaction_timeline[-14:],
            "action_candidates": action_candidates[:10],
        }
        packet["briefing"] = self.build_briefing(session, packet=packet)
        return packet

    def build_briefing(
        self,
        session: DelegateSession,
        *,
        packet: dict[str, Any] | None = None,
        ai_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result_generation_policy = self._result_generation_policy(session)
        packet = dict(packet or {})
        ai_result = dict(ai_result or {})
        intelligence = dict(packet.get("meeting_intelligence") or {})
        participants = self._clean_list(packet.get("participants"))
        action_items = self._clean_list(session.action_items or ai_result.get("action_items") or packet.get("action_candidates"))
        decisions = self._clean_list(ai_result.get("decisions") or intelligence.get("decisions"))
        open_questions = self._clean_list(ai_result.get("open_questions") or intelligence.get("open_questions"))
        risk_signals = self._clean_list(ai_result.get("risk_signals") or intelligence.get("risk_signals"))
        postprocess_requests = self._clean_postprocess_requests(ai_result.get("postprocess_requests"))
        executive_summary = (
            self._normalize(str(ai_result.get("executive_summary") or ""))
            or self._normalize(str(ai_result.get("summary") or ""))
            or self._normalize(str(session.summary or ""))
            or self._fallback_summary(session)
        )
        sections = self._sections_from_ai(session, ai_result)
        if not sections:
            sections = self._fallback_sections(
                session,
                executive_summary=executive_summary,
                decisions=decisions,
                action_items=action_items,
                open_questions=open_questions,
                risk_signals=risk_signals,
            )
        sections = self._enrich_sections_for_display(session, sections)
        design_intent_packet = dict(
            dict(session.ai_state.get("meeting_output_skill") or {}).get("design_intent_packet") or {}
        )
        return {
            "title": self._resolved_title(session, ai_result=ai_result, sections=sections, executive_summary=executive_summary),
            "meeting_datetime_label": self._meeting_datetime_label(session),
            "overview_author": str(session.bot_display_name or "").strip(),
            "overview_session_id": str(session.session_id or "").strip(),
            "executive_summary": executive_summary or "회의 전체 요약이 아직 생성되지 않았습니다.",
            "sections": self._limit_items(sections, result_generation_policy.get("max_display_sections")),
            "decisions": self._limit_items(decisions, result_generation_policy.get("max_decisions")),
            "action_items": self._limit_items(action_items, result_generation_policy.get("max_action_items")),
            "open_questions": self._limit_items(open_questions, result_generation_policy.get("max_open_questions")),
            "participants": participants[:20],
            "risk_signals": self._limit_items(risk_signals, result_generation_policy.get("max_risk_signals")),
            "postprocess_requests": self._limit_items(
                postprocess_requests,
                result_generation_policy.get("max_postprocess_requests"),
            ),
            "renderer_profile": "default",
            "design_intent_packet": design_intent_packet,
            "rendering_policy": dict(result_generation_policy),
        }

    def render_summary_markdown(self, session: DelegateSession) -> str:
        packet = dict(session.summary_packet or {})
        fresh_packet: dict[str, Any] | None = None
        if not packet:
            packet = self.build(session)
        elif self._participants_need_refresh(packet):
            fresh_packet = self.build(session)
            packet["participants"] = list(fresh_packet.get("participants") or [])
        briefing = dict(packet.get("briefing") or self.build_briefing(session, packet=packet))
        if self._participants_need_refresh({"participants": briefing.get("participants")}):
            if fresh_packet is None:
                fresh_packet = self.build(session)
            briefing["participants"] = list((fresh_packet or {}).get("participants") or [])
        title = self._display_title(session, briefing)
        meeting_datetime = str(briefing.get("meeting_datetime_label") or self._meeting_datetime_label(session)).strip()
        executive_summary = str(briefing.get("executive_summary") or session.summary or "").strip()
        result_generation_policy = self._result_generation_policy(session)
        sections = self._enrich_sections_for_display(session, list(briefing.get("sections") or []))
        sections = self._limit_items(sections, result_generation_policy.get("max_display_sections"))
        decisions = self._limit_items(
            self._clean_list(briefing.get("decisions")),
            result_generation_policy.get("max_decisions"),
        )
        action_items = self._limit_items(
            self._clean_list(briefing.get("action_items")),
            result_generation_policy.get("max_action_items"),
        )
        open_questions = self._limit_items(
            self._clean_list(briefing.get("open_questions")),
            result_generation_policy.get("max_open_questions"),
        )
        risk_signals = self._limit_items(
            self._clean_list(briefing.get("risk_signals")),
            result_generation_policy.get("max_risk_signals"),
        )
        postprocess_requests = self._limit_items(
            self._clean_postprocess_requests(briefing.get("postprocess_requests")),
            result_generation_policy.get("max_postprocess_requests"),
        )
        participants = self._clean_list(briefing.get("participants"))
        display_participants = self._overview_participants(session, participants)
        participant_text = ", ".join(participants) if participants else "미확인"

        participant_text = ", ".join(display_participants)

        lines: list[str] = []
        if str(result_generation_policy.get("show_title") or "always").strip().lower() != "never":
            lines.extend([f"# {title}", ""])
        lines.extend(
            self._render_result_blocks(
                session,
                meeting_datetime=meeting_datetime,
                participant_text=participant_text,
                executive_summary=executive_summary,
                sections=sections,
                decisions=decisions,
                action_items=action_items,
                open_questions=open_questions,
                risk_signals=risk_signals,
                postprocess_requests=postprocess_requests,
                result_generation_policy=result_generation_policy,
            )
        )
        return "\n".join(lines).strip() + "\n"

    def _render_result_blocks(
        self,
        session: DelegateSession,
        *,
        meeting_datetime: str,
        participant_text: str,
        executive_summary: str,
        sections: list[dict[str, Any]],
        decisions: list[str],
        action_items: list[str],
        open_questions: list[str],
        risk_signals: list[str],
        postprocess_requests: list[dict[str, str]],
        result_generation_policy: dict[str, Any],
    ) -> list[str]:
        order = [
            str(item).strip()
            for item in list(result_generation_policy.get("result_block_order") or [])
            if str(item).strip()
        ]
        if not order:
            order = ["overview", "executive_summary", "sections", "decisions", "action_items", "open_questions", "postprocess_requests", "memo"]
        if str(result_generation_policy.get("result_block_order_mode") or "append_missing").strip().lower() != "exact":
            if "overview" not in order:
                order.insert(0, "overview")
            if "executive_summary" not in order:
                insert_at = order.index("overview") + 1 if "overview" in order else 0
                order.insert(insert_at, "executive_summary")
        lines: list[str] = []
        rendered: set[str] = set()
        for block_name in order:
            lines.extend(
                self._render_one_result_block(
                    session,
                    block_name=block_name,
                    meeting_datetime=meeting_datetime,
                    participant_text=participant_text,
                    executive_summary=executive_summary,
                    sections=sections,
                    decisions=decisions,
                    action_items=action_items,
                    open_questions=open_questions,
                    risk_signals=risk_signals,
                    postprocess_requests=postprocess_requests,
                    result_generation_policy=result_generation_policy,
                )
            )
            rendered.add(block_name)
        if str(result_generation_policy.get("result_block_order_mode") or "append_missing").strip().lower() == "exact":
            return lines
        for block_name in ("overview", "executive_summary", "sections", "decisions", "action_items", "open_questions", "risk_signals", "postprocess_requests", "memo"):
            if block_name in rendered:
                continue
            lines.extend(
                self._render_one_result_block(
                    session,
                    block_name=block_name,
                    meeting_datetime=meeting_datetime,
                    participant_text=participant_text,
                    executive_summary=executive_summary,
                    sections=sections,
                    decisions=decisions,
                    action_items=action_items,
                    open_questions=open_questions,
                    risk_signals=risk_signals,
                    postprocess_requests=postprocess_requests,
                    result_generation_policy=result_generation_policy,
                )
            )
        return lines

    def _render_one_result_block(
        self,
        session: DelegateSession,
        *,
        block_name: str,
        meeting_datetime: str,
        participant_text: str,
        executive_summary: str,
        sections: list[dict[str, Any]],
        decisions: list[str],
        action_items: list[str],
        open_questions: list[str],
        risk_signals: list[str],
        postprocess_requests: list[dict[str, str]],
        result_generation_policy: dict[str, Any],
    ) -> list[str]:
        block = str(block_name or "").strip()
        if block == "overview":
            visibility = str(result_generation_policy.get("show_overview") or "always").strip().lower()
            if visibility == "never":
                return []
            return self._render_overview_block(
                session,
                meeting_datetime=meeting_datetime,
                participant_text=participant_text,
                result_generation_policy=result_generation_policy,
            )
        if block == "executive_summary":
            visibility = str(result_generation_policy.get("show_executive_summary") or "always").strip().lower()
            if visibility == "never":
                return []
            if visibility == "auto" and not executive_summary:
                return []
            return [
                "",
                f"## {result_generation_policy['executive_summary_heading']}",
                "",
                executive_summary or str(result_generation_policy["empty_executive_summary_message"]),
                "",
            ]
        if block == "sections":
            visibility = str(result_generation_policy.get("show_sections") or "always").strip().lower()
            if visibility == "never":
                return []
            if visibility == "auto" and not sections:
                return []
            return self._render_sections_block(sections, result_generation_policy)
        if block == "decisions":
            visibility = str(result_generation_policy.get("show_decisions") or "always").strip().lower()
            if visibility == "never":
                return []
            if visibility == "auto" and not decisions:
                return []
            return self._render_bullet_block(
                str(result_generation_policy["decisions_heading"]),
                decisions,
                str(result_generation_policy["empty_decisions_message"]),
            )
        if block == "action_items":
            visibility = str(result_generation_policy.get("show_action_items") or "always").strip().lower()
            if visibility == "never":
                return []
            if visibility == "auto" and not action_items:
                return []
            return self._render_bullet_block(
                str(result_generation_policy["action_items_heading"]),
                action_items,
                str(result_generation_policy["empty_action_items_message"]),
            )
        if block == "open_questions":
            visibility = str(result_generation_policy.get("show_open_questions") or "always").strip().lower()
            if visibility == "never":
                return []
            if visibility == "auto" and not open_questions:
                return []
            return self._render_bullet_block(
                str(result_generation_policy["open_questions_heading"]),
                open_questions,
                str(result_generation_policy["empty_open_questions_message"]),
            )
        if block == "risk_signals":
            visibility = str(result_generation_policy.get("show_risk_signals") or "never").strip().lower()
            if visibility == "never":
                return []
            if visibility == "auto" and not risk_signals:
                return []
            return self._render_bullet_block(
                str(result_generation_policy["risk_signals_heading"]),
                risk_signals,
                str(result_generation_policy["empty_risk_signals_message"]),
            )
        if block == "postprocess_requests":
            visibility = str(result_generation_policy.get("show_postprocess_requests") or "never").strip().lower()
            if visibility == "never":
                return []
            if visibility == "auto" and not postprocess_requests:
                return []
            return self._render_postprocess_requests_block(
                str(result_generation_policy["postprocess_requests_heading"]),
                postprocess_requests,
                str(result_generation_policy["empty_postprocess_requests_message"]),
                empty_item_title=str(result_generation_policy["empty_postprocess_item_title"]),
                empty_item_instruction=str(result_generation_policy["empty_postprocess_item_instruction"]),
            )
        if block == "memo":
            visibility = str(result_generation_policy.get("show_memo") or "never").strip().lower()
            if visibility == "never":
                return []
            memo_text = str(result_generation_policy.get("memo_text") or "").strip()
            if visibility == "auto" and not memo_text:
                return []
            return ["", f"## {result_generation_policy['memo_heading']}", "", memo_text]
        return []

    def _render_overview_block(
        self,
        session: DelegateSession,
        *,
        meeting_datetime: str,
        participant_text: str,
        result_generation_policy: dict[str, Any],
    ) -> list[str]:
        lines = ["", f"## {result_generation_policy['overview_heading']}", ""]
        fields = [
            ("show_overview_datetime", str(result_generation_policy["overview_datetime_label"]), meeting_datetime or "미확인"),
            ("show_overview_author", str(result_generation_policy["overview_author_label"]), session.bot_display_name),
            ("show_overview_session_id", str(result_generation_policy["overview_session_id_label"]), session.session_id),
            (
                "show_overview_participants",
                str(result_generation_policy["overview_participants_label"]),
                participant_text or str(result_generation_policy["empty_participants_message"]),
            ),
        ]
        for visibility_key, label, value in fields:
            visibility = str(result_generation_policy.get(visibility_key) or "always").strip().lower()
            if visibility == "never":
                continue
            if visibility == "auto" and not str(value or "").strip():
                continue
            lines.extend([f"**{label}**: {value}", ""])
        return lines

    def _render_sections_block(
        self,
        sections: list[dict[str, Any]],
        result_generation_policy: dict[str, Any],
    ) -> list[str]:
        lines: list[str] = ["", f"## {result_generation_policy['sections_heading']}", ""]
        max_timestamp_refs = 4
        if sections:
            for idx, section in enumerate(sections, start=1):
                heading = self._normalize(str(section.get("heading") or f"주제 {idx}")) or f"주제 {idx}"
                summary = self._normalize(str(section.get("summary") or "")) or str(result_generation_policy["empty_section_summary_message"])
                timestamp_refs = self._clean_list(section.get("timestamp_refs"))
                raised_by = self._normalize(str(section.get("raised_by") or ""))
                speakers = self._clean_list(section.get("speakers"))
                if str(result_generation_policy.get("section_numbering") or "numbered").strip().lower() == "plain":
                    section_heading = heading
                else:
                    section_heading = f"{idx}. {heading}"
                lines.extend([f"### {section_heading}", "", summary, ""])
                if raised_by:
                    lines.append(f"- 제기자: {raised_by}")
                display_speakers = list(speakers)
                if not display_speakers and raised_by:
                    display_speakers = [raised_by]
                if display_speakers:
                    lines.append(f"- 주요 화자: {', '.join(display_speakers[:3])}")
                if timestamp_refs:
                    display_timestamp_refs = self._limit_items(timestamp_refs, max_timestamp_refs)
                    formatted_refs = ", ".join(f"`{item}`" for item in display_timestamp_refs)
                    lines.append(f"- 타임스탬프: {formatted_refs}")
                lines.append("")
        else:
            lines.extend([f"- {result_generation_policy['empty_sections_message']}", ""])
        return lines

    def _render_bullet_block(self, heading: str, items: list[str], empty_message: str) -> list[str]:
        lines = ["", f"## {heading}", ""]
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append(f"- {empty_message}")
        return lines

    def _render_postprocess_requests_block(
        self,
        heading: str,
        items: list[dict[str, str]],
        empty_message: str,
        *,
        empty_item_title: str,
        empty_item_instruction: str,
    ) -> list[str]:
        lines = ["", f"## {heading}", ""]
        if not items:
            lines.append(f"- {empty_message}")
            return lines
        for item in items:
            title = self._normalize(str(item.get("title") or "")) or empty_item_title
            instruction = self._normalize(str(item.get("instruction") or "")) or empty_item_instruction
            tool_hint = self._normalize(str(item.get("tool_hint") or ""))
            caption = self._normalize(str(item.get("caption") or ""))
            if tool_hint:
                lines.append(f"- **{title}**: {instruction} (`{tool_hint}`)")
            else:
                lines.append(f"- **{title}**: {instruction}")
            if caption:
                lines.append(f"  - {caption}")
        return lines

    def _clean_postprocess_requests(self, value: Any) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for item in list(value or []):
            if not isinstance(item, dict):
                continue
            kind = self._normalize(str(item.get("kind") or ""))
            title = self._normalize(str(item.get("title") or ""))
            instruction = self._normalize(str(item.get("instruction") or ""))
            prompt = str(item.get("prompt") or "")
            tool_hint = self._normalize(str(item.get("tool_hint") or ""))
            caption = str(item.get("caption") or "")
            image_path = str(item.get("image_path") or "").strip()
            count = self._positive_int_or_default(item.get("count"), 1)
            placement_notes = str(item.get("placement_notes") or "")
            target_heading = str(item.get("target_heading") or "")
            if not kind or not title or not instruction:
                continue
            items.append(
                {
                    "kind": kind,
                    "title": title,
                    "instruction": instruction,
                    "prompt": prompt,
                    "tool_hint": tool_hint,
                    "caption": caption,
                    "image_path": image_path,
                    "count": str(count),
                    "placement_notes": placement_notes,
                    "target_heading": target_heading,
                }
            )
        return items

    def _result_generation_policy(self, session: DelegateSession) -> dict[str, Any]:
        state = dict(session.ai_state.get("meeting_output_skill") or {})
        policy = dict(state.get("result_generation_policy") or {})
        order = [
            str(item).strip()
            for item in list(policy.get("result_block_order") or [])
            if str(item).strip()
        ]
        if not order:
            order = ["overview", "executive_summary", "sections", "decisions", "action_items", "open_questions", "postprocess_requests", "memo"]
        result_block_order_mode = str(policy.get("result_block_order_mode") or "append_missing").strip().lower().replace("-", "_")
        if result_block_order_mode in {"exact_only", "only"}:
            result_block_order_mode = "exact"
        if result_block_order_mode not in {"append_missing", "exact"}:
            result_block_order_mode = "append_missing"
        max_display_sections = self._positive_int_or_none(policy.get("max_display_sections"))
        max_decisions = self._positive_int_or_none(policy.get("max_decisions"))
        max_action_items = self._positive_int_or_none(policy.get("max_action_items"))
        max_open_questions = self._positive_int_or_none(policy.get("max_open_questions"))
        max_risk_signals = self._positive_int_or_none(policy.get("max_risk_signals"))
        max_postprocess_requests = self._positive_int_or_none(policy.get("max_postprocess_requests"))
        max_section_timestamp_refs = 4
        open_questions_visibility = str(policy.get("show_open_questions") or "auto").strip().lower()
        if open_questions_visibility not in {"always", "auto", "never"}:
            open_questions_visibility = "auto"
        title_visibility = str(policy.get("show_title") or "always").strip().lower()
        if title_visibility not in {"always", "auto", "never"}:
            title_visibility = "always"
        overview_visibility = str(policy.get("show_overview") or "always").strip().lower()
        if overview_visibility not in {"always", "auto", "never"}:
            overview_visibility = "always"
        executive_summary_visibility = str(policy.get("show_executive_summary") or "always").strip().lower()
        if executive_summary_visibility not in {"always", "auto", "never"}:
            executive_summary_visibility = "always"
        risk_signals_visibility = str(policy.get("show_risk_signals") or "auto").strip().lower()
        if risk_signals_visibility not in {"always", "auto", "never"}:
            risk_signals_visibility = "auto"
        postprocess_requests_visibility = str(policy.get("show_postprocess_requests") or "never").strip().lower()
        if postprocess_requests_visibility not in {"always", "auto", "never"}:
            postprocess_requests_visibility = "never"
        sections_visibility = str(policy.get("show_sections") or "always").strip().lower()
        if sections_visibility not in {"always", "auto", "never"}:
            sections_visibility = "always"
        decisions_visibility = str(policy.get("show_decisions") or "auto").strip().lower()
        if decisions_visibility not in {"always", "auto", "never"}:
            decisions_visibility = "auto"
        action_items_visibility = str(policy.get("show_action_items") or "auto").strip().lower()
        if action_items_visibility not in {"always", "auto", "never"}:
            action_items_visibility = "auto"
        memo_visibility = str(policy.get("show_memo") or "never").strip().lower()
        if memo_visibility not in {"always", "auto", "never"}:
            memo_visibility = "never"
        section_raised_by_visibility = "always"
        section_speakers_visibility = "always"
        section_timestamps_visibility = "always"
        overview_datetime_visibility = str(policy.get("show_overview_datetime") or "always").strip().lower()
        if overview_datetime_visibility not in {"always", "auto", "never"}:
            overview_datetime_visibility = "always"
        overview_author_visibility = str(policy.get("show_overview_author") or "never").strip().lower()
        if overview_author_visibility not in {"always", "auto", "never"}:
            overview_author_visibility = "never"
        overview_session_id_visibility = str(policy.get("show_overview_session_id") or "never").strip().lower()
        if overview_session_id_visibility not in {"always", "auto", "never"}:
            overview_session_id_visibility = "never"
        overview_participants_visibility = str(policy.get("show_overview_participants") or "auto").strip().lower()
        if overview_participants_visibility not in {"always", "auto", "never"}:
            overview_participants_visibility = "auto"
        section_numbering = str(policy.get("section_numbering") or "numbered").strip().lower()
        if section_numbering in {"none", "unnumbered"}:
            section_numbering = "plain"
        if section_numbering not in {"numbered", "plain"}:
            section_numbering = "numbered"
        renderer_profile = "default"
        renderer_theme_name = str(policy.get("renderer_theme_name") or "").strip()
        renderer_primary_color = self._normalize_color_hex(policy.get("renderer_primary_color"))
        renderer_accent_color = self._normalize_color_hex(policy.get("renderer_accent_color"))
        renderer_neutral_color = self._normalize_color_hex(policy.get("renderer_neutral_color"))
        renderer_title_font = str(policy.get("renderer_title_font") or "").strip()
        renderer_heading_font = str(policy.get("renderer_heading_font") or "").strip()
        renderer_body_font = str(policy.get("renderer_body_font") or "").strip()
        renderer_cover_align = str(policy.get("renderer_cover_align") or "").strip()
        renderer_surface_tint_color = self._normalize_color_hex(policy.get("renderer_surface_tint_color"))
        renderer_cover_kicker = str(policy.get("renderer_cover_kicker") or "").strip()
        renderer_heading1_color = self._normalize_color_hex(policy.get("renderer_heading1_color"))
        renderer_heading2_color = self._normalize_color_hex(policy.get("renderer_heading2_color"))
        renderer_heading3_color = self._normalize_color_hex(policy.get("renderer_heading3_color"))
        renderer_body_text_color = self._normalize_color_hex(policy.get("renderer_body_text_color"))
        renderer_muted_text_color = self._normalize_color_hex(policy.get("renderer_muted_text_color"))
        renderer_title_divider_color = self._normalize_color_hex(policy.get("renderer_title_divider_color"))
        renderer_section_border_color = self._normalize_color_hex(policy.get("renderer_section_border_color"))
        renderer_table_header_fill_color = self._normalize_color_hex(policy.get("renderer_table_header_fill_color"))
        renderer_table_label_fill_color = self._normalize_color_hex(policy.get("renderer_table_label_fill_color"))
        renderer_cover_fill_color = self._normalize_color_hex(policy.get("renderer_cover_fill_color"))
        renderer_kicker_fill_color = self._normalize_color_hex(policy.get("renderer_kicker_fill_color"))
        renderer_kicker_text_color = self._normalize_color_hex(policy.get("renderer_kicker_text_color"))
        renderer_section_band_fill_color = self._normalize_color_hex(policy.get("renderer_section_band_fill_color"))
        renderer_section_panel_fill_color = self._normalize_color_hex(policy.get("renderer_section_panel_fill_color"))
        renderer_section_accent_fill_color = self._normalize_color_hex(policy.get("renderer_section_accent_fill_color"))
        renderer_overview_label_fill_color = self._normalize_color_hex(policy.get("renderer_overview_label_fill_color"))
        renderer_overview_value_fill_color = self._normalize_color_hex(policy.get("renderer_overview_value_fill_color"))
        renderer_overview_panel_fill_color = self._normalize_color_hex(policy.get("renderer_overview_panel_fill_color"))
        postprocess_image_width_inches = str(policy.get("postprocess_image_width_inches") or "5.9").strip() or "5.9"
        renderer_page_top_margin_inches = str(policy.get("renderer_page_top_margin_inches") or "").strip()
        renderer_page_bottom_margin_inches = str(policy.get("renderer_page_bottom_margin_inches") or "").strip()
        renderer_page_left_margin_inches = str(policy.get("renderer_page_left_margin_inches") or "").strip()
        renderer_page_right_margin_inches = str(policy.get("renderer_page_right_margin_inches") or "").strip()
        renderer_body_line_spacing = str(policy.get("renderer_body_line_spacing") or "").strip()
        renderer_list_line_spacing = str(policy.get("renderer_list_line_spacing") or "").strip()
        renderer_heading2_space_before_pt = str(policy.get("renderer_heading2_space_before_pt") or "").strip()
        renderer_heading2_space_after_pt = str(policy.get("renderer_heading2_space_after_pt") or "").strip()
        renderer_heading3_space_before_pt = str(policy.get("renderer_heading3_space_before_pt") or "").strip()
        renderer_heading3_space_after_pt = str(policy.get("renderer_heading3_space_after_pt") or "").strip()
        renderer_title_space_after_pt = str(policy.get("renderer_title_space_after_pt") or "").strip()
        renderer_title_divider_size = str(policy.get("renderer_title_divider_size") or "").strip()
        renderer_title_divider_space = str(policy.get("renderer_title_divider_space") or "").strip()
        return {
            "result_block_order": order,
            "result_block_order_mode": result_block_order_mode,
            "max_display_sections": max_display_sections,
            "max_decisions": max_decisions,
            "max_action_items": max_action_items,
            "max_open_questions": max_open_questions,
            "max_risk_signals": max_risk_signals,
            "max_postprocess_requests": max_postprocess_requests,
            "max_section_timestamp_refs": max_section_timestamp_refs,
            "renderer_profile": renderer_profile,
            "renderer_theme_name": renderer_theme_name,
            "renderer_primary_color": renderer_primary_color,
            "renderer_accent_color": renderer_accent_color,
            "renderer_neutral_color": renderer_neutral_color,
            "renderer_title_font": renderer_title_font,
            "renderer_heading_font": renderer_heading_font,
            "renderer_body_font": renderer_body_font,
            "renderer_cover_align": renderer_cover_align,
            "renderer_surface_tint_color": renderer_surface_tint_color,
            "renderer_cover_kicker": renderer_cover_kicker,
            "renderer_heading1_color": renderer_heading1_color,
            "renderer_heading2_color": renderer_heading2_color,
            "renderer_heading3_color": renderer_heading3_color,
            "renderer_body_text_color": renderer_body_text_color,
            "renderer_muted_text_color": renderer_muted_text_color,
            "renderer_title_divider_color": renderer_title_divider_color,
            "renderer_section_border_color": renderer_section_border_color,
            "renderer_table_header_fill_color": renderer_table_header_fill_color,
            "renderer_table_label_fill_color": renderer_table_label_fill_color,
            "renderer_cover_fill_color": renderer_cover_fill_color,
            "renderer_kicker_fill_color": renderer_kicker_fill_color,
            "renderer_kicker_text_color": renderer_kicker_text_color,
            "renderer_section_band_fill_color": renderer_section_band_fill_color,
            "renderer_section_panel_fill_color": renderer_section_panel_fill_color,
            "renderer_section_accent_fill_color": renderer_section_accent_fill_color,
            "renderer_overview_label_fill_color": renderer_overview_label_fill_color,
            "renderer_overview_value_fill_color": renderer_overview_value_fill_color,
            "renderer_overview_panel_fill_color": renderer_overview_panel_fill_color,
            "postprocess_image_width_inches": postprocess_image_width_inches,
            "renderer_page_top_margin_inches": renderer_page_top_margin_inches,
            "renderer_page_bottom_margin_inches": renderer_page_bottom_margin_inches,
            "renderer_page_left_margin_inches": renderer_page_left_margin_inches,
            "renderer_page_right_margin_inches": renderer_page_right_margin_inches,
            "renderer_body_line_spacing": renderer_body_line_spacing,
            "renderer_list_line_spacing": renderer_list_line_spacing,
            "renderer_heading2_space_before_pt": renderer_heading2_space_before_pt,
            "renderer_heading2_space_after_pt": renderer_heading2_space_after_pt,
            "renderer_heading3_space_before_pt": renderer_heading3_space_before_pt,
            "renderer_heading3_space_after_pt": renderer_heading3_space_after_pt,
            "renderer_title_space_after_pt": renderer_title_space_after_pt,
            "renderer_title_divider_size": renderer_title_divider_size,
            "renderer_title_divider_space": renderer_title_divider_space,
            "show_title": title_visibility,
            "show_overview": overview_visibility,
            "show_executive_summary": executive_summary_visibility,
            "show_sections": sections_visibility,
            "show_decisions": decisions_visibility,
            "show_action_items": action_items_visibility,
            "show_open_questions": open_questions_visibility,
            "show_risk_signals": risk_signals_visibility,
            "show_postprocess_requests": postprocess_requests_visibility,
            "show_memo": memo_visibility,
            "show_section_raised_by": section_raised_by_visibility,
            "show_section_speakers": section_speakers_visibility,
            "show_section_timestamps": section_timestamps_visibility,
            "show_overview_datetime": overview_datetime_visibility,
            "show_overview_author": overview_author_visibility,
            "show_overview_session_id": overview_session_id_visibility,
            "show_overview_participants": overview_participants_visibility,
            "section_numbering": section_numbering,
            "overview_heading": str(policy.get("overview_heading") or "회의 개요"),
            "overview_datetime_label": str(policy.get("overview_datetime_label") or "회의 일시"),
            "overview_author_label": str(policy.get("overview_author_label") or "작성 주체"),
            "overview_session_id_label": str(policy.get("overview_session_id_label") or "세션 ID"),
            "overview_participants_label": str(policy.get("overview_participants_label") or "참석자"),
            "executive_summary_heading": str(policy.get("executive_summary_heading") or "회의 전체 요약"),
            "sections_heading": str(policy.get("sections_heading") or "핵심 논의 주제"),
            "decisions_heading": str(policy.get("decisions_heading") or "결정사항"),
            "action_items_heading": str(policy.get("action_items_heading") or "액션 아이템"),
            "open_questions_heading": str(policy.get("open_questions_heading") or "열린 질문"),
            "risk_signals_heading": str(policy.get("risk_signals_heading") or "리스크 신호"),
            "postprocess_requests_heading": str(policy.get("postprocess_requests_heading") or "추가 결과물 제안"),
            "memo_heading": str(policy.get("memo_heading") or "메모"),
            "section_raised_by_label": "제기자",
            "section_speakers_label": "주요 화자",
            "section_timestamps_label": "타임스탬프",
            "empty_executive_summary_message": str(policy.get("empty_executive_summary_message") or "회의 전체 요약이 아직 생성되지 않았습니다."),
            "empty_sections_message": str(policy.get("empty_sections_message") or "핵심 논의 주제가 아직 정리되지 않았습니다."),
            "empty_decisions_message": str(policy.get("empty_decisions_message") or "아직 확정된 결정사항이 없습니다."),
            "empty_action_items_message": str(policy.get("empty_action_items_message") or "추출된 액션 아이템이 없습니다."),
            "empty_open_questions_message": str(policy.get("empty_open_questions_message") or "현재 남은 열린 질문이 없습니다."),
            "empty_risk_signals_message": str(policy.get("empty_risk_signals_message") or "현재 강조할 리스크 신호가 없습니다."),
            "empty_postprocess_requests_message": str(policy.get("empty_postprocess_requests_message") or "현재 추가 결과물 제안이 없습니다."),
            "empty_participants_message": str(policy.get("empty_participants_message") or "미확인"),
            "empty_section_summary_message": str(policy.get("empty_section_summary_message") or "요약 내용이 없습니다."),
            "empty_postprocess_item_title": str(policy.get("empty_postprocess_item_title") or "후속 처리"),
            "empty_postprocess_item_instruction": str(policy.get("empty_postprocess_item_instruction") or "추가 후속 처리 요청"),
            "memo_text": str(policy.get("memo_text") or "세부 음성 전사와 채팅 원문은 별도 export 파일에서 확인할 수 있습니다."),
        }

    def _positive_int_or_default(self, value: Any, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return number if number > 0 else default

    def _positive_int_or_none(self, value: Any) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _limit_items(self, items: list[Any], limit: Any) -> list[Any]:
        parsed_limit = self._positive_int_or_none(limit)
        if parsed_limit is None:
            return list(items)
        return list(items)[:parsed_limit]

    def _normalize_color_hex(self, value: Any) -> str:
        text = str(value or "").strip().lstrip("#").upper()
        if len(text) == 3 and all(ch in "0123456789ABCDEF" for ch in text):
            text = "".join(ch * 2 for ch in text)
        if len(text) == 6 and all(ch in "0123456789ABCDEF" for ch in text):
            return text
        return ""

    def _enrich_sections_for_display(self, session: DelegateSession, sections: list[Any]) -> list[dict[str, Any]]:
        valid_items = [item for item in sections if isinstance(item, dict)]
        section_count = max(len(valid_items), 1)
        enriched: list[dict[str, Any]] = []
        for section_index, item in enumerate(valid_items):
            section = dict(item)
            heading = self._normalize(str(section.get("heading") or ""))
            refs = self._restore_section_timestamp_refs(
                session,
                list(section.get("timestamp_refs") or []),
                heading=heading,
                summary=self._normalize(str(section.get("summary") or "")),
                section_index=section_index,
                section_count=section_count,
            )
            section["timestamp_refs"] = refs
            if refs:
                raised_by = self._speaker_from_timestamp_refs(
                    session,
                    refs,
                    topic_hints=[
                        heading,
                        self._normalize(str(section.get("summary") or "")),
                    ],
                )
                if raised_by:
                    section["raised_by"] = raised_by
                elif self._is_placeholder_participant_label(self._normalize(str(section.get("raised_by") or ""))):
                    section.pop("raised_by", None)
                speaker_rankings = self._speaker_rankings_from_timestamp_refs(session, refs)
                speaker_candidates = [speaker for speaker, _ in speaker_rankings]
                if speaker_candidates:
                    display_speakers = list(speaker_candidates[:3])
                    if len(speaker_rankings) >= 2:
                        top_score = float(speaker_rankings[0][1] or 0.0)
                        second_score = float(speaker_rankings[1][1] or 0.0)
                        participant_count = len(
                            [
                                item
                                for item in self._named_human_participants(session)
                                if item != session.bot_display_name
                            ]
                        )
                        if participant_count <= 2 and second_score < (top_score * 0.5):
                            display_speakers = [speaker_rankings[0][0]]
                    if len(display_speakers) >= 2:
                        section["speakers"] = display_speakers
                    else:
                        section.pop("speakers", None)
                else:
                    existing_speakers = self._clean_list(section.get("speakers"))
                    if existing_speakers:
                        section["speakers"] = existing_speakers
                    else:
                        section.pop("speakers", None)
            enriched.append(section)
        return enriched

    def render_transcript_markdown(self, session: DelegateSession) -> str:
        briefing = dict((session.summary_packet or {}).get("briefing") or {})
        title = self._display_title(session, briefing) if briefing else self._resolved_title(session)
        lines = [
            f"# {title} - 전사 원문",
            "",
            f"- 세션 ID: `{session.session_id}`",
            f"- 회의 번호: {session.meeting_id or session.meeting_number or '미확인'}",
            f"- 모드: {session.delegate_mode}",
            f"- 입력 이벤트 수: {len(session.input_timeline)}",
            f"- 전사 라인 수: {len(session.transcript)}",
            f"- 채팅 수: {len(session.chat_history)}",
            "",
            "## 음성 전사",
            "",
        ]
        if not session.transcript:
            lines.append("_아직 수집된 음성 전사가 없습니다._")
        else:
            for idx, chunk in enumerate(session.transcript, start=1):
                speaker = self._speaker_display_name(
                    chunk.speaker,
                    getattr(chunk, "metadata", {}),
                    session=session,
                    created_at=getattr(chunk, "created_at", None),
                )
                text = self._normalize(chunk.text)
                source = self._source_label(chunk)
                lines.append(f"{idx}. **{speaker}** [{source}]")
                lines.append(f"   {text}")

        lines.extend(["", "## 회의 채팅", ""])
        if not session.chat_history:
            lines.append("_아직 수집된 회의 채팅이 없습니다._")
        else:
            for idx, turn in enumerate(session.chat_history, start=1):
                speaker = self._speaker_display_name(
                    turn.speaker or turn.role or "participant",
                    session=session,
                    created_at=getattr(turn, "created_at", None),
                )
                text = self._normalize(turn.text)
                source = self._normalize(turn.source or "meeting_chat") or "meeting_chat"
                lines.append(f"{idx}. **{speaker}** [{source}]")
                lines.append(f"   {text}")
        return "\n".join(lines).strip() + "\n"

    def _participants_need_refresh(self, packet: dict[str, Any]) -> bool:
        participants = [self._normalize(str(item)) for item in list(packet.get("participants") or []) if self._normalize(str(item))]
        if not participants:
            return False
        if any(self._is_internal_placeholder_participant_label(item) for item in participants):
            return True
        placeholder_only = [item for item in participants if self._is_placeholder_participant_label(item)]
        return bool(len(placeholder_only) == len(participants))

    def _overview_participants(self, session: DelegateSession, participants: list[str]) -> list[str]:
        display_participants: list[str] = []
        for item in participants:
            normalized = self._normalize(str(item))
            if (
                not normalized
                or normalized == session.bot_display_name
                or self._is_placeholder_participant_label(normalized)
                or self._is_internal_placeholder_participant_label(normalized)
            ):
                continue
            self._append_unique(display_participants, normalized)
        if display_participants:
            return display_participants
        for item in self._named_human_participants(session):
            if item and item != session.bot_display_name and not self._is_placeholder_participant_label(item):
                self._append_unique(display_participants, item)
        return display_participants

    def _display_title(self, session: DelegateSession, briefing: dict[str, Any]) -> str:
        title = self._normalize(str(briefing.get("title") or ""))
        if title and not self._looks_like_broken_title(title):
            return title
        section_title = self._section_title_fallback(list(briefing.get("sections") or []))
        if section_title:
            return section_title
        summary_head = self._first_sentence(self._normalize(str(briefing.get("executive_summary") or "")))
        if summary_head and not self._looks_like_broken_title(summary_head):
            return summary_head[:80]
        return self._resolved_title(session)

    def _sections_from_ai(self, session: DelegateSession, ai_result: dict[str, Any]) -> list[dict[str, Any]]:
        raw_sections = ai_result.get("sections")
        if not isinstance(raw_sections, list):
            return []
        valid_items = [item for item in raw_sections if isinstance(item, dict)]
        section_count = max(len(valid_items), 1)
        sections: list[dict[str, Any]] = []
        for section_index, item in enumerate(valid_items):
            heading = self._normalize(str(item.get("heading") or ""))
            summary = self._normalize(str(item.get("summary") or ""))
            refs = self._restore_section_timestamp_refs(
                session,
                list(item.get("timestamp_refs") or []),
                heading=heading,
                summary=summary,
                section_index=section_index,
                section_count=section_count,
            )
            if not heading or not summary:
                continue
            section = {"heading": heading, "summary": summary, "timestamp_refs": refs}
            raised_by = self._speaker_from_timestamp_refs(session, refs, topic_hints=[heading, summary])
            if raised_by:
                section["raised_by"] = raised_by
            sections.append(section)
        return sections

    def _fallback_sections(self, session: DelegateSession, *, executive_summary: str, decisions: list[str], action_items: list[str], open_questions: list[str], risk_signals: list[str]) -> list[dict[str, Any]]:
        records = self._interaction_records(session)
        sections: list[dict[str, Any]] = []
        definitions = [
            ("회의 흐름 요약", executive_summary, records[:3]),
            ("결정과 후속 작업", self._join_sentences(decisions[:3] + action_items[:3]), records[2:6]),
            ("남은 질문과 리스크", self._join_sentences(open_questions[:3] + risk_signals[:3]), records[-4:]),
        ]
        for heading, summary, slice_records in definitions:
            if not summary:
                continue
            section = {"heading": heading, "summary": summary, "timestamp_refs": self._timeline_timestamp_refs(slice_records)}
            raised_by = self._speaker_from_records(session, slice_records)
            if raised_by:
                section["raised_by"] = raised_by
            sections.append(section)
        if not sections:
            records = records[:4]
            section = {"heading": "회의 메모", "summary": self._fallback_summary(session), "timestamp_refs": self._timeline_timestamp_refs(records)}
            raised_by = self._speaker_from_records(session, records)
            if raised_by:
                section["raised_by"] = raised_by
            sections.append(section)
        return sections

    def _interaction_records(self, session: DelegateSession) -> list[dict[str, str]]:
        cache_key = self._session_cache_key(session)
        cached = self._interaction_records_cache.get(cache_key)
        if cached is not None:
            return [dict(item) for item in cached]
        records: list[dict[str, str]] = []
        for chunk in session.transcript:
            speaker = self._speaker_display_name(
                chunk.speaker,
                getattr(chunk, "metadata", {}),
                session=session,
                created_at=getattr(chunk, "created_at", None),
            )
            text = self._normalize(chunk.text)
            if text:
                records.append({"timestamp_ref": self._time_ref_from_chunk(chunk), "speaker": speaker, "text": text})
        for turn in session.chat_history:
            speaker = self._speaker_display_name(
                turn.speaker or turn.role or "participant",
                session=session,
                created_at=getattr(turn, "created_at", None),
            )
            text = self._normalize(turn.text)
            if text:
                records.append({"timestamp_ref": self._time_ref(turn.created_at), "speaker": speaker, "text": text})
        self._interaction_records_cache[cache_key] = [dict(item) for item in records]
        return records

    def _timeline_timestamp_refs(self, records: list[dict[str, str]]) -> list[str]:
        refs: list[str] = []
        for item in records:
            ref = self._normalize(item.get("timestamp_ref") or "")
            if ref and ref not in refs:
                refs.append(ref)
        return refs[:4]

    def _restore_section_timestamp_refs(
        self,
        session: DelegateSession,
        refs: list[Any],
        *,
        heading: str,
        summary: str,
        section_index: int,
        section_count: int,
    ) -> list[str]:
        cleaned_refs = self._clean_list(refs)
        if cleaned_refs:
            return cleaned_refs[:4]
        recovered_refs = self._recover_timestamp_refs_for_section(session, heading=heading, summary=summary)
        if recovered_refs:
            return recovered_refs[:4]
        return self._section_position_timestamp_refs(session, section_index=section_index, section_count=section_count)

    def _recover_timestamp_refs_for_section(
        self,
        session: DelegateSession,
        *,
        heading: str,
        summary: str,
    ) -> list[str]:
        topic_tokens = self._topic_tokens(heading, summary)
        if not topic_tokens:
            return []
        ranked_matches: list[tuple[float, int, str]] = []
        for index, item in enumerate(self._interaction_records(session)):
            ref = self._normalize(item.get("timestamp_ref") or "")
            text = self._normalize(item.get("text") or "")
            if not ref or not text:
                continue
            overlap = self._topic_overlap_score(text, topic_tokens)
            if overlap <= 0:
                continue
            score = float(overlap)
            if self._looks_like_substantive_intro_text(text):
                score += 0.25
            speaker = self._normalize(item.get("speaker") or "")
            if speaker and not self._is_placeholder_participant_label(speaker):
                score += 0.1
            ranked_matches.append((score, index, ref))
        if not ranked_matches:
            return []
        ranked_matches.sort(key=lambda item: (-item[0], item[1], item[2]))
        selected = sorted(ranked_matches[:4], key=lambda item: item[1])
        refs: list[str] = []
        for _, _, ref in selected:
            self._append_unique(refs, ref)
        return refs[:4]

    def _section_position_timestamp_refs(
        self,
        session: DelegateSession,
        *,
        section_index: int,
        section_count: int,
    ) -> list[str]:
        records = self._interaction_records(session)
        if not records:
            return []
        normalized_count = max(section_count, 1)
        start = int((section_index / normalized_count) * len(records))
        end = int(((section_index + 1) / normalized_count) * len(records))
        if end <= start:
            end = min(len(records), start + 1)
        slice_records = records[start:end] or records[start : min(len(records), start + 1)] or records[:1]
        return self._timeline_timestamp_refs(slice_records)

    def _speaker_from_timestamp_refs(self, session: DelegateSession, refs: list[str], *, topic_hints: list[str] | None = None) -> str:
        cleaned_refs = self._clean_list(refs)
        if not cleaned_refs:
            return ""
        records = [
            item
            for item in self._interaction_records(session)
            if self._normalize(item.get("speaker") or "")
            and self._normalize(item.get("speaker") or "") != session.bot_display_name
        ]
        if not records:
            return ""

        named_records = [item for item in records if not self._is_placeholder_participant_label(item.get("speaker") or "")]
        candidate_records = named_records or records
        ref_seconds = [value for value in (self._time_reference_seconds(ref) for ref in cleaned_refs) if value is not None]
        if not ref_seconds:
            return self._speaker_from_records(session, candidate_records)

        earliest_ref = min(ref_seconds)
        window_start = earliest_ref - 25.0
        window_end = earliest_ref + 2.5
        focus_records: list[tuple[float, dict[str, str]]] = []
        for item in candidate_records:
            item_seconds = self._time_reference_seconds(item.get("timestamp_ref") or "")
            if item_seconds is None:
                continue
            if window_start <= item_seconds <= window_end:
                focus_records.append((item_seconds, item))
        if not focus_records:
            candidates = self._speaker_candidates_from_timestamp_refs(session, refs)
            return candidates[0] if candidates else ""

        focus_records.sort(key=lambda pair: pair[0])
        anchor_index = min(range(len(focus_records)), key=lambda idx: abs(focus_records[idx][0] - earliest_ref))
        cluster_start = anchor_index
        while cluster_start > 0:
            current_seconds = focus_records[cluster_start][0]
            previous_seconds = focus_records[cluster_start - 1][0]
            if current_seconds - previous_seconds > 6.0:
                break
            cluster_start -= 1
        cluster = [item for _, item in focus_records[cluster_start : anchor_index + 1]]

        topic_sources = list(topic_hints or [])
        if not topic_sources:
            topic_sources = [item.get("text") or "" for item in candidate_records if item.get("timestamp_ref") in cleaned_refs]
        topic_tokens = self._topic_tokens(*topic_sources)
        if topic_tokens:
            for item in cluster:
                speaker = self._normalize(item.get("speaker") or "")
                text = self._normalize(item.get("text") or "")
                if speaker and not self._is_placeholder_participant_label(speaker) and self._topic_overlap_score(text, topic_tokens) > 0:
                    return speaker

        for item in cluster:
            speaker = self._normalize(item.get("speaker") or "")
            text = self._normalize(item.get("text") or "")
            if speaker and not self._is_placeholder_participant_label(speaker) and self._looks_like_substantive_intro_text(text):
                return speaker
        for item in cluster:
            speaker = self._normalize(item.get("speaker") or "")
            if speaker and not self._is_placeholder_participant_label(speaker):
                return speaker

        candidates = self._speaker_candidates_from_timestamp_refs(session, refs)
        return candidates[0] if candidates else ""

    def _speaker_candidates_from_timestamp_refs(self, session: DelegateSession, refs: list[str]) -> list[str]:
        return [speaker for speaker, _ in self._speaker_rankings_from_timestamp_refs(session, refs)]

    def _speaker_rankings_from_timestamp_refs(self, session: DelegateSession, refs: list[str]) -> list[tuple[str, float]]:
        cleaned_refs = self._clean_list(refs)
        if not cleaned_refs:
            return []
        records = [
            item
            for item in self._interaction_records(session)
            if self._normalize(item.get("speaker") or "")
            and self._normalize(item.get("speaker") or "") != session.bot_display_name
        ]
        if not records:
            preferred = self._preferred_local_speaker_name(session)
            return [(preferred, 1.0)] if preferred else []

        exact_votes: list[str] = []
        proximity_votes: list[tuple[str, float]] = []
        for ref in cleaned_refs:
            for item in records:
                if item.get("timestamp_ref") == ref:
                    speaker = self._normalize(item.get("speaker") or "")
                    if speaker:
                        exact_votes.append(speaker)
            target_seconds = self._time_reference_seconds(ref)
            if target_seconds is None:
                continue
            nearest_by_speaker: dict[str, float] = {}
            for item in records:
                item_seconds = self._time_reference_seconds(item.get("timestamp_ref") or "")
                if item_seconds is None:
                    continue
                delta = abs(item_seconds - target_seconds)
                if delta > 5.0:
                    continue
                speaker = self._normalize(item.get("speaker") or "")
                if not speaker:
                    continue
                previous = nearest_by_speaker.get(speaker)
                if previous is None or delta < previous:
                    nearest_by_speaker[speaker] = delta
            for speaker, delta in nearest_by_speaker.items():
                proximity_votes.append((speaker, delta))

        all_votes = list(exact_votes) + [speaker for speaker, _ in proximity_votes]
        named_votes = [speaker for speaker in all_votes if not self._is_placeholder_participant_label(speaker)]
        if named_votes:
            exact_votes = [speaker for speaker in exact_votes if not self._is_placeholder_participant_label(speaker)]
            proximity_votes = [(speaker, delta) for speaker, delta in proximity_votes if not self._is_placeholder_participant_label(speaker)]
        elif all_votes and all(self._is_placeholder_participant_label(speaker) for speaker in all_votes):
            preferred = self._preferred_local_speaker_name(session)
            return [(preferred, 1.0)] if preferred else []

        scores: dict[str, float] = {}
        first_seen: dict[str, int] = {}
        for idx, speaker in enumerate(exact_votes):
            scores[speaker] = scores.get(speaker, 0.0) + 3.0
            first_seen.setdefault(speaker, idx)
        start_index = len(first_seen)
        for idx, (speaker, delta) in enumerate(proximity_votes):
            weight = max(0.25, 1.5 - (delta / 5.0))
            scores[speaker] = scores.get(speaker, 0.0) + weight
            first_seen.setdefault(speaker, start_index + idx)

        if scores:
            ranked = sorted(scores.items(), key=lambda item: (-item[1], first_seen.get(item[0], 10_000), item[0]))
            return [(speaker, score) for speaker, score in ranked[:3]]

        preferred = self._preferred_local_speaker_name(session)
        if preferred:
            return [(preferred, 1.0)]
        return []

    def _speaker_from_records(self, session: DelegateSession, records: list[dict[str, str]]) -> str:
        for item in records:
            speaker = self._normalize(item.get("speaker") or "")
            if speaker and speaker != session.bot_display_name:
                return speaker
        return ""

    def _looks_like_substantive_intro_text(self, text: str) -> bool:
        normalized = self._normalize(text)
        if not normalized:
            return False
        lowered = normalized.lower()
        if lowered in {"네", "예", "네네", "음", "음.", "오케이", "오케이 오케이", "맞습니다", "좋습니다", "아 네"}:
            return False
        return len(normalized) >= 6

    def _topic_tokens(self, *values: str) -> set[str]:
        tokens: set[str] = set()
        for value in values:
            normalized = self._normalize(value)
            if not normalized:
                continue
            for token in re.split(r"[^\w가-힣]+", normalized):
                clean = self._normalize(token)
                if len(clean) >= 2:
                    tokens.add(clean.lower())
        return tokens

    def _topic_overlap_score(self, text: str, topic_tokens: set[str]) -> int:
        if not topic_tokens:
            return 0
        normalized = self._normalize(text).lower()
        if not normalized:
            return 0
        return sum(1 for token in topic_tokens if token in normalized)

    def _session_participants(
        self,
        session: DelegateSession,
        *,
        transcript_participants: set[str],
        unresolved_local_speaker: bool,
        unresolved_remote_speaker: bool,
    ) -> list[str]:
        participants: list[str] = []
        if session.bot_display_name:
            participants.append(session.bot_display_name)
        participant_state_humans = self._participant_state_human_names(session)
        for label in self._participant_state_labels(session):
            self._append_unique(participants, label)
        for turn in session.chat_history:
            if turn.role == "bot":
                continue
            speaker = self._speaker_display_name(
                turn.speaker or turn.role or "participant",
                session=session,
                created_at=getattr(turn, "created_at", None),
            )
            if not self._is_placeholder_participant_label(speaker):
                self._append_unique(participants, speaker)

        for speaker in sorted(transcript_participants):
            self._append_unique(participants, speaker)

        named_humans = [item for item in participants if item != session.bot_display_name]
        if unresolved_local_speaker and not named_humans:
            self._append_unique(participants, "로컬 발화자")
        if unresolved_remote_speaker and not participant_state_humans:
            self._append_unique(participants, "원격 참가자(이름 미확인)")
        return participants[:20]

    def _participant_state_labels(self, session: DelegateSession) -> list[str]:
        cache_key = self._session_cache_key(session)
        cached = self._participant_state_labels_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        labels: list[str] = []
        entries = list(session.input_timeline) + list(session.workspace_events)
        for entry in entries:
            entry_type = self._normalize(getattr(entry, "input_type", "") or getattr(entry, "event_type", ""))
            if "participant_state" not in entry_type:
                continue
            metadata = dict(getattr(entry, "metadata", {}) or {})
            raw_value = metadata.get("raw")
            raw = dict(raw_value) if isinstance(raw_value, dict) else {}
            candidate = (
                self._normalize(getattr(entry, "speaker", "") or "")
                or self._normalize(str(raw.get("displayName") or raw.get("participantName") or raw.get("userName") or ""))
                or self._normalize(str(metadata.get("participant") or ""))
            )
            if not candidate or self._is_placeholder_participant_label(candidate):
                continue
            self._append_unique(labels, candidate)
        self._participant_state_labels_cache[cache_key] = list(labels)
        return labels

    def _resolved_title(self, session: DelegateSession, *, ai_result: dict[str, Any] | None = None, sections: list[dict[str, Any]] | None = None, executive_summary: str | None = None) -> str:
        ai_result = dict(ai_result or {})
        sections = list(sections or [])
        for candidate in (self._normalize(str(ai_result.get("title") or "")), self._normalize(str(session.meeting_topic or ""))):
            if candidate and not self._looks_like_broken_title(candidate):
                return candidate
        section_title = self._section_title_fallback(sections)
        if section_title:
            return section_title
        summary_head = self._first_sentence(self._normalize(str(executive_summary or "")))
        if summary_head and not self._looks_like_broken_title(summary_head):
            return summary_head[:80]
        if session.meeting_number:
            return f"Zoom 회의 {session.meeting_number}"
        if session.meeting_id:
            return f"회의 {session.meeting_id}"
        return "회의 요약"

    def _looks_like_broken_title(self, value: str) -> bool:
        text = self._normalize(value)
        if not text:
            return True
        if "�" in text or text.count("?") >= 3:
            return True
        lowered = text.lower()
        if lowered in {"zoom", "zoom meeting", "zoom 회의", "meeting", "회의"}:
            return True
        return lowered.startswith("zoom") and re.fullmatch(r"zoom[\s\-\:_?？!./]*", lowered) is not None

    def _is_generic_title_candidate(self, value: str) -> bool:
        lowered = self._normalize(value).lower()
        return lowered in {
            "회의 흐름 요약",
            "결정과 후속 작업",
            "남은 질문과 리스크",
            "회의 메모",
            "회의 전체 요약",
            "결정사항",
            "액션 아이템",
            "열린 질문",
        }

    def _section_title_fallback(self, sections: list[dict[str, Any]] | list[Any]) -> str:
        headings: list[str] = []
        for item in sections:
            if not isinstance(item, dict):
                continue
            heading = self._normalize(str(item.get("heading") or ""))
            if not heading or self._looks_like_broken_title(heading) or self._is_generic_title_candidate(heading):
                continue
            if heading not in headings:
                headings.append(heading)
        if not headings:
            return ""
        if len(headings) == 1:
            return headings[0]
        combined = f"{headings[0]} · {headings[1]}"
        return combined[:80]

    def _fallback_summary(self, session: DelegateSession) -> str:
        recent_lines = self._interaction_lines(session)[-4:]
        return self._join_sentences(recent_lines) if recent_lines else "회의 핵심 내용이 아직 정리되지 않았습니다."

    def _interaction_lines(self, session: DelegateSession) -> list[str]:
        lines: list[str] = []
        for item in self._interaction_records(session):
            ref = self._normalize(item.get("timestamp_ref") or "")
            speaker = self._normalize(item.get("speaker") or "")
            text = self._normalize(item.get("text") or "")
            prefix = f"[{ref}] " if ref else ""
            if speaker and text:
                lines.append(f"{prefix}{speaker}: {text}".strip())
        return lines

    def _meeting_datetime_label(self, session: DelegateSession) -> str:
        raw = str(session.updated_at or session.created_at or "").strip()
        if not raw:
            return ""
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return raw
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")

    def _join_sentences(self, items: list[str]) -> str:
        cleaned = [self._normalize(item) for item in items if self._normalize(item)]
        return " ".join(cleaned[:6]) if cleaned else ""

    def _clean_list(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for item in values:
            self._append_unique(result, self._normalize(str(item)))
        return result

    def _append_unique(self, values: list[str], item: str) -> None:
        cleaned = self._normalize(item)
        if cleaned and cleaned not in values:
            values.append(cleaned)

    def _should_collect_transcript_intelligence(self, session: DelegateSession, chunk: Any, text: str) -> bool:
        if len(text) > 280:
            return False
        speaker = self._normalize(getattr(chunk, "speaker", "") or "")
        if speaker and speaker == self._normalize(session.bot_display_name):
            return False
        source = self._normalize(getattr(chunk, "source", "") or "")
        return "bot" not in source

    def _speaker_display_name(
        self,
        speaker: str,
        metadata: dict[str, Any] | None = None,
        *,
        session: DelegateSession | None = None,
        created_at: str | None = None,
        resolve_local_alias: bool = True,
    ) -> str:
        meta = dict(metadata or {})
        for key in ("speaker_name", "speaker_display_name", "participant_name", "participantName", "display_name", "displayName", "userName", "user_name", "name"):
            candidate = self._normalize(str(meta.get(key) or ""))
            if candidate and not self._is_placeholder_participant_label(candidate):
                return candidate
        normalized = self._normalize(speaker or "unknown") or "unknown"
        lowered = normalized.lower()
        audio_source = self._normalize(str(meta.get("audio_source") or meta.get("capture_mode") or ""))
        uses_internal_placeholder = (
            self._is_local_placeholder_label(normalized)
            or self._is_remote_placeholder_label(normalized)
            or self._is_known_internal_speaker(lowered)
        )
        if uses_internal_placeholder:
            zoom_name = self._zoom_active_speaker_name(session, metadata=meta, created_at=created_at)
            if zoom_name:
                return zoom_name
        preferred_local_name = self._preferred_local_speaker_name(session) if resolve_local_alias else None
        preferred_remote_name = self._preferred_remote_speaker_name(session) if resolve_local_alias else None
        use_local_source_hint = self._is_local_placeholder_label(normalized) or (
            audio_source == "microphone" and self._is_known_internal_speaker(lowered)
        )
        use_remote_source_hint = self._is_remote_placeholder_label(normalized) or (
            audio_source == "system" and self._is_known_internal_speaker(lowered)
        )
        if preferred_local_name and use_local_source_hint:
            return preferred_local_name
        if preferred_remote_name and use_remote_source_hint:
            return preferred_remote_name
        if lowered not in {"participant", "meeting_audio", "unknown"} and not self._is_known_internal_speaker(lowered):
            return normalized
        if self._is_local_placeholder_label(normalized) or audio_source == "microphone":
            return "로컬 발화자"
        if self._is_remote_placeholder_label(normalized) or audio_source == "system":
            return "원격 참가자(이름 미확인)"
        if lowered == "local_system_audio":
            return "회의 출력 음성"
        return audio_source or normalized

    def _zoom_active_speaker_name(
        self,
        session: DelegateSession | None,
        *,
        metadata: dict[str, Any],
        created_at: str | None,
        allowed_names: set[str] | None = None,
    ) -> str | None:
        if session is None:
            return None
        events = self._zoom_active_speaker_events(session)
        if not events:
            return None
        allowed = {self._normalize(name) for name in (allowed_names or set()) if self._normalize(name)}
        target_offset = self._session_offset_seconds(metadata)
        if target_offset is not None:
            best_name: str | None = None
            best_delta: float | None = None
            for event in events:
                name = self._normalize(str(event.get("name") or ""))
                if allowed and name not in allowed:
                    continue
                offset = event.get("offset_seconds")
                if offset is None:
                    continue
                delta = abs(offset - target_offset)
                if delta > 3.0:
                    continue
                if best_delta is None or delta < best_delta:
                    best_delta = delta
                    best_name = name
            if best_name:
                return best_name
        target = self._event_datetime(created_at) or self._event_datetime(str(metadata.get("captured_at") or ""))
        if target is None:
            return None
        best_name: str | None = None
        best_delta: float | None = None
        for event in events:
            name = self._normalize(str(event.get("name") or ""))
            when = event.get("when")
            if not name or when is None:
                continue
            if allowed and name not in allowed:
                continue
            delta = abs((when - target).total_seconds())
            if delta > 8.0:
                continue
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_name = name
        return best_name

    def _zoom_active_speaker_events(self, session: DelegateSession) -> list[dict[str, Any]]:
        cache_key = self._session_cache_key(session)
        cached = self._zoom_active_speaker_events_cache.get(cache_key)
        if cached is not None:
            return [dict(item) for item in cached]
        events: list[dict[str, Any]] = []
        for entry in session.input_timeline:
            if entry.input_type != "participant_state":
                continue
            metadata = dict(entry.metadata or {})
            if self._normalize(str(metadata.get("event") or "")).lower() != "active-speaker":
                continue
            raw_value = metadata.get("raw")
            raw = dict(raw_value) if isinstance(raw_value, dict) else {}
            name = (
                self._normalize(str(entry.speaker or ""))
                or self._normalize(str(raw.get("displayName") or raw.get("userName") or raw.get("participantName") or ""))
            )
            if not name or name == session.bot_display_name:
                continue
            when = self._event_datetime(str(entry.created_at or "")) or self._event_datetime(str(metadata.get("created_at") or ""))
            events.append(
                {
                    "name": name,
                    "user_id": self._normalize(str(raw.get("userId") or metadata.get("userId") or "")),
                    "when": when,
                    "offset_seconds": self._session_offset_seconds(metadata),
                }
            )
        self._zoom_active_speaker_events_cache[cache_key] = [dict(item) for item in events]
        return events

    def _session_offset_seconds(self, metadata: dict[str, Any] | None) -> float | None:
        meta = dict(metadata or {})
        for key in ("session_offset_seconds", "session_start_offset_seconds"):
            value = meta.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _event_datetime(self, value: str | None) -> datetime | None:
        text = self._normalize(str(value or ""))
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def _preferred_local_speaker_name(self, session: DelegateSession | None) -> str | None:
        return self._preferred_named_speakers(session)[0] if session is not None else None

    def _preferred_remote_speaker_name(self, session: DelegateSession | None) -> str | None:
        return self._preferred_named_speakers(session)[1] if session is not None else None

    def _preferred_named_speakers(self, session: DelegateSession) -> tuple[str | None, str | None]:
        cache_key = self._session_cache_key(session)
        cached = self._preferred_named_speakers_cache.get(cache_key)
        if cached is not None:
            return cached

        names = [item for item in self._participant_state_human_names(session) if item != session.bot_display_name]
        if not names:
            names = [item for item in self._named_human_participants(session) if item != session.bot_display_name]
        if len(names) == 1:
            result = (names[0], names[0])
            self._preferred_named_speakers_cache[cache_key] = result
            return result

        local_votes = self._source_hint_speaker_votes(session, source_kind="local", candidate_names=names)
        remote_votes = self._source_hint_speaker_votes(session, source_kind="remote", candidate_names=names)
        local_name = self._select_preferred_speaker_name(local_votes)
        remote_name = self._select_preferred_speaker_name(remote_votes)

        # When one person dominates both local/remote vote pools, avoid forcing the
        # remaining participant into the remote slot by elimination alone.
        if not local_name:
            local_name = self._best_distinct_vote_name(local_votes, excluded_names=set())
        if local_name and remote_name == local_name:
            remote_name = None
        remote_name = self._coerce_distinct_preferred_speaker_name(
            remote_name,
            votes=remote_votes,
            excluded_names={local_name} if local_name else set(),
        )
        if remote_name:
            local_name = self._coerce_distinct_preferred_speaker_name(
                local_name,
                votes=local_votes,
                excluded_names={remote_name},
            )

        result = (local_name, remote_name)
        self._preferred_named_speakers_cache[cache_key] = result
        return result

    def _participant_state_human_names(self, session: DelegateSession) -> list[str]:
        return [
            label
            for label in self._participant_state_labels(session)
            if label and label != session.bot_display_name and not self._is_placeholder_participant_label(label)
        ]

    def _source_hint_speaker_votes(
        self,
        session: DelegateSession,
        *,
        source_kind: str,
        candidate_names: list[str],
    ) -> dict[str, int]:
        allowed_names = {self._normalize(name) for name in candidate_names if self._normalize(name)}
        if not allowed_names:
            return {}
        votes: dict[str, int] = {}
        for chunk in session.transcript:
            raw_speaker = self._normalize(getattr(chunk, "speaker", "") or "")
            metadata = dict(getattr(chunk, "metadata", {}) or {})
            audio_source = self._normalize(str(metadata.get("audio_source") or metadata.get("capture_mode") or ""))
            if source_kind == "local":
                source_matches = self._is_local_placeholder_label(raw_speaker) or audio_source == "microphone"
            else:
                source_matches = self._is_remote_placeholder_label(raw_speaker) or audio_source == "system"
            if not source_matches:
                continue
            matched_name = self._zoom_active_speaker_name(
                session,
                metadata=metadata,
                created_at=getattr(chunk, "created_at", None),
                allowed_names=allowed_names,
            )
            if not matched_name:
                continue
            votes[matched_name] = votes.get(matched_name, 0) + 1
        return votes

    def _select_preferred_speaker_name(self, votes: dict[str, int]) -> str | None:
        if not votes:
            return None
        ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
        if len(ranked) == 1:
            return ranked[0][0]
        top_name, top_count = ranked[0]
        second_count = ranked[1][1]
        if top_count >= 2 and (top_count - second_count >= 2 or top_count >= (second_count * 1.25)):
            return top_name
        return None

    def _coerce_distinct_preferred_speaker_name(
        self,
        current_name: str | None,
        *,
        votes: dict[str, int],
        excluded_names: set[str],
    ) -> str | None:
        normalized_excluded = {self._normalize(name) for name in excluded_names if self._normalize(name)}
        current = self._normalize(current_name or "")
        if current and current not in normalized_excluded:
            return current
        return self._best_distinct_vote_name(votes, excluded_names=normalized_excluded)

    def _best_distinct_vote_name(self, votes: dict[str, int], *, excluded_names: set[str]) -> str | None:
        if not votes:
            return None
        ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
        overall_top_count = ranked[0][1]
        for name, count in ranked:
            normalized_name = self._normalize(name)
            if not normalized_name or normalized_name in excluded_names:
                continue
            if count < 2:
                return None
            if count >= max(2, overall_top_count * 0.4):
                return normalized_name
            return None
        return None

    def _named_human_participants(self, session: DelegateSession) -> list[str]:
        names: list[str] = []
        for label in self._participant_state_human_names(session):
            self._append_unique(names, label)
        for turn in session.chat_history:
            if turn.role == "bot":
                continue
            speaker = self._speaker_display_name(
                turn.speaker or turn.role or "participant",
                session=session,
                created_at=getattr(turn, "created_at", None),
                resolve_local_alias=False,
            )
            if speaker and speaker != session.bot_display_name and not self._is_placeholder_participant_label(speaker):
                self._append_unique(names, speaker)
        return names

    def _session_cache_key(self, session: DelegateSession) -> tuple[str, str]:
        return (session.session_id, self._normalize(session.updated_at))

    def _is_known_internal_speaker(self, lowered: str) -> bool:
        return lowered in {"participant", "meeting_audio", "unknown", "local_user", "remote_participant", "local_microphone", "local_system_audio", "meeting_output"} or lowered.startswith("local_user_") or lowered.startswith("remote_participant_") or lowered.startswith("local_microphone_")

    def _is_placeholder_participant_label(self, value: str) -> bool:
        text = self._normalize(value)
        return (not text) or self._is_local_placeholder_label(text) or self._is_remote_placeholder_label(text) or text.lower() in {"participant", "meeting_audio", "unknown", "local_system_audio", "meeting_output"}

    def _is_internal_placeholder_participant_label(self, value: str) -> bool:
        lowered = self._normalize(value).lower()
        return lowered in {
            "participant",
            "meeting_audio",
            "unknown",
            "local_user",
            "remote_participant",
            "local_microphone",
            "local_system_audio",
            "meeting_output",
        } or lowered.startswith("local_user_") or lowered.startswith("remote_participant_") or lowered.startswith("local_microphone_")

    def _is_local_placeholder_label(self, value: str) -> bool:
        lowered = self._normalize(value).lower()
        return lowered in {"local_user", "local_microphone", "로컬 발화자"} or lowered.startswith("local_user_") or lowered.startswith("local_microphone_") or lowered.startswith("로컬 발화자 ")

    def _is_remote_placeholder_label(self, value: str) -> bool:
        lowered = self._normalize(value).lower()
        return lowered in {"remote_participant", "회의 출력 음성", "원격 참가자", "원격 참가자(이름 미확인)"} or lowered.startswith("remote_participant_") or lowered.startswith("원격 참가자 ")

    def _source_label(self, chunk: Any) -> str:
        source = self._normalize(getattr(chunk, "source", "") or "manual") or "manual"
        label = self._time_label_from_chunk(chunk).strip()
        return source if not label else f"{source} | {label.strip('[] ')}"

    def _time_ref_from_chunk(self, chunk: Any) -> str:
        metadata = dict(getattr(chunk, "metadata", {}) or {})
        start_offset = metadata.get("session_start_offset_seconds")
        if start_offset is not None:
            return self._clock_label(start_offset)
        start_offset = metadata.get("start_offset_seconds")
        if start_offset is not None:
            return self._clock_label(start_offset)
        return self._normalize(str(getattr(chunk, "created_at", None) or ""))

    def _time_label_from_chunk(self, chunk: Any) -> str:
        return self._time_label(self._time_ref_from_chunk(chunk))

    def _time_ref(self, value: Any) -> str:
        return self._normalize(str(value or ""))

    def _time_reference_seconds(self, value: Any) -> float | None:
        text = self._time_ref(value)
        if not text:
            return None
        if re.fullmatch(r"\d{2}:\d{2}\.\d{2}", text):
            minutes_text, seconds_text = text.split(":", 1)
            try:
                return (int(minutes_text) * 60) + float(seconds_text)
            except ValueError:
                return None
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}\.\d{2}", text):
            hours_text, minutes_text, seconds_text = text.split(":", 2)
            try:
                return (int(hours_text) * 3600) + (int(minutes_text) * 60) + float(seconds_text)
            except ValueError:
                return None
        return None

    def _time_label(self, value: Any) -> str:
        text = self._time_ref(value)
        return f"[{text}] " if text else ""

    def _clock_label(self, seconds: Any) -> str:
        try:
            total = max(float(seconds), 0.0)
        except (TypeError, ValueError):
            return ""
        minutes = int(total // 60)
        remainder = total - (minutes * 60)
        if minutes >= 60:
            hours = minutes // 60
            minutes = minutes % 60
            return f"{hours:02d}:{minutes:02d}:{remainder:05.2f}"
        return f"{minutes:02d}:{remainder:05.2f}"

    def _collect_meeting_intelligence(self, *, text: str, action_candidates: list[str], decision_candidates: list[str], open_questions: list[str], risk_signals: list[str]) -> None:
        action = self._action_candidate(text)
        if action and action not in action_candidates:
            action_candidates.append(action)
        decision = self._decision_candidate(text)
        if decision and decision not in decision_candidates:
            decision_candidates.append(decision)
        question = self._question_candidate(text)
        if question and question not in open_questions:
            open_questions.append(question)
        risk = self._risk_candidate(text)
        if risk and risk not in risk_signals:
            risk_signals.append(risk)

    def _classify_source(self, source: str) -> str:
        normalized = self._normalize(source).lower()
        if normalized.startswith("local_") or normalized.startswith("platform_audio") or normalized == "manual" or "transcript" in normalized or "audio" in normalized:
            return "spoken_transcript"
        if "chat" in normalized:
            return "workspace_chat"
        return "other"

    def _action_candidate(self, text: str) -> str | None:
        return text if any(token in text.lower() for token in ("action", "todo", "follow up", "next step", "need to", "should", "해야", "후속", "액션")) else None

    def _decision_candidate(self, text: str) -> str | None:
        return text if any(token in text.lower() for token in ("decided", "we will", "we'll", "confirmed", "approved", "정했다", "결정", "확정", "확인")) else None

    def _question_candidate(self, text: str) -> str | None:
        if "?" in text:
            return text
        return text if any(token in text.lower() for token in ("question", "need to know", "unclear", "whether", "what if", "어떻게", "무엇", "언제", "가능한가")) else None

    def _risk_candidate(self, text: str) -> str | None:
        return text if any(token in text.lower() for token in ("risk", "blocker", "issue", "problem", "delay", "concern", "리스크", "문제", "이슈", "지연", "막자")) else None

    def _first_sentence(self, text: str) -> str:
        normalized = self._normalize(text)
        return re.split(r"(?<=[.!?])\s+", normalized, maxsplit=1)[0].strip() if normalized else ""

    def _normalize(self, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()
