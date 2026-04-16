from __future__ import annotations

import re
from typing import Any, Iterable


_CSS_GENERIC_FAMILIES = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-sans-serif",
    "ui-serif",
    "emoji",
    "math",
    "fangsong",
}

_DEFAULT_SANS_STACK = ("Noto Sans KR", "Nanum Gothic", "Malgun Gothic", "sans-serif")
_DEFAULT_SERIF_STACK = ("Noto Serif KR", "Batang", "serif")

_CANONICAL_FONT_STACKS: dict[str, tuple[str, ...]] = {
    "suit": ("SUIT Variable", "SUIT", "Noto Sans KR", "Malgun Gothic", "sans-serif"),
    "pretendard": (
        "Pretendard Variable",
        "Pretendard",
        "SUIT Variable",
        "SUIT",
        "Noto Sans KR",
        "Malgun Gothic",
        "sans-serif",
    ),
    "noto sans kr": ("Noto Sans KR", "Noto Sans", "Nanum Gothic", "Malgun Gothic", "sans-serif"),
    "noto serif kr": ("Noto Serif KR", "Noto Serif", "Batang", "serif"),
    "nanum gothic": ("Nanum Gothic", "Noto Sans KR", "Malgun Gothic", "sans-serif"),
    "nanum myeongjo": ("Nanum Myeongjo", "Noto Serif KR", "Batang", "serif"),
    "nanumsquareround": ("NanumSquareRound", "Nanum Gothic", "Malgun Gothic", "sans-serif"),
    "maruburi": ("MaruBuri", "Maru Buri", "Noto Serif KR", "Batang", "serif"),
    "spoqa han sans neo": ("Spoqa Han Sans Neo", "SUIT", "Noto Sans KR", "Malgun Gothic", "sans-serif"),
    "kopubworld dotum": ("KoPubWorld Dotum", "Noto Sans KR", "Malgun Gothic", "sans-serif"),
    "kopubworld batang": ("KoPubWorld Batang", "Noto Serif KR", "Batang", "serif"),
    "malgun gothic": ("Malgun Gothic", "Noto Sans KR", "sans-serif"),
    "batang": ("Batang", "Noto Serif KR", "serif"),
}

_FONT_ALIAS_MAP = {
    "suit variable": "suit",
    "suit": "suit",
    "pretendard variable": "pretendard",
    "pretendard": "pretendard",
    "noto sans kr": "noto sans kr",
    "noto sans korean": "noto sans kr",
    "본고딕": "noto sans kr",
    "source han sans kr": "noto sans kr",
    "noto serif kr": "noto serif kr",
    "noto serif korean": "noto serif kr",
    "본명조": "noto serif kr",
    "source han serif kr": "noto serif kr",
    "nanum gothic": "nanum gothic",
    "나눔고딕": "nanum gothic",
    "nanum myeongjo": "nanum myeongjo",
    "나눔명조": "nanum myeongjo",
    "nanumsquareround": "nanumsquareround",
    "nanum square round": "nanumsquareround",
    "나눔스퀘어라운드": "nanumsquareround",
    "maruburi": "maruburi",
    "maru buri": "maruburi",
    "마루부리": "maruburi",
    "spoqa han sans neo": "spoqa han sans neo",
    "스포카 한 산스 네오": "spoqa han sans neo",
    "kopubworld dotum": "kopubworld dotum",
    "kopub dotum": "kopubworld dotum",
    "코펍월드돋움": "kopubworld dotum",
    "코펍 돋움": "kopubworld dotum",
    "kopubworld batang": "kopubworld batang",
    "kopub batang": "kopubworld batang",
    "코펍월드바탕": "kopubworld batang",
    "코펍 바탕": "kopubworld batang",
    "malgun gothic": "malgun gothic",
    "맑은 고딕": "malgun gothic",
    "batang": "batang",
    "바탕": "batang",
}

_DISPLAY_NAME_BY_CANONICAL = {
    "suit": "SUIT",
    "pretendard": "Pretendard",
    "noto sans kr": "Noto Sans KR",
    "noto serif kr": "Noto Serif KR",
    "nanum gothic": "Nanum Gothic",
    "nanum myeongjo": "Nanum Myeongjo",
    "nanumsquareround": "NanumSquareRound",
    "maruburi": "MaruBuri",
    "spoqa han sans neo": "Spoqa Han Sans Neo",
    "kopubworld dotum": "KoPubWorld Dotum",
    "kopubworld batang": "KoPubWorld Batang",
    "malgun gothic": "Malgun Gothic",
    "batang": "Batang",
}

_SERIF_CANONICAL_KEYS = {"noto serif kr", "nanum myeongjo", "maruburi", "kopubworld batang", "batang"}

_CANONICAL_FONT_IMPORT_URLS: dict[str, tuple[str, ...]] = {
    "suit": ("https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/variable/woff2/SUIT-Variable.css",),
    "pretendard": (
        "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css",
    ),
    "noto sans kr": ("https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap",),
    "noto serif kr": (
        "https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400;500;600;700;800&display=swap",
    ),
    "nanum gothic": ("https://fonts.googleapis.com/css2?family=Nanum+Gothic:wght@400;700;800&display=swap",),
    "nanum myeongjo": ("https://fonts.googleapis.com/css2?family=Nanum+Myeongjo:wght@400;700;800&display=swap",),
    "spoqa han sans neo": ("https://spoqa.github.io/spoqa-han-sans/css/SpoqaHanSansNeo.css",),
}

def _normalize_font_token(value: Any) -> str:
    text = str(value or "").strip().strip("\"'").replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    normalized = text.lower()
    if normalized.endswith(" variable"):
        normalized = normalized[: -len(" variable")].strip()
    return _FONT_ALIAS_MAP.get(normalized, normalized)


def _dedupe_font_parts(parts: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        part = str(raw or "").strip()
        if not part:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(part)
    return result


def canonical_font_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for part in text.split(","):
        token = part.strip().strip("\"'")
        if not token:
            continue
        normalized = _normalize_font_token(token)
        if normalized in _DISPLAY_NAME_BY_CANONICAL:
            return _DISPLAY_NAME_BY_CANONICAL[normalized]
        lowered = token.lower()
        if lowered in _CSS_GENERIC_FAMILIES:
            continue
        return token
    return ""


def font_prefers_serif(value: Any) -> bool:
    normalized = _normalize_font_token(canonical_font_name(value) or value)
    if normalized in _SERIF_CANONICAL_KEYS:
        return True
    return any(keyword in str(value or "").lower() for keyword in ("serif", "명조", "부리", "batang"))


def stylesheet_import_urls_for_fonts(*values: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        requested_parts = [part.strip().strip("\"'") for part in str(value or "").split(",") if part.strip()]
        for token in requested_parts:
            normalized = _normalize_font_token(token)
            for url in _CANONICAL_FONT_IMPORT_URLS.get(normalized, ()):
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
    return urls


def expand_css_font_stack(value: Any, *, fallback_kind: str = "sans") -> str:
    requested_parts = [part.strip().strip("\"'") for part in str(value or "").split(",") if part.strip()]
    resolved_parts: list[str] = []
    for token in requested_parts:
        lowered = token.lower()
        if lowered in _CSS_GENERIC_FAMILIES:
            resolved_parts.append(lowered)
            continue
        canonical = _normalize_font_token(token)
        if canonical in _CANONICAL_FONT_STACKS:
            resolved_parts.extend(_CANONICAL_FONT_STACKS[canonical])
            continue
        resolved_parts.append(token)
    if not resolved_parts:
        resolved_parts.extend(_DEFAULT_SERIF_STACK if fallback_kind == "serif" else _DEFAULT_SANS_STACK)
    if not any(part.lower() in _CSS_GENERIC_FAMILIES for part in resolved_parts):
        resolved_parts.extend(_DEFAULT_SERIF_STACK if fallback_kind == "serif" else _DEFAULT_SANS_STACK)
    deduped_parts = _dedupe_font_parts(resolved_parts)
    return ", ".join(
        part if part.lower() in _CSS_GENERIC_FAMILIES else f'"{part}"'
        for part in deduped_parts
    )
