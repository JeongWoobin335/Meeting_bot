---
name: meeting-output-default
description: ZOOM_MEETING_BOT의 기본 결과물 생성 스킬. 회의 세션을 브리핑 JSON, Markdown, DOCX, PDF 결과물로 가공할 때 쓰는 기준본이다.
metadata:
  result_generation_scope: summary, briefing, markdown, pdf, postprocess
  result_block_order: overview, executive_summary, sections, decisions, action_items, open_questions, memo
  result_block_order_mode: exact
  renderer_profile: default
  renderer_cover_layout: minimal
  renderer_cover_background_style: minimal
  renderer_panel_style: minimal
  renderer_heading_style: underline
  renderer_overview_layout: stack
  renderer_section_style: minimal
  renderer_list_style: minimal
  renderer_primary_color: 245d44
  renderer_accent_color: 245d44
  renderer_heading1_color: 245d44
  renderer_heading2_color: 245d44
  renderer_heading3_color: 245d44
  renderer_body_text_color: 1f2933
  renderer_muted_text_color: 4b5563
  renderer_section_border_color: d7e0da
  renderer_title_divider_color: 245d44
  renderer_block_gap_pt: 8
  renderer_title_divider_size: 2
  renderer_title_divider_space: 8
  postprocess_image_width_inches: 5.9
  show_title: always
  show_overview: always
  show_executive_summary: always
  show_sections: always
  show_decisions: always
  show_action_items: always
  show_open_questions: always
  show_risk_signals: never
  show_memo: always
  show_overview_datetime: always
  show_overview_author: always
  show_overview_session_id: always
  show_overview_participants: always
  section_numbering: numbered
  overview_heading: 회의 개요
  overview_datetime_label: 회의 일시
  overview_author_label: 작성 주체
  overview_session_id_label: 세션 ID
  overview_participants_label: 참석자
  executive_summary_heading: 회의 전체 요약
  sections_heading: 핵심 논의 주제
  decisions_heading: 결정사항
  action_items_heading: 액션 아이템
  open_questions_heading: 열린 질문
  risk_signals_heading: 리스크 신호
  postprocess_requests_heading: 추가 결과물 제안
  memo_heading: 메모
  empty_executive_summary_message: 회의 전체 요약이 아직 생성되지 않았습니다.
  empty_sections_message: 핵심 논의 주제가 아직 정리되지 않았습니다.
  empty_decisions_message: 아직 확정된 결정사항이 없습니다.
  empty_action_items_message: 추출된 액션 아이템이 없습니다.
  empty_open_questions_message: 현재 추가 열린 질문이 없습니다.
  empty_risk_signals_message: 현재 강조할 리스크 신호가 없습니다.
  empty_postprocess_requests_message: 현재 추가 결과물 제안이 없습니다.
  empty_participants_message: 미확인
  empty_section_summary_message: 요약 내용이 없습니다.
  memo_text: 세부 음성 전사와 채팅 원문은 별도 export 파일에서 확인할 수 있습니다.
---

# 기본 결과물 생성 스킬

이 파일은 `ZOOM_MEETING_BOT`가 회의 결과물을 어떤 방식으로 생성하고 정리해야 하는지 알려주는 기본 기준본이다.

이 기준본은 가능한 한 `0의 상태`에 가깝게 유지한다.

- 기본값은 텍스트 흐름과 얇은 경계 중심의 중립적 문서여야 한다.
- 카드, 캡슐, 강한 배경면, 강한 그라데이션 같은 시각 취향은 기본이 아니라 오버라이드 스킬에서 추가되어야 한다.
- 사용자가 별도 스킬에서 더 강한 레이아웃, 도형, 브랜드 표현을 요구할 때 그 위에 확장되는 구조를 전제로 한다.

```css
.cover-meta {
  display: none;
}

.cover {
  margin-bottom: var(--block-gap, 8pt);
}

.cover-title {
  max-width: 100%;
}
```

중요한 점:

- 이 스킬은 회의를 수집하거나 전사하는 엔진을 바꾸는 파일이 아니다.
- 이 스킬은 결과물 생성 단계에서 AI가 무엇을 더 중요하게 보고, 어떤 순서로 보여주고, 어떤 후속 처리를 제안할지 정하는 파일이다.
- 사용자는 이 기준본을 그대로 쓸 수도 있고, 자신의 회의 스타일에 맞게 다른 스킬로 확장할 수도 있다.

## 이 스킬이 다루는 범위

- 제목을 어떤 방식으로 쓸지
- 회의 전체 요약을 얼마나 강조할지
- 섹션을 어떻게 나눌지
- 결정사항, 액션 아이템, 열린 질문, 리스크 신호를 어떤 성격으로 작성할지
- 최종 문서에서 어떤 블록을 어떤 순서로 배치할지
- 추가 시각 자료나 별도 렌더링 방향을 어떤 식으로 반영할지

## 기본 작성 원칙

- 한국어 문어체로 자연스럽게 쓴다.
- 날것 전사 문장을 그대로 복사하지 않는다.
- 실제 회의의 핵심 주제, 결정 방향, 남은 질문을 우선한다.
- 보조 예시나 부수 산출물을 회의 전체 핵심 의제로 과장하지 않는다.
- 가능하면 구체 예시, 이름, 개념, 이유를 한 가지 이상 살린다.
- 긴 회의나 개념 설명형 회의를 짧은 양식에 맞추려고 과하게 압축하지 않는다.
- 최소 결과물은 안정적으로 유지하되, 사용자 스킬이 그 위에서 더 발전할 수 있게 열어둔다.

## 필드별 작성 기준

### `title`

- 짧고 명확한 회의 제목으로 쓴다.
- 문장형 설명보다 핵심 명사구에 가깝게 쓴다.

### `executive_summary`

- PDF 맨 위에 들어가는 가장 응축된 요약이다.
- 회의 전체 공통축, 결정 방향, 남은 긴장을 먼저 보여준다.
- 짧은 회의라면 짧게 끝나도 되지만, 긴 회의나 설명형 회의라면 여러 문장으로 더 풍부하게 써도 된다.
- 특정 섹션의 보조 예시를 회의 전체 핵심처럼 과장하지 않는다.

### `summary`

- `executive_summary`보다 한 단계 더 풍부한 연결 요약이다.
- 왜 이 회의가 중요했는지, 어떤 예시가 나왔는지, 어떤 방향으로 정리됐는지까지 담는다.
- 고정된 문장 수를 먼저 맞추기보다, 회의의 실제 밀도와 복잡도에 맞게 자연스럽게 쓴다.

### `sections`

- 실제 회의 구조에 맞게 핵심 논의 주제를 나눈다.
- 짧은 회의는 몇 개의 큰 덩어리로 묶어도 되고, 긴 회의는 더 많은 섹션으로 나눠도 된다.
- 각 섹션은 한두 문장으로 끝날 수도 있고, 해설형 브리핑처럼 더 길게 풀어쓸 수도 있다.
- 가능하면 예시, 비교, 개념, 이유 같은 구체 디테일을 하나 이상 남긴다.

### `action_items`, `decisions`, `open_questions`, `risk_signals`

- 실제 회의 근거가 있는 내용만 쓴다.
- 추상적인 슬로건보다 실행 가능하거나 확인 가능한 문장으로 쓴다.
- 내부 JSON key가 `decisions`여도, 다른 스킬이 이 블록을 `검토사항`, `논의 포인트`, `판단 메모`처럼 다시 정의하면 그 의미에 맞게 내용을 작성할 수 있다.

## 결과물 배치 정책

- `result_block_order`는 최종 Markdown/PDF에서 어떤 블록을 어떤 순서로 보여줄지 정한다.
- `result_block_order_mode`는 사용자가 적지 않은 기본 블록을 뒤에 보강할지(`append_missing`), 사용자가 지정한 블록만 정확히 보여줄지(`exact`) 정한다.
- `show_*` 계열 값은 블록 자체를 항상 보일지, 자동으로 보일지, 숨길지 정한다.
- `max_*` 계열 값은 꼭 필요할 때만 각 블록의 상한을 잡는 용도이며, 기본 기준본은 모든 회의를 똑같은 분량으로 잘라내는 것을 목표로 하지 않는다.
- `*_heading` 값은 사용자에게 보이는 최종 헤더 이름을 바꾼다.
- `empty_*` 값은 블록이 비었을 때 보여줄 문구를 바꾼다.
- `section_numbering`은 섹션 제목 앞에 번호를 붙일지(`numbered`) 번호 없이 제목만 보여줄지(`plain`) 정한다.
- `제기자`, `주요 화자`, `타임스탬프`는 시스템 핵심 추적 정보이므로 skill에서 숨기거나 이름을 바꾸지 않는다.
- `show_overview_*` 계열 값은 회의 일시, 작성 주체, 세션 ID, 참석자 같은 개요 항목을 각각 보일지 숨길지 정한다.

## 추가 결과물과 렌더링

- `renderer_profile`은 최종 DOCX/PDF의 기본 출발점을 정하는 힌트다. 꼭 고정된 메뉴처럼 쓸 필요는 없고, 더 직접적인 색/폰트/표면 처리 지시가 있으면 그쪽을 우선해도 된다.
- `renderer_theme_name`은 사용자가 원하는 브랜드나 무드의 이름을 짧게 담는 값이다.
- `renderer_primary_color`, `renderer_accent_color`, `renderer_neutral_color`는 더 구체적인 브랜드 느낌이 필요할 때 색 방향을 잡는 값이다.
- `renderer_title_font`, `renderer_heading_font`, `renderer_body_font`는 브랜드나 무드에 맞는 문서용 글꼴 방향을 정할 때 쓸 수 있다.
- 필요하면 `renderer_cover_fill_color`, `renderer_section_panel_fill_color`, `renderer_section_accent_fill_color`, `renderer_overview_panel_fill_color`, `renderer_title_divider_mode`처럼 더 직접적인 문서 표면 처리 값을 써서 결과물 디자인을 세밀하게 풀 수 있다.
- `postprocess_image_width_inches`는 후속 이미지가 DOCX/PDF에 삽입될 때의 폭을 정한다.
- 사용자가 `카카오 느낌`, `조용한 공공기관 문서 느낌`, `따뜻한 스타트업 브리핑 느낌`처럼 말하면, 그 의도를 이런 렌더링 힌트로 옮길 수 있다.
- 필요하다면 회사나 브랜드 무드에 맞는 색/폰트/디자인 힌트를 더 구체적으로 풀어낼 수 있다.
- `postprocess_requests`는 최종 결과물에 붙일 추가 작업 힌트를 위한 블록이다.
- 예를 들어 이미지 생성 브리프, 별첨 제안, 요약 결과물의 시각 자료 방향 같은 요청을 이 블록에 담을 수 있다.
- 이 블록에는 `nano-banana` 같은 도구 힌트나, 결과물에 어떤 이미지를 몇 장 정도 붙이고 싶은지 같은 의도도 담을 수 있다.
- 실제 이미지 파일이 이미 준비되면, 그 경로와 캡션을 추가 결과물 요청에 함께 담아 최종 DOCX/PDF에 시각 자료로 붙일 수 있다.
- 이 블록은 기본값으로는 숨겨져 있지만, 다른 스킬에서 켜고 제목이나 개수를 바꿀 수 있다.

## 블록 의미 재정의

- 사용자에게 보이는 블록 이름과 그 블록이 실제로 담아야 하는 의미는 스킬에서 다시 정의할 수 있다.
- 예를 들어 `열린 질문`을 `검토사항`으로 바꾸고 싶다면, 이름만 바꾸는 데서 끝나지 않고 그 블록 안의 내용도 검토 중인 사항, 판단 포인트, 추가 확인이 필요한 항목 중심으로 작성되게 만들 수 있다.
- 같은 방식으로 `메모`를 숨기거나, `결정사항`을 `논의 포인트`처럼 더 넓은 성격으로 재해석하는 것도 가능하다.
- 중요한 것은 내부 엔진 계약이 아니라, 사용자에게 보이는 결과물의 의미와 표현을 스킬이 바꾸는 것이다.

## 확장 원칙

- 이 기준본은 안전한 시작점이지 감옥이 아니다.
- 사용자는 자신의 스킬을 통해 블록 이름, 블록 의미, 배치, 추가 결과물, 렌더링 스타일을 더 발전시킬 수 있다.
- 같은 회의라도 요약형, 브리핑형, 해설형, 회사 맞춤형으로 다르게 발전할 수 있어야 한다.
- 다만 엔진 계약, 세션/전사/전달 파이프라인, 필수 JSON 구조는 깨지지 않아야 한다.

## Renderer CSS Overrides

- 스킬 본문에는 fenced `css` 블록을 넣을 수 있다.
- `css` 블록은 렌더러 기본 스타일시트 뒤에 그대로 붙기 때문에, 패널 외곽선, 배경, 간격, 페이지 분할 방식, 블록별 디자인 언어를 직접 덮어쓸 수 있다.
- 안정적인 블록 selector:
  - `.block-name-overview`
  - `.block-name-executive_summary`
  - `.block-name-sections`
  - `.block-name-decisions`
  - `.block-name-action_items`
  - `.block-name-open_questions`
  - `.block-name-risk_signals`
  - `.block-name-postprocess_requests`
  - `.block-name-memo`
- 각 블록은 `data-block="<block_name>"` 속성도 함께 가지므로, 스킬에서 block 단위 CSS를 직접 지정할 수 있다.
- 즉 결과물 생성 레이어의 디자인 자유도가 부족할 때는 렌더러 고정값을 더 만드는 대신, 스킬에서 직접 CSS로 여는 것이 원칙이다.
