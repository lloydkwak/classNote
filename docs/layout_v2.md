# Phase E Layout v2 — 작업 계획과 검증 범위

시작 HEAD `8345a5f`, clean main. 실제 회귀 강의 job `6c094c2fe7cc`를 확인했다:
11페이지 PDF, m4a 원본/MP3, 37 STT segments, alignment/Rich Notes/grounding/기존 PDF 존재.
원본 job은 수정하지 않고 `data/phase-e-runs/` 복사본에서 비교한다.

Baseline backend432/frontend25 통과. PyAV18.1.0, faster-whisper1.2.1 보존.
현재 검증된 지식은 3/5/7페이지 총4개다. 4페이지는 unsupported_number 2개 거절,
6페이지는 의미 추출 ValueError unavailable이다. 페이지3 alignment confidence도 낮다.
레이아웃 개선이 이 upstream 누락이나 의미 오류를 고쳤다고 주장하지 않는다.

구현 순서: 기존 action→선택적 Layout v2, occupied/free-space 후보, 안전한 mark/connector,
확장 margin/bottom band, 공유 SVG/PDF geometry, style/density/render cache 분리,
loss audit와 정렬 진단, React upload/progress/workspace 전면 개편, 실제 브라우저/PDF 비교.
필기 내용·source ID·원본 파일을 보존하고 GPU/LLM을 레이아웃 때문에 호출하지 않는다.

실제 결과/남은 한계는 구현·시각 검증 후 아래에 갱신한다.

## 구현

`layout_v2.py`: `enrich_geometry`, `candidates`, `segment_hits`, `safe_path`, bounded `route`,
`make_mark`, `layout_page_v2`, `evaluate`. PyMuPDF native registry/word geometry와 기존 free_rects를
재사용한다. OCR import/download 및 GPU 호출은 없다. 원본 unrotated crop-local registry는
기존 `display_box`를 통해 display-normalized geometry로 바꾼다.

기존 `density_annotations`/`consolidate`/`export_density`가 layout=v2일 때만 새 경로를 선택한다.
`LAYOUT_ENGINE=v1`이 backend 기본이며 API query `layout=v1|v2`,
`ink_style=clean|natural|highlighter`로 비교한다. 새 UI는 명시적으로 v2를 요청한다.
기존 job에 detailed_notes가 없으면 기존 annotations를 그대로 반환한다.

## 배치와 hard constraints

각 native target 근처 상하좌우, 기존 free-space, 양측 확장 margin 후보를 만든다.
occupied native words와 figure 영역, 이전 annotation textbox를 padding 포함해 배제한다.
배경색을 추측해 슬라이드 내부에 읽기 어려운 글을 배치하기보다 조용한 margin을 선호한다.
후보 비용은 거리와 margin preference로 정렬하고 입력 순서/ID가 같으면 재현된다.

입력 PDF width/height/비율은 유지하고 양쪽 gutter를 page width의33%로 확장한다.
여백이 꽉 차거나 메모가 길면 하단 note band를 늘린다. 글꼴을 계속 줄이거나 text를 자르지 않는다.
v2 planner는 density importance threshold는 유지하되 기존 physical-space budget으로
고우선순위 item을 버리지 않는다(`allow_overflow=True`). v1 planner는 이전 정책을 유지한다.
Action 정책 버전을 올렸고 VLM raw cache/knowledge는 재사용한다.

Connector는 직접 경로를 먼저 검증하고, 막히면 8방향 A*를 쓴다. 앞선 connector와의 교차도
피한다. 3500-node 예산, 8개 endpoint 후보로 작업량을 제한한다. safe grid entry가 없거나
탐색 budget을 소진하면 connector를 생략하고 명시적 reason을 남긴다. 필기 text는 보존한다.
OR-Tools/비결정적 solver는 설치하지 않았다. node-budget fallback을 테스트했다.

symbol circle은 작고 검증된 native bbox에서만 생성한다. 다른 glyph와 부딪히면 해당 glyph의
짧은 underline으로 바꾸고, 그것도 불가능하면 mark를 생략한다. 임의 좌표로 옮기지 않는다.
figure block만 확인된 경우 큰 원이나 세부 vector/axis arrow를 그리지 않는다. 관련 강의 설명을
margin에 두고 `figure_block_only_no_verified_subtarget`으로 connector 생략을 기록한다.
PDF 안의 label 의미를 확인한 것처럼 보이지 않게 하며 canonical block provenance는 유지한다.

## 렌더 계약

`Sketch`: 기존 mark/strokes/arrow/text position/font/lines에 `stroke_width`,
`stroke_opacity`, `placement`를 호환 가능한 optional/default 필드로 추가했다.
placement에는 최종 text_box, target_bbox, anchor_point, connector_control_points,
placement status/reason, connector/mark fallback, collision_count/overflow가 있다.
knowledge/action/target IDs는 외부 Annotation.provenance에 그대로 남는다.

`PageAnnotations.output_height`, gutter, layout_metrics가 확장 canvas를 정의한다.
PDF는 원본 vector page를 원래 크기로 옮기고 같은 stroke 좌표를 gutter offset만 적용해 그린다.
SVG도 같은 viewBox와 크기를 사용한다. density/font/style/layout은 export key와 graph render
revision에 포함되며 STT/VLM 재분석을 하지 않는다. 원본 PDF는 쓰지 않는다.

- Clean: 정돈된 stroke, 기존 설정 글꼴.
- Natural: ID/스타일 seed로 약한 jitter, 기존 Nanum Pen, 수학은 별도 fallback.
- Highlighter: 같은 geometry에 width5/opacity.28. 본문에 불투명 배경을 덮지 않는다.

## 진단과 평가 범위

기존 annotation API의 `loss_audit`에 페이지별 audio ranges/confidence, STT/aligned counts,
candidate/validated/grounded/planned/placed counts와 거절 이유를 담았다.
grounded count는 density 선택 전의 지식 기준이다. `stage_losses`로 not_extracted,
failed_evidence_validation, extraction_unavailable, grounding_rejected,
not_selected_by_density, unplaced_by_layout, render_failed를 구분한다.
PDF export 전의 render_failed는 null이며 `render_state`는 layout ready다.
실제 PDF 성공은 evaluation report의 pdf_export_validity에서 별도로 확인한다.

평가 수치는 native word/figure bbox에 대한 기하학적 지표다. 이미지 안에 래스터화된 글씨,
없는 OCR 대상, semantic 정확도, 사람의 가독성은 자동 지표가 포괄하지 않는다.
figure-caption 판단도 native caption box 근접 휴리스틱이다. 원래 강의 정렬을 바꾸지 않았다.

최종 산출물/실측/한계는 [annotation_quality_report.md](annotation_quality_report.md),
기술 선정은 [phase_e_technology_decisions.md](phase_e_technology_decisions.md) 참고.
