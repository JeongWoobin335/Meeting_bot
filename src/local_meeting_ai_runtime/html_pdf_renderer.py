"""HTML/CSS based PDF-first renderer for meeting outputs."""

from __future__ import annotations

import ast
import html
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from .font_resolver import expand_css_font_stack, font_prefers_serif, stylesheet_import_urls_for_fonts


class HTMLPDFRenderError(RuntimeError):
    """Raised when the HTML/CSS PDF renderer cannot complete."""


_DEFAULT_BASE_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{DOCUMENT_TITLE}}</title>
  <style>
{{STYLE}}
  </style>
</head>
<body class="{{BODY_CLASS}}">
{{BODY_HTML}}
</body>
</html>
"""

_DEFAULT_REPORT_CSS = ""


class HTMLPDFRenderer:
    """Render briefing payloads to HTML and PDF through a browser engine."""

    def __init__(self) -> None:
        self._browser = self._resolve_browser()
        self._timeout = float(os.getenv("DELEGATE_HTML_PDF_TIMEOUT_SECONDS", "90"))
        self._template_dir = Path("doc/templates/html-meeting-output")
        self._base_template_path = self._template_dir / "base.html"
        self._report_css_path = self._template_dir / "report.css"
        self._pdf_settle_seconds = max(float(os.getenv("DELEGATE_HTML_PDF_SETTLE_SECONDS", "5.0")), 0.0)

    @property
    def pdf_ready(self) -> bool:
        return bool(self._browser)

    def readiness(self) -> dict[str, Any]:
        blocking_reasons: list[str] = []
        if not self._browser:
            blocking_reasons.append("Chrome/Edge is not available for HTML-first summary.pdf export.")
        return {
            "html_pdf_ready": bool(self._browser),
            "html_pdf_browser_path": self._browser,
            "html_template_dir": str(self._template_dir.resolve()),
            "blocking_reasons": blocking_reasons,
        }

    def render_summary_bundle(
        self,
        summary_markdown_path: Path,
        *,
        briefing: dict[str, Any],
        rendering_policy: dict[str, Any] | None = None,
        postprocess_requests: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        exports: list[dict[str, str]] = []
        output_dir = summary_markdown_path.parent
        html_path = summary_markdown_path.with_suffix(".html")
        pdf_path = summary_markdown_path.with_suffix(".pdf")
        manifest_path = output_dir / "render_manifest.json"

        policy = dict(rendering_policy or {})
        requests = list(postprocess_requests or [])
        font_resources = self._prepare_font_resources(rendering_policy=policy, base_dir=output_dir)
        html_text = self.render_html_document(
            briefing=briefing,
            rendering_policy=policy,
            postprocess_requests=requests,
            base_dir=output_dir,
            embedded_font_css=str(font_resources.get("css") or ""),
        )
        html_path.write_text(html_text, encoding="utf-8")
        exports.append({"format": "html", "path": str(html_path)})

        warnings: list[str] = list(font_resources.get("warnings") or [])
        pdf_generated = False
        if not self._browser:
            warnings.append("No compatible browser was found, so HTML was generated without PDF rendering.")
        else:
            self._render_pdf_from_html(html_path, pdf_path)
            pdf_generated = self._wait_for_pdf_output(pdf_path)
            if pdf_generated:
                exports.append({"format": "pdf", "path": str(pdf_path)})
            else:
                warnings.append("Browser render completed without producing a PDF file.")

        manifest = {
            "renderer": "html_pdf_renderer",
            "renderer_version": "prototype-v2",
            "browser_path": self._browser,
            "template_dir": str(self._template_dir.resolve()),
            "theme_name": str(policy.get("renderer_theme_name") or ""),
            "fonts": {
                "title": str(policy.get("renderer_title_font") or ""),
                "heading": str(policy.get("renderer_heading_font") or ""),
                "body": str(policy.get("renderer_body_font") or ""),
            },
            "font_stylesheets": list(font_resources.get("stylesheets") or []),
            "font_assets": list(font_resources.get("assets") or []),
            "images": self._resolve_visual_assets(requests, base_dir=output_dir),
            "html_path": str(html_path),
            "pdf_path": str(pdf_path),
            "pdf_generated": pdf_generated,
            "warnings": warnings,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        exports.append({"format": "render_manifest", "path": str(manifest_path)})
        return exports

    def render_html_document(
        self,
        *,
        briefing: dict[str, Any],
        rendering_policy: dict[str, Any] | None = None,
        postprocess_requests: list[dict[str, Any]] | None = None,
        base_dir: Path,
        embedded_font_css: str | None = None,
    ) -> str:
        policy = dict(rendering_policy or {})
        requests = list(postprocess_requests or briefing.get("postprocess_requests") or [])
        visuals = self._resolve_visual_assets(requests, base_dir=base_dir)
        title = str(briefing.get("title") or "Meeting Summary").strip() or "Meeting Summary"
        sections = list(briefing.get("sections") or [])
        decisions = list(briefing.get("decisions") or [])
        action_items = list(briefing.get("action_items") or [])
        open_questions = list(briefing.get("open_questions") or [])
        risk_signals = list(briefing.get("risk_signals") or [])
        block_order = self._content_block_order(policy)
        section_headings = {
            str(section.get("heading") or "").strip()
            for section in sections
            if str(section.get("heading") or "").strip()
        }
        sections_will_render = (
            "sections" in block_order
            and self._is_visible(policy.get("show_sections"), has_content=bool(sections), default="always")
        )

        body_html: list[str] = ['<main class="document">']
        cover_html = self._render_cover(briefing=briefing, rendering_policy=policy)
        if cover_html:
            body_html.append(cover_html)
        body_html.append('<section class="page">')
        for block_name in block_order:
            block_html = self._render_named_block(
                block_name,
                briefing=briefing,
                rendering_policy=policy,
                sections=sections,
                decisions=decisions,
                action_items=action_items,
                open_questions=open_questions,
                risk_signals=risk_signals,
                requests=requests,
                visuals=visuals,
                section_headings=section_headings,
                sections_will_render=sections_will_render,
            )
            if block_html:
                body_html.append(block_html)
        body_html.append("</section>")
        body_html.append("</main>")

        if embedded_font_css is None:
            embedded_font_css = str(self._prepare_font_resources(rendering_policy=policy, base_dir=base_dir).get("css") or "")
        style = self._compose_style(rendering_policy=policy, embedded_font_css=embedded_font_css)
        template = self._load_text(self._base_template_path, _DEFAULT_BASE_TEMPLATE)
        return (
            template
            .replace("{{DOCUMENT_TITLE}}", html.escape(title))
            .replace("{{STYLE}}", style)
            .replace("{{BODY_CLASS}}", self._body_classes(policy))
            .replace("{{BODY_HTML}}", "\n".join(body_html))
        )

    def _content_block_order(self, rendering_policy: dict[str, Any]) -> list[str]:
        order = [str(item).strip() for item in list(rendering_policy.get("result_block_order") or []) if str(item).strip()]
        if not order:
            order = ["overview", "executive_summary", "sections", "decisions", "action_items", "open_questions", "risk_signals", "postprocess_requests", "memo"]
        mode = str(rendering_policy.get("result_block_order_mode") or "append_missing").strip().lower()
        if mode != "exact":
            if "overview" not in order:
                order.insert(0, "overview")
            if "executive_summary" not in order:
                insert_at = order.index("overview") + 1 if "overview" in order else 0
                order.insert(insert_at, "executive_summary")
            for block_name in ("sections", "decisions", "action_items", "open_questions", "risk_signals", "postprocess_requests", "memo"):
                if block_name not in order:
                    order.append(block_name)
        deduped: list[str] = []
        seen: set[str] = set()
        for block_name in order:
            if block_name in seen:
                continue
            seen.add(block_name)
            deduped.append(block_name)
        return deduped

    def _body_classes(self, rendering_policy: dict[str, Any]) -> str:
        classes = [
            "meeting-output",
            f'cover-layout-{self._cover_layout(rendering_policy)}',
            f'cover-background-{self._cover_background_style(rendering_policy)}',
            f'panel-style-{self._panel_style(rendering_policy)}',
            f'heading-style-{self._heading_style(rendering_policy)}',
            f'overview-layout-{self._overview_layout(rendering_policy)}',
            f'section-style-{self._section_style(rendering_policy)}',
            f'list-style-{self._list_style(rendering_policy)}',
        ]
        return " ".join(classes)

    def _block_open_tag(self, *, block_name: str, classes: list[str]) -> str:
        normalized = str(block_name or "").strip().lower().replace(" ", "_").replace("-", "_")
        class_names = ["block-shell", f"block-name-{normalized}", *[item for item in classes if item]]
        return f'<section class="{" ".join(class_names)}" data-block="{html.escape(normalized)}">'

    def _render_named_block(
        self,
        block_name: str,
        *,
        briefing: dict[str, Any],
        rendering_policy: dict[str, Any],
        sections: list[dict[str, Any]],
        decisions: list[Any],
        action_items: list[Any],
        open_questions: list[Any],
        risk_signals: list[Any],
        requests: list[dict[str, Any]],
        visuals: list[dict[str, Any]],
        section_headings: set[str],
        sections_will_render: bool,
    ) -> str:
        block = str(block_name or "").strip().lower()
        if block == "overview":
            rows = self._overview_rows(briefing=briefing, rendering_policy=rendering_policy)
            if not self._is_visible(rendering_policy.get("show_overview"), has_content=bool(rows), default="always"):
                return ""
            return self._render_overview(
                self._policy_text(rendering_policy, "overview_heading", "Overview"),
                rows,
                block_name=block,
                rendering_policy=rendering_policy,
            )
        if block == "executive_summary":
            summary_text = str(briefing.get("executive_summary") or "").strip()
            if not self._is_visible(rendering_policy.get("show_executive_summary"), has_content=bool(summary_text), default="always"):
                return ""
            return self._render_prose_block(
                heading=self._policy_text(rendering_policy, "executive_summary_heading", "Executive Summary"),
                paragraphs=[summary_text or self._policy_text(rendering_policy, "empty_executive_summary_message", "No executive summary is available yet.")],
                block_name=block,
                rendering_policy=rendering_policy,
            )
        if block == "sections":
            if not self._is_visible(rendering_policy.get("show_sections"), has_content=bool(sections), default="always"):
                return ""
            if not sections:
                return self._render_prose_block(
                    heading=self._policy_text(rendering_policy, "sections_heading", "Key Topics"),
                    paragraphs=[self._policy_text(rendering_policy, "empty_sections_message", "No sections are available yet.")],
                    block_name=block,
                    rendering_policy=rendering_policy,
                )
            return self._render_sections_block(
                heading=self._policy_text(rendering_policy, "sections_heading", "Key Topics"),
                sections=sections,
                visuals=visuals,
                block_name=block,
                rendering_policy=rendering_policy,
                raised_by_label=self._policy_text(rendering_policy, "section_raised_by_label", "Raised By"),
                speakers_label=self._policy_text(rendering_policy, "section_speakers_label", "Speakers"),
                timestamps_label=self._policy_text(rendering_policy, "section_timestamps_label", "Timestamps"),
                numbering_mode=str(rendering_policy.get("section_numbering") or "numbered").strip().lower(),
                show_raised_by=self._is_visible(rendering_policy.get("show_section_raised_by"), has_content=True, default="always"),
                show_speakers=self._is_visible(rendering_policy.get("show_section_speakers"), has_content=True, default="always"),
                show_timestamps=self._is_visible(rendering_policy.get("show_section_timestamps"), has_content=True, default="always"),
                empty_summary_message=self._policy_text(rendering_policy, "empty_section_summary_message", "No section summary is available."),
            )
        if block == "decisions":
            if not self._is_visible(rendering_policy.get("show_decisions"), has_content=bool(decisions), default="auto"):
                return ""
            return self._render_item_list_block(
                self._policy_text(rendering_policy, "decisions_heading", "Decisions"),
                decisions,
                empty_message=self._policy_text(rendering_policy, "empty_decisions_message", "No decisions are available."),
                block_name=block,
                rendering_policy=rendering_policy,
            )
        if block == "action_items":
            if not self._is_visible(rendering_policy.get("show_action_items"), has_content=bool(action_items), default="auto"):
                return ""
            return self._render_item_list_block(
                self._policy_text(rendering_policy, "action_items_heading", "Action Items"),
                action_items,
                empty_message=self._policy_text(rendering_policy, "empty_action_items_message", "No action items are available."),
                block_name=block,
                rendering_policy=rendering_policy,
            )
        if block == "open_questions":
            if not self._is_visible(rendering_policy.get("show_open_questions"), has_content=bool(open_questions), default="auto"):
                return ""
            return self._render_item_list_block(
                self._policy_text(rendering_policy, "open_questions_heading", "Open Questions"),
                open_questions,
                empty_message=self._policy_text(rendering_policy, "empty_open_questions_message", "No open questions are available."),
                block_name=block,
                rendering_policy=rendering_policy,
            )
        if block == "risk_signals":
            if not self._is_visible(rendering_policy.get("show_risk_signals"), has_content=bool(risk_signals), default="auto"):
                return ""
            return self._render_item_list_block(
                self._policy_text(rendering_policy, "risk_signals_heading", "Risk Signals"),
                risk_signals,
                empty_message=self._policy_text(rendering_policy, "empty_risk_signals_message", "No risk signals are available."),
                block_name=block,
                rendering_policy=rendering_policy,
            )
        if block == "postprocess_requests":
            appendix_visuals = self._appendix_visuals(visuals, section_headings=section_headings, sections_will_render=sections_will_render)
            request_items = self._postprocess_request_items(requests)
            if not self._is_visible(rendering_policy.get("show_postprocess_requests"), has_content=bool(appendix_visuals or request_items), default="never"):
                return ""
            return self._render_postprocess_block(
                heading=self._policy_text(rendering_policy, "postprocess_requests_heading", "Result Materials"),
                requests=request_items,
                visuals=appendix_visuals,
                empty_message=self._policy_text(rendering_policy, "empty_postprocess_requests_message", "No result materials are available."),
                block_name=block,
                rendering_policy=rendering_policy,
            )
        if block == "memo":
            memo_text = str(rendering_policy.get("memo_text") or "").strip()
            if not self._is_visible(rendering_policy.get("show_memo"), has_content=bool(memo_text), default="never"):
                return ""
            return self._render_memo_block(
                self._policy_text(rendering_policy, "memo_heading", "Memo"),
                memo_text,
                block_name=block,
                rendering_policy=rendering_policy,
            )
        return ""

    def _render_pdf_from_html(self, html_path: Path, pdf_path: Path) -> None:
        if not self._browser:
            raise HTMLPDFRenderError("HTML-first PDF export requires a compatible browser.")
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._browser,
            "--headless=new",
            "--disable-gpu",
            "--allow-file-access-from-files",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_path.resolve().as_uri(),
        ]
        self._run(command, error_label="Browser HTML PDF export")

    def _wait_for_pdf_output(self, pdf_path: Path) -> bool:
        deadline = time.monotonic() + self._pdf_settle_seconds
        while True:
            try:
                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    return True
            except OSError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def _run(self, args: list[str], *, error_label: str) -> None:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                check=False,
            )
        except OSError as exc:
            raise HTMLPDFRenderError(f"{error_label} could not start: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise HTMLPDFRenderError(f"{error_label} timed out after {self._timeout} seconds.") from exc
        if completed.returncode != 0:
            details = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
            raise HTMLPDFRenderError(f"{error_label} failed: {details}")

    def _render_cover(self, *, briefing: dict[str, Any], rendering_policy: dict[str, Any]) -> str:
        show_title = str(rendering_policy.get("show_title") or "always").strip().lower()
        title = html.escape(str(briefing.get("title") or "Meeting Summary").strip() or "Meeting Summary")
        kicker = html.escape(str(rendering_policy.get("renderer_cover_kicker") or "").strip())
        cover_layout = self._cover_layout(rendering_policy)
        cover_background = self._cover_background_style(rendering_policy)
        meeting_datetime = html.escape(str(briefing.get("meeting_datetime_label") or "").strip())
        participants = ", ".join(str(item).strip() for item in list(briefing.get("participants") or []) if str(item).strip())
        participants = html.escape(participants)
        cover_meta = []
        if meeting_datetime:
            cover_meta.append(
                f'<div class="cover-meta-label">{html.escape(self._policy_text(rendering_policy, "overview_datetime_label", "Datetime"))}</div>'
                f'<div class="cover-meta-value">{meeting_datetime}</div>'
            )
        if participants:
            cover_meta.append(
                f'<div class="cover-meta-label">{html.escape(self._policy_text(rendering_policy, "overview_participants_label", "Participants"))}</div>'
                f'<div class="cover-meta-value">{participants}</div>'
            )
        title_html = f'<h1 class="cover-title">{title}</h1>' if show_title != "never" else ""
        divider_html = '<div class="cover-divider"></div>' if show_title != "never" else ""
        cover_main = (
            '<div class="cover-main">'
            + (f'<div class="cover-kicker">{kicker}</div>' if kicker else "")
            + title_html
            + divider_html
            + "</div>"
        )
        cover_meta_html = f'<div class="cover-meta">{"".join(cover_meta)}</div>' if cover_meta else ""
        if not (title_html or kicker or cover_meta_html):
            return ""
        return (
            f'<section class="cover cover-layout-{cover_layout} cover-background-{cover_background}">'
            + '<div class="cover-inner">'
            + cover_main
            + cover_meta_html
            + "</div>"
            + "</section>"
        )

    def _overview_rows(self, *, briefing: dict[str, Any], rendering_policy: dict[str, Any]) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        meeting_datetime = str(briefing.get("meeting_datetime_label") or "").strip()
        if self._is_visible(rendering_policy.get("show_overview_datetime"), has_content=bool(meeting_datetime), default="always") and meeting_datetime:
            rows.append((self._policy_text(rendering_policy, "overview_datetime_label", "Datetime"), meeting_datetime))
        overview_author = str(briefing.get("overview_author") or "").strip()
        if self._is_visible(rendering_policy.get("show_overview_author"), has_content=bool(overview_author), default="never") and overview_author:
            rows.append((self._policy_text(rendering_policy, "overview_author_label", "Author"), overview_author))
        overview_session_id = str(briefing.get("overview_session_id") or "").strip()
        if self._is_visible(rendering_policy.get("show_overview_session_id"), has_content=bool(overview_session_id), default="never") and overview_session_id:
            rows.append((self._policy_text(rendering_policy, "overview_session_id_label", "Session ID"), overview_session_id))
        participants = ", ".join(str(item).strip() for item in list(briefing.get("participants") or []) if str(item).strip())
        show_participants = self._is_visible(rendering_policy.get("show_overview_participants"), has_content=bool(participants), default="auto")
        if show_participants and (participants or str(rendering_policy.get("show_overview_participants") or "").strip().lower() == "always"):
            rows.append(
                (
                    self._policy_text(rendering_policy, "overview_participants_label", "Participants"),
                    participants or self._policy_text(rendering_policy, "empty_participants_message", "Unavailable"),
                )
            )
        return rows

    def _render_overview(
        self,
        heading: str,
        rows: list[tuple[str, str]],
        *,
        block_name: str,
        rendering_policy: dict[str, Any],
    ) -> str:
        if not rows:
            return ""
        overview_layout = self._overview_layout(rendering_policy)
        if overview_layout == "inline":
            content_html = (
                '<div class="overview-inline">'
                + "".join(
                    '<div class="overview-chip">'
                    f'<span class="overview-chip-label">{html.escape(label)}</span>'
                    f'<span class="overview-chip-value">{html.escape(value)}</span>'
                    "</div>"
                    for label, value in rows
                )
                + "</div>"
            )
        elif overview_layout == "stack":
            content_html = (
                '<div class="overview-stack">'
                + "".join(
                    '<div class="overview-row">'
                    f'<div class="overview-label">{html.escape(label)}</div>'
                    f'<div class="overview-value">{html.escape(value)}</div>'
                    "</div>"
                    for label, value in rows
                )
                + "</div>"
            )
        else:
            grid_html = "".join(
                f'<div class="overview-label">{html.escape(label)}</div>'
                f'<div class="overview-value">{html.escape(value)}</div>'
                for label, value in rows
            )
            content_html = f'<div class="overview-grid">{grid_html}</div>'
        return (
            self._block_open_tag(
                block_name=block_name,
                classes=["block", "panel-block", "overview-block", f"overview-layout-{overview_layout}"],
            )
            + f'<h2 class="sub-heading">{html.escape(heading)}</h2>'
            + f'<div class="overview-card">{content_html}</div>'
            + "</section>"
        )

    def _render_prose_block(
        self,
        *,
        heading: str,
        paragraphs: list[str],
        block_name: str,
        rendering_policy: dict[str, Any],
    ) -> str:
        cleaned = [item.strip() for item in paragraphs if str(item).strip()]
        if not cleaned:
            return ""
        prose = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in cleaned)
        return (
            self._block_open_tag(
                block_name=block_name,
                classes=["block", "panel-block", "prose-block", "narrative-block"],
            )
            + f'<h2 class="sub-heading">{html.escape(heading)}</h2>'
            + f'<div class="prose">{prose}</div>'
            + "</section>"
        )

    def _render_sections_block(
        self,
        *,
        heading: str,
        sections: list[dict[str, Any]],
        visuals: list[dict[str, Any]],
        block_name: str,
        rendering_policy: dict[str, Any],
        raised_by_label: str,
        speakers_label: str,
        timestamps_label: str,
        numbering_mode: str,
        show_raised_by: bool,
        show_speakers: bool,
        show_timestamps: bool,
        empty_summary_message: str,
    ) -> str:
        if not sections:
            return ""
        numbered = numbering_mode != "plain"
        section_style = self._section_style(rendering_policy)
        content: list[str] = [
            self._block_open_tag(
                block_name=block_name,
                classes=["block", "panel-block", "section-block", f"section-style-{section_style}"],
            ),
            f'<h2 class="sub-heading">{html.escape(heading)}</h2>',
        ]
        for index, section in enumerate(sections, start=1):
            section_heading = str(section.get("heading") or f"Topic {index}").strip() or f"Topic {index}"
            section_summary = str(section.get("summary") or "").strip()
            content.append('<article class="meeting-section">')
            heading_text = f"{index}. {section_heading}" if numbered else section_heading
            content.append(f'<h3 class="meeting-section-title">{html.escape(heading_text)}</h3>')
            content.append(
                f'<div class="prose"><p>{html.escape(section_summary or empty_summary_message)}</p></div>'
            )
            trace_lines: list[str] = []
            raised_by = str(section.get("raised_by") or "").strip()
            if show_raised_by and raised_by:
                trace_lines.append(f"<div><b>{html.escape(raised_by_label)}:</b> {html.escape(raised_by)}</div>")
            speakers = section.get("speakers")
            speaker_text = ", ".join(str(item).strip() for item in speakers if str(item).strip()) if isinstance(speakers, list) else str(speakers or "").strip()
            if show_speakers and speaker_text:
                trace_lines.append(f"<div><b>{html.escape(speakers_label)}:</b> {html.escape(speaker_text)}</div>")
            timestamps = section.get("timestamp_refs") or []
            timestamp_text = ", ".join(str(item).strip() for item in timestamps if str(item).strip())
            if show_timestamps and timestamp_text:
                trace_lines.append(f"<div><b>{html.escape(timestamps_label)}:</b> {html.escape(timestamp_text)}</div>")
            if trace_lines:
                content.append(f'<div class="trace">{"".join(trace_lines)}</div>')
            matched = self._find_visuals_for_heading(visuals, section_heading)
            for visual in matched:
                visual["_consumed"] = True
                content.append(self._render_visual_card(visual))
            content.append("</article>")
        content.append("</section>")
        return "".join(content)

    def _render_item_list_block(
        self,
        heading: str,
        items: list[Any],
        *,
        empty_message: str = "",
        block_name: str,
        rendering_policy: dict[str, Any],
    ) -> str:
        normalized = [self._normalize_list_item(item) for item in items]
        normalized = [item for item in normalized if item]
        list_style = self._list_style(rendering_policy)
        if not normalized:
            if not empty_message:
                return ""
            return (
                self._block_open_tag(
                    block_name=block_name,
                    classes=["block", "panel-block", "list-block", f"list-style-{list_style}", "empty-block"],
                )
                + f'<h2 class="sub-heading">{html.escape(heading)}</h2>'
                + f'<div class="empty-state">{html.escape(empty_message)}</div>'
                + "</section>"
            )
        list_html = "".join(f"<li>{item}</li>" for item in normalized)
        return (
            self._block_open_tag(
                block_name=block_name,
                classes=["block", "panel-block", "list-block", f"list-style-{list_style}"],
            )
            + f'<h2 class="sub-heading">{html.escape(heading)}</h2>'
            + f'<ul>{list_html}</ul>'
            + "</section>"
        )

    def _render_postprocess_block(
        self,
        *,
        heading: str,
        requests: list[dict[str, str]],
        visuals: list[dict[str, Any]],
        empty_message: str = "",
        block_name: str,
        rendering_policy: dict[str, Any],
    ) -> str:
        list_style = self._list_style(rendering_policy)
        body_parts: list[str] = []
        if requests:
            request_html = "".join(f"<li>{self._normalize_postprocess_item(item)}</li>" for item in requests)
            if request_html:
                body_parts.append(f'<ul class="postprocess-list">{request_html}</ul>')
        if visuals:
            body_parts.append("".join(self._render_visual_card(visual) for visual in visuals))
        if not body_parts:
            if not empty_message:
                return ""
            body_parts.append(f'<div class="empty-state">{html.escape(empty_message)}</div>')
        return (
            self._block_open_tag(
                block_name=block_name,
                classes=["block", "panel-block", "postprocess-block", "list-block", f"list-style-{list_style}", "appendix"],
            )
            + f'<h2 class="sub-heading">{html.escape(heading)}</h2>'
            + f'{"".join(body_parts)}</section>'
        )

    def _render_memo_block(self, heading: str, text: str, *, block_name: str, rendering_policy: dict[str, Any]) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        return (
            self._block_open_tag(
                block_name=block_name,
                classes=["block", "panel-block", "memo-block", "narrative-block"],
            )
            + f'<h2 class="sub-heading">{html.escape(heading)}</h2>'
            + f'<div class="memo-text">{html.escape(cleaned)}</div>'
            + "</section>"
        )

    def _render_visual_card(self, visual: dict[str, Any]) -> str:
        image_uri = html.escape(str(visual.get("uri") or ""))
        return (
            '<figure class="visual-asset">'
            + (f'<img class="visual-image" src="{image_uri}" alt="meeting visual" />' if image_uri else "")
            + "</figure>"
        )

    def _compose_style(self, *, rendering_policy: dict[str, Any], embedded_font_css: str = "") -> str:
        css = self._load_text(self._report_css_path, _DEFAULT_REPORT_CSS)
        band_fill = self._css_color(
            rendering_policy.get("renderer_section_band_fill_color") or rendering_policy.get("renderer_table_header_fill_color"),
            "transparent",
        )
        section_panel_fill = self._css_color(rendering_policy.get("renderer_section_panel_fill_color"), "transparent")
        section_accent_fill = self._css_color(
            rendering_policy.get("renderer_section_accent_fill_color") or rendering_policy.get("renderer_accent_color"),
            "transparent",
        )
        overview_panel_fill = self._css_color(rendering_policy.get("renderer_overview_panel_fill_color"), "transparent")
        overview_label_fill = self._css_color(
            rendering_policy.get("renderer_overview_label_fill_color") or rendering_policy.get("renderer_table_label_fill_color"),
            "#eef9f3",
        )
        cover_fill = self._css_color(
            rendering_policy.get("renderer_cover_fill_color") or rendering_policy.get("renderer_primary_color"),
            "#f4faf7",
        )
        cover_align = str(rendering_policy.get("renderer_cover_align") or "").strip().lower()
        title_font = rendering_policy.get("renderer_title_font")
        heading_font = rendering_policy.get("renderer_heading_font")
        body_font = rendering_policy.get("renderer_body_font")
        variables = {
            "--page-background": "#ffffff",
            "--primary-color": self._css_color(rendering_policy.get("renderer_primary_color"), "#03C75A"),
            "--accent-color": self._css_color(rendering_policy.get("renderer_accent_color"), "#2B8A63"),
            "--neutral-color": self._css_color(rendering_policy.get("renderer_neutral_color"), "#f3f7f5"),
            "--surface-tint": self._css_color(rendering_policy.get("renderer_surface_tint_color"), "#ffffff"),
            "--title-color": self._css_color(rendering_policy.get("renderer_heading1_color") or rendering_policy.get("renderer_primary_color"), "#03C75A"),
            "--heading2-color": self._css_color(rendering_policy.get("renderer_heading2_color") or rendering_policy.get("renderer_primary_color"), "#03C75A"),
            "--heading3-color": self._css_color(rendering_policy.get("renderer_heading3_color") or rendering_policy.get("renderer_primary_color"), "#1F5E43"),
            "--body-color": self._css_color(rendering_policy.get("renderer_body_text_color"), "#1f2933"),
            "--muted-color": self._css_color(rendering_policy.get("renderer_muted_text_color"), "#4b5563"),
            "--section-border": self._css_color(rendering_policy.get("renderer_section_border_color"), "#d7e8de"),
            "--overview-label-fill": overview_label_fill,
            "--overview-value-fill": self._css_color(rendering_policy.get("renderer_overview_value_fill_color"), "#ffffff"),
            "--overview-panel-fill": overview_panel_fill,
            "--kicker-fill": self._css_color(rendering_policy.get("renderer_kicker_fill_color") or rendering_policy.get("renderer_primary_color"), "#03C75A"),
            "--kicker-text": self._css_color(rendering_policy.get("renderer_kicker_text_color"), "#ffffff"),
            "--title-font": self._css_font(title_font),
            "--heading-font": self._css_font(heading_font),
            "--body-font": self._css_font(body_font),
            "--page-top-margin": self._css_inches(rendering_policy.get("renderer_page_top_margin_inches"), "0.62in"),
            "--page-bottom-margin": self._css_inches(rendering_policy.get("renderer_page_bottom_margin_inches"), "0.68in"),
            "--page-left-margin": self._css_inches(rendering_policy.get("renderer_page_left_margin_inches"), "0.66in"),
            "--page-right-margin": self._css_inches(rendering_policy.get("renderer_page_right_margin_inches"), "0.66in"),
            "--body-line-height": self._css_number(rendering_policy.get("renderer_body_line_spacing"), "1.7"),
            "--list-line-height": self._css_number(rendering_policy.get("renderer_list_line_spacing") or rendering_policy.get("renderer_body_line_spacing"), "1.7"),
            "--cover-fill": cover_fill,
            "--cover-border-width": "1px" if cover_fill != "transparent" else "0px",
            "--cover-text-align": self._css_text_align(cover_align),
            "--cover-items-align": self._css_flex_align(cover_align),
            "--title-divider-color": self._css_color(rendering_policy.get("renderer_title_divider_color") or rendering_policy.get("renderer_primary_color"), "#03C75A"),
            "--title-divider-size": self._css_pt(rendering_policy.get("renderer_title_divider_size"), "3pt"),
            "--title-divider-space": self._css_pt(rendering_policy.get("renderer_title_divider_space"), "8pt"),
            "--title-space-after": self._css_pt(rendering_policy.get("renderer_title_space_after_pt"), "6pt"),
            "--block-gap": self._css_pt(rendering_policy.get("renderer_block_gap_pt"), "10pt"),
            "--panel-radius": self._css_pt(rendering_policy.get("renderer_panel_radius_pt"), "12pt"),
            "--cover-radius": self._css_pt(rendering_policy.get("renderer_cover_radius_pt"), "16pt"),
            "--heading-chip-radius": self._css_pt(rendering_policy.get("renderer_heading_chip_radius_pt"), "999pt"),
            "--overview-radius": self._css_pt(rendering_policy.get("renderer_overview_radius_pt"), "10pt"),
            "--heading2-space-before": self._css_pt(rendering_policy.get("renderer_heading2_space_before_pt"), "0pt"),
            "--heading2-space-after": self._css_pt(rendering_policy.get("renderer_heading2_space_after_pt"), "8pt"),
            "--heading3-space-before": self._css_pt(rendering_policy.get("renderer_heading3_space_before_pt"), "0pt"),
            "--heading3-space-after": self._css_pt(rendering_policy.get("renderer_heading3_space_after_pt"), "6pt"),
            "--section-band-fill": band_fill,
            "--section-band-padding-x": "10px" if band_fill != "transparent" else "0px",
            "--section-band-padding-y": "5px" if band_fill != "transparent" else "0px",
            "--section-band-radius": "999px" if band_fill != "transparent" else "0px",
            "--section-panel-fill": section_panel_fill,
            "--section-panel-padding": "14px 16px" if section_panel_fill != "transparent" else "0px",
            "--section-panel-border-width": "1px" if section_panel_fill != "transparent" else "0px",
            "--section-accent-fill": section_accent_fill,
            "--overview-panel-padding": "10px" if overview_panel_fill != "transparent" else "0px",
            "--overview-panel-border-width": "1px" if overview_panel_fill != "transparent" else "0px",
            "--postprocess-image-width": self._css_inches(rendering_policy.get("postprocess_image_width_inches"), "5.90in"),
        }
        variable_block = ":root {\n" + "\n".join(f"  {key}: {value};" for key, value in variables.items()) + "\n}\n\n"
        font_block = str(embedded_font_css or "").strip()
        custom_css = str(rendering_policy.get("renderer_custom_css") or "").strip()
        return (font_block + "\n\n" if font_block else "") + variable_block + css + (("\n\n" + custom_css) if custom_css else "")

    def _prepare_font_resources(self, *, rendering_policy: dict[str, Any], base_dir: Path) -> dict[str, Any]:
        stylesheet_urls = stylesheet_import_urls_for_fonts(
            rendering_policy.get("renderer_title_font"),
            rendering_policy.get("renderer_heading_font"),
            rendering_policy.get("renderer_body_font"),
        )
        if not stylesheet_urls:
            return {"stylesheets": [], "css": "", "assets": [], "warnings": []}

        asset_dir = base_dir / ".html-font-assets"
        css_blocks: list[str] = []
        asset_records: list[dict[str, str]] = []
        warnings: list[str] = []

        for stylesheet_url in stylesheet_urls:
            try:
                css_text = self._download_url_text(stylesheet_url)
                rewritten_css, records = self._materialize_stylesheet_assets(
                    stylesheet_url=stylesheet_url,
                    css_text=css_text,
                    asset_dir=asset_dir,
                    html_dir=base_dir,
                )
                cleaned = rewritten_css.strip()
                if cleaned:
                    css_blocks.append(cleaned)
                asset_records.extend(records)
            except Exception as exc:
                warnings.append(f"Font embedding fallback for {stylesheet_url}: {exc}")
                css_blocks.append(f'@import url("{stylesheet_url}");')

        deduped_assets: list[dict[str, str]] = []
        seen_asset_paths: set[str] = set()
        for item in asset_records:
            path_key = str(item.get("path") or "")
            if not path_key or path_key in seen_asset_paths:
                continue
            seen_asset_paths.add(path_key)
            deduped_assets.append(item)

        return {
            "stylesheets": stylesheet_urls,
            "css": "\n\n".join(css_blocks),
            "assets": deduped_assets,
            "warnings": warnings,
        }

    def _materialize_stylesheet_assets(
        self,
        *,
        stylesheet_url: str,
        css_text: str,
        asset_dir: Path,
        html_dir: Path,
    ) -> tuple[str, list[dict[str, str]]]:
        asset_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, str]] = []

        def replace_url(match: re.Match[str]) -> str:
            raw_target = str(match.group("target") or "").strip().strip("\"'")
            if not raw_target or raw_target.startswith("data:"):
                return match.group(0)
            asset_url = urljoin(stylesheet_url, raw_target)
            asset_path = self._materialize_remote_asset(asset_url, asset_dir)
            relative_path = os.path.relpath(asset_path, html_dir).replace("\\", "/")
            records.append({"source_url": asset_url, "path": str(asset_path)})
            return f'url("{relative_path}")'

        rewritten = re.sub(r"url\((?P<target>[^)]+)\)", replace_url, css_text)
        rewritten = re.sub(r"@charset\s+[^;]+;\s*", "", rewritten, flags=re.IGNORECASE)
        return rewritten, records

    def _materialize_remote_asset(self, url: str, asset_dir: Path) -> Path:
        parsed = urlparse(url)
        suffix = Path(unquote(parsed.path)).suffix or ".bin"
        stem = Path(unquote(parsed.path)).stem or "asset"
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "asset"
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        asset_path = asset_dir / f"{digest}-{safe_stem}{suffix}"
        if asset_path.exists() and asset_path.stat().st_size > 0:
            return asset_path
        asset_path.write_bytes(self._download_url_bytes(url))
        return asset_path

    def _download_url_text(self, url: str) -> str:
        return self._download_url_bytes(url).decode("utf-8")

    def _download_url_bytes(self, url: str) -> bytes:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=min(self._timeout, 30.0)) as response:
            return response.read()

    def _appendix_visuals(self, visuals: list[dict[str, Any]], *, section_headings: set[str], sections_will_render: bool) -> list[dict[str, Any]]:
        appendix_visuals: list[dict[str, Any]] = []
        for visual in visuals:
            if visual.get("_consumed"):
                continue
            target_heading = str(visual.get("target_heading") or "").strip()
            if sections_will_render and target_heading and target_heading in section_headings:
                continue
            appendix_visuals.append(visual)
        return appendix_visuals

    def _postprocess_request_items(self, requests: list[dict[str, Any]]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for request in requests:
            if str(request.get("image_path") or "").strip():
                continue
            title = str(request.get("title") or "").strip()
            instruction = str(request.get("instruction") or "").strip()
            placement_notes = str(request.get("placement_notes") or "").strip()
            summary = f"{instruction} ({placement_notes})" if instruction and placement_notes else (instruction or placement_notes)
            if title or summary:
                items.append({"heading": title, "summary": summary})
        return items

    def _normalize_postprocess_item(self, item: dict[str, str]) -> str:
        heading = html.escape(str(item.get("heading") or "").strip())
        summary = html.escape(str(item.get("summary") or "").strip())
        if heading and summary:
            return f"<strong>{heading}</strong>: {summary}"
        if heading:
            return heading
        return summary

    def _policy_text(self, rendering_policy: dict[str, Any], key: str, fallback: str) -> str:
        return str(rendering_policy.get(key) or "").strip() or fallback

    def _is_visible(self, value: Any, *, has_content: bool, default: str) -> bool:
        normalized = str(value or default).strip().lower()
        if normalized == "never":
            return False
        if normalized == "auto":
            return has_content
        return True

    def _resolve_visual_assets(self, requests: list[dict[str, Any]], *, base_dir: Path) -> list[dict[str, Any]]:
        visuals: list[dict[str, Any]] = []
        for request in requests:
            image_path = str(request.get("image_path") or "").strip()
            if not image_path:
                continue
            path = Path(image_path)
            if not path.is_absolute():
                path = (base_dir / image_path).resolve()
            path = self._prefer_raw_visual_path(path)
            if not path.exists():
                continue
            visuals.append(
                {
                    "title": str(request.get("title") or "").strip(),
                    "caption": str(request.get("caption") or "").strip(),
                    "instruction": str(request.get("instruction") or "").strip(),
                    "target_heading": str(request.get("target_heading") or "").strip(),
                    "placement_notes": str(request.get("placement_notes") or "").strip(),
                    "path": str(path),
                    "uri": path.resolve().as_uri(),
                }
            )
        return visuals

    def _prefer_raw_visual_path(self, path: Path) -> Path:
        name = path.name
        match = re.match(r"^(?P<stem>.+)-card-(?P<index>\d+)(?P<suffix>\.[^.]+)$", name)
        if not match:
            return path
        raw_candidate = path.with_name(f"{match.group('stem')}{match.group('suffix')}")
        if raw_candidate.exists():
            return raw_candidate
        return path

    def _find_visuals_for_heading(self, visuals: list[dict[str, Any]], heading: str) -> list[dict[str, Any]]:
        heading_text = str(heading or "").strip()
        matched: list[dict[str, Any]] = []
        for visual in visuals:
            if visual.get("_consumed"):
                continue
            if str(visual.get("target_heading") or "").strip() == heading_text:
                matched.append(visual)
        return matched

    def _normalize_list_item(self, item: Any) -> str:
        if isinstance(item, str):
            cleaned = item.strip()
            if not cleaned:
                return ""
            parsed = self._parse_serialized_list_item(cleaned)
            if parsed is not None:
                return self._normalize_list_item(parsed)
            return html.escape(cleaned)
        if isinstance(item, list):
            nested_items = [self._normalize_list_item(entry) for entry in item]
            nested_items = [entry for entry in nested_items if entry]
            return "<br />".join(nested_items)
        if isinstance(item, dict):
            heading = str(item.get("heading") or item.get("title") or "").strip()
            summary = str(item.get("summary") or item.get("description") or "").strip()
            if heading and summary:
                return f"<strong>{html.escape(heading)}</strong>: {html.escape(summary)}"
            if heading:
                return html.escape(heading)
            if summary:
                return html.escape(summary)
            decision_text = str(item.get("decision") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
            if decision_text and rationale:
                return f"<strong>{html.escape(decision_text)}</strong>: {html.escape(rationale)}"
            if decision_text:
                return html.escape(decision_text)
            action_text = str(item.get("item") or item.get("action") or "").strip()
            owner = str(item.get("owner") or "").strip()
            if action_text and owner and not self._is_placeholder_owner(owner):
                return f"{html.escape(action_text)} ({html.escape(owner)})"
            if action_text:
                return html.escape(action_text)
            primary_text = str(
                item.get("question")
                or item.get("signal")
                or item.get("text")
                or item.get("message")
                or item.get("content")
                or ""
            ).strip()
            if primary_text:
                return html.escape(primary_text)
        return ""

    def _parse_serialized_list_item(self, text: str) -> Any | None:
        if not text or text[0] not in "{[":
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                return None
        return parsed if isinstance(parsed, (dict, list, str)) else None

    def _is_placeholder_owner(self, value: str) -> bool:
        normalized = str(value or "").strip().lower()
        return normalized in {
            "\ubbf8\uc815",
            "\ubbf8\uc9c0\uc815",
            "\uc5c6\uc74c",
            "tbd",
            "unknown",
            "unassigned",
            "n/a",
            "na",
            "-",
        }

    def _load_text(self, path: Path, fallback: str) -> str:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
        except Exception:
            pass
        return fallback

    def _resolve_browser(self) -> str | None:
        configured = os.getenv("DELEGATE_HTML_PDF_BROWSER_PATH", "").strip()
        if configured and Path(configured).exists():
            return configured
        for command in ("chrome", "msedge", "chromium"):
            discovered = shutil.which(command)
            if discovered:
                return discovered
        for candidate in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ):
            if Path(candidate).exists():
                return candidate
        return None

    def _css_color(self, value: Any, fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            return fallback
        if text == "transparent" or text.startswith("#"):
            return text
        return f"#{text}"

    def _css_font(self, value: Any) -> str:
        return expand_css_font_stack(value, fallback_kind="serif" if font_prefers_serif(value) else "sans")

    def _cover_layout(self, rendering_policy: dict[str, Any]) -> str:
        return self._style_choice(
            rendering_policy.get("renderer_cover_layout"),
            fallback="panel",
            aliases={
                "panel": {"panel", "card", "boxed"},
                "minimal": {"minimal", "plain", "bare"},
                "split": {"split", "two-column", "two_column", "side-meta"},
            },
        )

    def _cover_background_style(self, rendering_policy: dict[str, Any]) -> str:
        return self._style_choice(
            rendering_policy.get("renderer_cover_background_style"),
            fallback="gradient",
            aliases={
                "gradient": {"gradient", "soft", "blend"},
                "solid": {"solid", "flat"},
                "minimal": {"minimal", "plain", "none"},
            },
        )

    def _panel_style(self, rendering_policy: dict[str, Any]) -> str:
        return self._style_choice(
            rendering_policy.get("renderer_panel_style"),
            fallback="soft",
            aliases={
                "soft": {"soft", "card", "rounded", "soft-card", "soft_card"},
                "sharp": {"sharp", "angular", "outline", "sharp-outline", "sharp_outline"},
                "minimal": {"minimal", "plain", "bare", "none"},
            },
        )

    def _heading_style(self, rendering_policy: dict[str, Any]) -> str:
        return self._style_choice(
            rendering_policy.get("renderer_heading_style"),
            fallback="chip",
            aliases={
                "chip": {"chip", "pill", "capsule", "tag"},
                "underline": {"underline", "line", "rule"},
                "plain": {"plain", "text", "minimal"},
                "band": {"band", "bar", "block"},
            },
        )

    def _overview_layout(self, rendering_policy: dict[str, Any]) -> str:
        return self._style_choice(
            rendering_policy.get("renderer_overview_layout"),
            fallback="grid",
            aliases={
                "grid": {"grid", "table", "matrix"},
                "inline": {"inline", "chips", "chip"},
                "stack": {"stack", "stacked", "list"},
            },
        )

    def _section_style(self, rendering_policy: dict[str, Any]) -> str:
        return self._style_choice(
            rendering_policy.get("renderer_section_style"),
            fallback="accent",
            aliases={
                "accent": {"accent", "accent-bar", "accent_bar", "rail"},
                "divider": {"divider", "rule", "separated"},
                "minimal": {"minimal", "plain", "bare"},
            },
        )

    def _list_style(self, rendering_policy: dict[str, Any]) -> str:
        return self._style_choice(
            rendering_policy.get("renderer_list_style"),
            fallback="panel",
            aliases={
                "panel": {"panel", "card", "default"},
                "divider": {"divider", "rule", "lined"},
                "minimal": {"minimal", "plain", "bare"},
            },
        )

    def _style_choice(self, value: Any, *, fallback: str, aliases: dict[str, set[str]]) -> str:
        text = str(value or "").strip().lower().replace("_", "-")
        if not text:
            return fallback
        for canonical, options in aliases.items():
            normalized_options = {item.replace("_", "-") for item in options}
            if text in normalized_options:
                return canonical
        return fallback

    def _css_text_align(self, value: str) -> str:
        if value in {"center", "middle"}:
            return "center"
        if value in {"right", "end"}:
            return "right"
        return "left"

    def _css_flex_align(self, value: str) -> str:
        if value in {"center", "middle"}:
            return "center"
        if value in {"right", "end"}:
            return "flex-end"
        return "flex-start"

    def _css_inches(self, value: Any, fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            return fallback
        if text.endswith("in"):
            return text
        try:
            return f"{float(text):.2f}in"
        except ValueError:
            return fallback

    def _css_number(self, value: Any, fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            return fallback
        try:
            return str(float(text))
        except ValueError:
            return fallback

    def _css_pt(self, value: Any, fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            return fallback
        if text.endswith("pt"):
            return text
        try:
            return f"{float(text):.2f}pt"
        except ValueError:
            return fallback
