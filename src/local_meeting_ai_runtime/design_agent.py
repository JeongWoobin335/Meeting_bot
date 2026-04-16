from __future__ import annotations

from typing import Any


class MeetingOutputDesignAgent:
    _RENDERER_COLOR_KEYS = (
        "renderer_primary_color",
        "renderer_accent_color",
        "renderer_neutral_color",
        "renderer_surface_tint_color",
        "renderer_heading1_color",
        "renderer_heading2_color",
        "renderer_heading3_color",
        "renderer_body_text_color",
        "renderer_muted_text_color",
        "renderer_title_divider_color",
        "renderer_section_border_color",
        "renderer_table_header_fill_color",
        "renderer_table_label_fill_color",
        "renderer_cover_fill_color",
        "renderer_kicker_fill_color",
        "renderer_kicker_text_color",
        "renderer_section_band_fill_color",
        "renderer_section_panel_fill_color",
        "renderer_section_accent_fill_color",
        "renderer_overview_label_fill_color",
        "renderer_overview_value_fill_color",
        "renderer_overview_panel_fill_color",
    )
    _RENDERER_TOKEN_KEYS = ("renderer_cover_align",)
    _RENDERER_TEXT_KEYS = (
        "renderer_theme_name",
        "renderer_title_font",
        "renderer_heading_font",
        "renderer_body_font",
        "renderer_cover_kicker",
    )
    _RENDERER_NUMERIC_KEYS = (
        "postprocess_image_width_inches",
        "renderer_page_top_margin_inches",
        "renderer_page_bottom_margin_inches",
        "renderer_page_left_margin_inches",
        "renderer_page_right_margin_inches",
        "renderer_body_line_spacing",
        "renderer_list_line_spacing",
        "renderer_heading2_space_before_pt",
        "renderer_heading2_space_after_pt",
        "renderer_heading3_space_before_pt",
        "renderer_heading3_space_after_pt",
        "renderer_title_space_after_pt",
        "renderer_title_divider_size",
        "renderer_title_divider_space",
    )
    _RENDERER_OVERRIDE_KEYS = (
        _RENDERER_COLOR_KEYS + _RENDERER_TOKEN_KEYS + _RENDERER_TEXT_KEYS + _RENDERER_NUMERIC_KEYS
    )

    def resolve(
        self,
        *,
        active_skill: dict[str, object] | None,
        current_policy: dict[str, Any] | None,
        source: str = "",
    ) -> dict[str, object]:
        policy = dict(current_policy or {})
        skill = dict(active_skill or {})
        metadata = dict(skill.get("metadata") or {})
        packet = self._build_intent_packet(
            skill=skill,
            policy=policy,
            source=source,
        )
        resolved_policy = dict(policy)
        renderer_overrides = dict(packet.get("renderer_overrides") or {})

        for key in self._RENDERER_OVERRIDE_KEYS:
            value = str(renderer_overrides.get(key) or "").strip()
            self._set_inferred(
                resolved_policy,
                metadata,
                key,
                value,
                default_value=self._default_value_for_key(key),
            )

        packet["theme_name"] = str(
            resolved_policy.get("renderer_theme_name")
            or packet.get("theme_name")
            or ""
        ).strip()
        packet["cover_align"] = str(resolved_policy.get("renderer_cover_align") or "").strip()
        packet["surface_tint_color"] = self._normalize_color_hex(
            resolved_policy.get("renderer_surface_tint_color")
        )
        packet["cover_kicker"] = str(resolved_policy.get("renderer_cover_kicker") or "").strip()
        packet["surface_controls"] = {
            key: str(resolved_policy.get(key) or "").strip()
            for key in self._RENDERER_NUMERIC_KEYS
            if str(resolved_policy.get(key) or "").strip()
        }

        return {
            "intent_packet": packet,
            "resolved_policy": resolved_policy,
        }

    def _build_intent_packet(
        self,
        *,
        skill: dict[str, object],
        policy: dict[str, Any],
        source: str,
    ) -> dict[str, object]:
        renderer_overrides = self._normalize_renderer_overrides(policy)
        directive_lines = self._skill_instruction_lines(skill)

        return {
            "source": source,
            "theme_name": str(
                policy.get("renderer_theme_name")
                or renderer_overrides.get("renderer_theme_name")
                or ""
            ).strip(),
            "cover_align": "",
            "surface_tint_color": "",
            "cover_kicker": "",
            "design_priority": "normal",
            "require_brand_research": False,
            "visuals_requested": False,
            "postprocess_image_placement": "",
            "postprocess_image_anchor": "",
            "postprocess_image_target_heading": "",
            "postprocess_image_minimum_count": 0,
            "directive_lines": directive_lines,
            "execution_notes": [],
            "design_notes": [],
            "visual_notes": [],
            "notes": [],
            "renderer_overrides": renderer_overrides,
        }

    def _set_inferred(
        self,
        policy: dict[str, Any],
        metadata: dict[str, Any],
        key: str,
        value: str,
        *,
        default_value: str,
    ) -> None:
        if not str(value or "").strip():
            return
        if str(metadata.get(key) or "").strip():
            return
        existing = str(policy.get(key) or "").strip()
        if existing and existing != str(default_value or "").strip():
            return
        policy[key] = value

    def _explicit_value(
        self,
        metadata: dict[str, Any],
        policy: dict[str, Any],
        *,
        key: str,
        default_value: str,
    ) -> str:
        metadata_value = str(metadata.get(key) or "").strip()
        if metadata_value:
            return metadata_value
        policy_value = str(policy.get(key) or "").strip()
        if not policy_value:
            return ""
        if policy_value == str(default_value or "").strip():
            return ""
        return policy_value

    def _normalize_renderer_overrides(self, value: Any) -> dict[str, str]:
        payload = dict(value or {})
        normalized: dict[str, str] = {}
        for key in self._RENDERER_OVERRIDE_KEYS:
            raw = payload.get(key)
            if key in self._RENDERER_COLOR_KEYS:
                text = self._normalize_color_hex(raw)
            else:
                text = self._normalize_text_value(raw)
            if text:
                normalized[key] = text
        return normalized

    def _default_value_for_key(self, key: str) -> str:
        return ""

    def _normalize_text_value(self, value: Any) -> str:
        return str(value or "").strip()

    def _skill_instruction_lines(self, skill: dict[str, object]) -> list[str]:
        lines: list[str] = []
        description = str(skill.get("description") or "")
        if description:
            lines.append(description)
        body = str(skill.get("body") or "")
        if body:
            lines.extend(body.splitlines())
        return lines

    def _normalize_color_hex(self, value: Any) -> str:
        text = str(value or "").strip().lstrip("#").upper()
        if len(text) == 3 and all(ch in "0123456789ABCDEF" for ch in text):
            text = "".join(ch * 2 for ch in text)
        if len(text) == 6 and all(ch in "0123456789ABCDEF" for ch in text):
            return text
        return ""
