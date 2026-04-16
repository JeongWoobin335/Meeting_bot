# PDF-First HTML 렌더러 세부 설계안

작성일: 2026-04-11  
기준 문서: [pdf-first-meeting-output-system-direction-2026-04-11.md](C:\Users\jung\Desktop\ZOOM_MEETING_BOT\notes\pdf-first-meeting-output-system-direction-2026-04-11.md)

## 1. 이 문서의 역할

이 문서는 기존 방향 문서를 더 짧고 실행 가능한 단위로 쪼개기 위한 세부 설계안이다.

이번 문서의 목적은 세 가지다.

1. 무엇을 절대 건드리면 안 되는지 다시 고정한다.
2. 새 HTML/CSS 기반 PDF 렌더러의 입출력과 책임 경계를 명확히 한다.
3. 바로 프로토타입 구현에 들어갈 수 있도록 1차 범위를 잘게 나눈다.

이 문서는 아직 구현 문서가 아니다.  
하지만 구현자가 이 문서만 읽고도:

- 어떤 파일을 새로 만들지
- 기존 어느 지점을 갈아끼울지
- 무엇은 유지하고 무엇은 바꿀지

를 바로 이해할 수 있어야 한다.

---

## 2. 이번 작업에서 절대 고정할 원칙

### 2-1. 바꾸는 것은 결과물 생성 방식뿐이다

이번 작업은 `결과물 생성 레이어` 교체 작업이다.

유지:

- Zoom 참가
- 음성 수집
- 전사
- 참가자 추적
- 제기자 / 주요 화자 / 타임스탬프 복원
- 세션 관리
- briefing JSON 생성
- SKILL 반영 철학

교체:

- DOCX 중심 렌더링
- PDF 생성 방식
- 결과물 시각 레이아웃 엔진

### 2-2. SKILL은 결과물 생성 안에서 최우선 지시사항이다

새 HTML 렌더러도 지금까지 합의한 기준을 그대로 따른다.

- SKILL에 있는 내용은 결과물 생성 안에서 전부 집행 대상이다.
- SKILL에 없는 것만 기본 레퍼런스로 간다.
- 시스템이 SKILL을 편한 내부 메뉴 몇 개로 줄여서 대체하면 안 된다.

### 2-3. trace 정보는 렌더러가 건드리지 않는다

아래 값은 렌더러의 스타일 옵션이 아니다.

- `raised_by`
- `speakers`
- `timestamp_refs`

이 값들은 이미 생성된 회의 근거 정보이며, 렌더러는 이를 보여주는 책임만 가진다.

---

## 3. 새 구조의 핵심 한 줄

`briefing JSON + SKILL + 이미지 자산`을 입력으로 받아  
`HTML/CSS 문서 -> PDF`를 직접 만든다.

즉 새 중심 흐름은 다음과 같다.

1. 회의 종료
2. 기존 시스템이 briefing JSON 생성
3. 필요 시 이미지 생성
4. HTML 렌더러가 JSON/SKILL/이미지를 조합해 HTML 작성
5. 브라우저 기반 엔진이 HTML을 PDF로 출력
6. DOCX는 필요하면 보조 산출물로만 별도 생성

---

## 4. 1차 프로토타입 범위

이번 1차 목표는 "완전한 템플릿 엔진"이 아니라,  
`기존 DOCX-first PDF보다 명확히 나은 PDF-first 결과물`을 만드는 것이다.

1차 프로토타입에서 반드시 지원할 것:

- 표지
- 회의 개요
- 회의 전체 요약
- 핵심 논의 주제
- 결정사항
- 액션 아이템
- 열린 질문 / 검토 사항
- 제기자 / 주요 화자 / 타임스탬프 표시
- 시각자료 inline 삽입
- 브랜드/회사 분위기 반영
- 폰트 번들 또는 명시적 폰트 매핑

1차에서 욕심내지 않을 것:

- 완전 자유 배치 편집기 수준의 시각 편집
- PPT 수준의 슬라이드형 애니메이션 개념
- 복잡한 다단/멀티칼럼 잡지형 레이아웃
- DOCX와 HTML의 완전한 1:1 시각 동일성

---

## 5. 입력 계약

새 렌더러의 입력은 아래 4개다.

### 5-1. briefing payload

기존 `summary_pipeline`이 생성하는 briefing JSON을 그대로 사용한다.

최소 필요 필드:

- `title`
- `meeting_datetime_label`
- `participants`
- `executive_summary`
- `sections`
- `decisions`
- `action_items`
- `open_questions`
- `postprocess_requests`
- `rendering_policy`
- `design_intent_packet`

### 5-2. SKILL-derived rendering policy

기존처럼 `rendering_policy`는 별도 딕셔너리로 유지한다.  
다만 HTML 렌더러는 이 값을 DOCX용 속성으로 변환하지 않고,  
HTML/CSS 표현에 직접 연결한다.

예:

- 색
- 폰트
- cover kicker
- surface tint
- 간격
- 페이지 밀도
- 섹션 강조 방식

### 5-3. visual assets

시각자료 관련 파일 경로 목록.

- raw generated image
- text-overlaid card image
- 향후 HTML 내부 카드 조합용 asset

### 5-4. export context

문서 생성 시 필요한 최소 부가 정보.

- 세션 id
- export stem
- 생성 시각
- output directory

---

## 6. 출력 계약

새 렌더러는 최소 아래 3개 산출물을 만들 수 있어야 한다.

1. `summary.html`
2. `summary.pdf`
3. `render_manifest.json`

`render_manifest.json`에는 아래를 기록한다.

- 사용한 렌더러 버전
- 적용된 폰트 자산
- 사용한 이미지 목록
- 사용한 theme name
- PDF 생성 성공 여부
- 품질 경고가 있다면 경고 목록

이 manifest는 이후 품질 디버깅에 매우 중요하다.

---

## 7. 권장 모듈 구조

1차 구현은 아래 구조를 권장한다.

### 7-1. 새 렌더러 파일

`src/local_meeting_ai_runtime/html_pdf_renderer.py`

책임:

- briefing + rendering_policy 입력 받기
- HTML 텍스트 생성
- CSS 결합
- PDF 렌더링 호출
- manifest 작성

### 7-2. 템플릿 자산 폴더

`doc/templates/html-meeting-output/`

예상 구성:

- `base.html`
- `report.css`
- `assets/fonts/`
- `assets/icons/`

### 7-3. 기존 exporter의 역할 축소

`artifact_exporter.py`는 당장은 유지하되,

- DOCX-first 경로
- HTML-first 경로

둘 다 호출 가능한 임시 오케스트레이터가 되게 한다.

최종 목표는:

- PDF는 HTML renderer 주도
- DOCX는 선택적 보조 출력

---

## 8. HTML 문서 문법 원칙

### 8-1. 문서는 block 흐름이 아니라 page-aware layout여야 한다

Word처럼 "블록을 쌓아두고 PDF가 알아서 페이지를 나누는" 방식으로 두지 않는다.

HTML/CSS 단계에서 고려해야 할 것:

- cover는 독립 page treatment
- 섹션 카드/이미지는 `break-inside: avoid`
- 과도한 하단 여백이 생기지 않도록 block height 설계
- 캡션/제목/이미지 묶음을 하나의 semantic figure로 유지

### 8-2. 시각자료는 테이블 블록이 아니라 figure component로 간다

지금 문제의 한 축은 이미지 카드가 테이블처럼 무겁게 들어간다는 점이다.  
HTML에서는 시각자료를 다음 구조로 둔다.

- `section`
  - `heading`
  - `body`
  - `figure.visual-card`
    - `figcaption`
    - `visual-body`

즉 시각자료를 문서 흐름 안의 자연스러운 figure로 취급한다.

### 8-3. 카드 내부 텍스트는 HTML이 책임진다

원칙:

- 이미지 모델은 구조/아이콘/분위기 위주
- 실제 한글 텍스트는 HTML/CSS가 책임

즉 최종 카드도 가능하면:

- background visual
- overlay text block
- caption

구조로 가야 한다.

이렇게 해야:

- `???` 방지
- 세션 내용과 정확히 맞는 한글 텍스트 반영
- 폰트 일관성 유지

가 가능하다.

---

## 9. 폰트 전략

### 9-1. 로컬 설치 폰트 의존을 줄인다

PDF가 메인이라면 폰트는 HTML/CSS 쪽에서 직접 통제해야 한다.

권장:

- 프로젝트 내부 `assets/fonts/` 폴더 도입
- `@font-face` 사용
- title / heading / body font 분리 지원

### 9-2. 폰트 선택 순서

1. SKILL에서 명시한 폰트 파일 또는 폰트 이름
2. 프로젝트 번들 폰트
3. 시스템 fallback

가능하면 1차에서도 `renderer_title_font`, `renderer_heading_font`, `renderer_body_font`를  
HTML CSS 변수로 직접 연결한다.

### 9-3. 라이선스 원칙

- 상용/브랜드 전용 폰트는 라이선스 확인 전 번들 금지
- 배포 가능한 폰트만 저장소에 포함
- 미포함 폰트는 대체 가능한 공개 폰트 매핑표를 둔다

---

## 10. 이미지 전략

### 10-1. raw visual과 final card를 분리한다

구조:

1. raw image generation
2. visual quality check
3. HTML figure 조합
4. PDF에 배치

즉 "최종 카드 PNG 하나"에 모든 걸 구겨 넣는 구조를 줄인다.

### 10-2. image placement는 semantic target 기준으로 간다

`before/inside/after` 같은 거친 분류 대신,

- 어느 섹션과 연결되는지
- 어떤 문단 묶음 뒤에 붙는지
- 어떤 figure 스타일인지

를 HTML 생성 단계에서 계산한다.

즉 배치는 figure target과 section id 중심으로 한다.

### 10-3. 잘림보다 축소를 우선한다

현재처럼 `ImageOps.fit(...)`으로 잘라 넣는 방식은 지양한다.

우선순위:

1. 전체 맥락이 보이도록 contain
2. 필요하면 배경 박스와 함께 레이아웃 조정
3. 마지막에만 crop

---

## 11. 페이지 / 여백 전략

### 11-1. A4 기준 print CSS를 사용한다

CSS `@page` 기준으로:

- page size
- margin
- section spacing
- figure spacing
- heading spacing

을 PDF 기준으로 직접 통제한다.

### 11-2. 페이지 하단 큰 공백 방지 규칙

아래 규칙을 기본값으로 둔다.

- `figure`, `summary cards`, `decision tables`에는 `break-inside: avoid`
- 너무 큰 블록은 variant layout으로 자동 축소
- 시각자료는 "제목만 앞 페이지, 본체는 다음 페이지"가 되지 않도록 캡션/이미지 묶음 유지

### 11-3. 긴 회의는 페이지 수를 제한하지 않는다

페이지 수는 고정하지 않는다.  
대신:

- 섹션 밀도
- 카드 크기
- 여백
- line length

를 조절해 긴 문서가 자연스럽게 늘어나게 한다.

---

## 12. 권장 PDF 엔진

1차 권장안은 **브라우저 기반 PDF 렌더링**이다.

이유:

- print CSS 지원
- 폰트 처리 유연성
- HTML/CSS 카드 표현력
- PDF가 실제로 사용자가 보는 결과물과 더 가까움

실행 후보:

- Playwright + Chromium

이 선택의 장점:

- 이미 이 프로젝트에서 브라우저 계열 도구를 다루는 문맥이 있음
- PDF 결과가 비교적 예측 가능함
- 향후 screenshot/visual regression도 붙이기 쉬움

---

## 13. 1차 구현 순서

### 단계 1. 새 렌더러 파일 뼈대 추가

목표:

- `html_pdf_renderer.py` 추가
- `render_html(...)`
- `render_pdf(...)`
- `write_manifest(...)`

### 단계 2. 최소 HTML 템플릿 추가

목표:

- cover
- overview
- executive summary
- sections
- inline visuals

까지만 지원

### 단계 3. 서비스 연결은 병렬 경로로 추가

목표:

- 기존 DOCX-first 경로는 당장 유지
- 설정값으로 HTML-first PDF를 켤 수 있게 추가

예상 플래그:

- `meeting_artifacts.pdf_renderer: "docx" | "html"`

### 단계 4. 네이버 skill 실제 케이스로 비교

비교 항목:

- 폰트 유지
- 표지 인상
- 이미지 품질
- 페이지 여백
- 본문과 시각자료의 결합감

### 단계 5. HTML-first를 PDF 기본값으로 승격

조건:

- PDF 품질이 DOCX-first보다 명백히 낫다
- trace fields 누락 없음
- skill 반영 품질이 더 높다

---

## 14. 1차 완료 기준

아래를 만족해야 1차 성공이다.

1. PDF가 DOCX-first 결과보다 폰트 일관성이 낫다.
2. 이미지 때문에 생기는 과한 빈 페이지가 크게 줄어든다.
3. 시각자료가 문서 안에서 툭 튀지 않고 자연스럽게 들어간다.
4. SKILL의 폰트/색/분위기 지시가 PDF에 더 직접적으로 보인다.
5. `제기자 / 주요 화자 / 타임스탬프`는 그대로 유지된다.
6. 기존 브리핑 JSON 계약을 깨지 않는다.

---

## 15. 이번 단계에서의 판단

지금 해야 하는 건 "새 문서 엔진을 처음부터 완벽하게 만드는 것"이 아니다.

지금 해야 하는 건:

- 시스템 본체는 그대로 둔 채
- 결과물 생성만 HTML/CSS 기반 PDF-first로 병렬 추가하고
- 실제 회의 결과물에서 DOCX-first보다 확실히 낫다는 걸 증명하는 것

즉 이번 세부 설계안의 결론은 다음 한 줄이다.

> **다음 구현 단계는 `HTML PDF 렌더러 프로토타입 추가`이며, 기존 core/briefing/trace 구조는 그대로 둔 채 출력 엔진만 교체 가능한 형태로 병렬 연결한다.**
