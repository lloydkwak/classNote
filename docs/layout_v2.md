# Slide-first Layout v2 + Lecture Overview — 2026-11-06

현재 구현은 `layout-v3-slide-first`이며 `LAYOUT_ENGINE=v2`가 기본값이다.
API `layout=v1|v2`, `ink_style=clean|natural|highlighter` 비교와 이전 job 호환을 유지한다.
UI 업로드는 정밀 grounding을 기본 선택하며 사용자가 해제할 수 있다. API의 기존
`visual_grounding` 기본값/실패 fallback은 유지한다. 모델은 `.venv-vlm`의 기존 SSD 8B NF4를 쓴다.

## 실제 연결

`ContentItem → GroundingItem → validated canonical ID → AnnotationAction → consolidate →
layout_page_v2 → export_annotated_pdf → React SlideSheet`가 하나의 production 경로다.
`choose_action`은 기존 semantic type/importance/element kind로 underline, highlight, box,
circle, check, bracket, arrow를 선택하고 `selection_reason`을 남긴다. 별도 LLM은 호출하지 않는다.
숫자/기호/중복 단어와 stale registry 검증을 유지한다. rejected/ambiguous/unmatched에
확정 target을 부여하지 않는다. Renderer가 mark를 억제하면 mark_reason이 남는다.

긴 derivation은 `local_request`가 발화의 마지막 문장을 정확히 복사한 `selection_quote`로
선택 초점을 좁힐 수 있다. native 수식의 lexical evidence가 있을 때만 적용하며 원래
source_quote, segment IDs, 시간은 유지한다. focus가 원문 부분 문자열이 아니면 거절한다.

## 배치와 원본 보호

1. 검증된 native target에 직접 표시한다.
2. target 근처/페이지 내부 free-space 후보를 거리 순으로 고른다.
3. 내부에 안전한 후보가 없을 때만 기존 좌우 33% margin을 사용한다.
4. margin도 부족하면 하단 note band를 늘린다. Detailed Notes를 자르지 않는다.

내부/외부 후보는 hard tier로 구분한다. 이전의 내부 0.8×width penalty는 제거했다.
모든 메모가 내부에 들어가면 gutter는 0이며 원래 페이지 크기/비율/벡터를 보존한다.

중요한 수정: semantic extractor가 반복 header/footer/작은 수식 조각을 제외하더라도
그 내용은 PDF에 남아 있다. `enrich_geometry`는 **모든 native PDF word bbox**를
`Page.protected_text_bboxes`에 별도로 보존한다. 이 bbox는 배치 장애물이며 selectable
canonical ID로 승격하지 않는다. 회전/crop은 source.rotation_matrix로 변환한다.
기존 figure bbox, 다른 메모, stroke width/padding도 hard collision 검사에 사용한다.

텍스트는 실제 사용 글꼴의 ascender/descender로 높이와 line-height를 계산한다.
`placement.baseline_offset`을 PDF TextWriter와 SVG가 공유한다. 이전 job은 font_size를
baseline으로 쓰는 기존 값으로 fallback한다. 그리스 문자/괄호/화살표는 기존 CJK math
fallback으로 보존하며, 아이콘처럼 렌더되는 Pretendard math slot을 사용하지 않는다.
64×64 native 배경 밝기와 텍스트 영역 표본으로 어두운 슬라이드에는 밝은 노란 펜을 쓴다.

Connector는 기존 직접 경로/8방향 bounded A*와 3500-node 예산을 재사용한다.
안전한 경로가 없으면 생략 이유를 기록한다. 큰 figure circle은 생성하지 않는다.
검증되지 않은 figure 내부 기호에 특정 수식을 표시하지 않으며 PDF에는
`Figure explanation → Detailed Notes` 연결 안내를 남긴다. 원래 상세 설명/근거는 보존한다.

## 렌더와 provenance

`Sketch`는 final text_box, baseline_offset, stroke paths, connector control points,
text_color/mark_color, opacity/width, placement/fallback reason을 갖는다.
기존 annotation/action/knowledge/segment/target ID와 registry hash는 그대로 유지된다.
Highlight는 opacity .24이며 실제 target 내부의 투명 강조 접촉은 불투명 occlusion과
분리 집계한다. 둥근 stroke 끝이 옆 단어를 침범하지 않도록 endpoint를 안쪽으로 제한한다.
connector의 실제 폭 1과 mark 폭을 각각 평가한다.

## Lecture Overview와 페이지 계약

모든 production export의 첫 물리 페이지는 Overview다. `include_overview=False`는
내부 baseline 비교용 Python 인자이며 public API에는 노출하지 않는다.
원본이 N장이면 기본 출력은 N+1장이다. 항상 original input.pdf에서 재export하므로 중복 삽입하지 않는다.

| 대상 | 기존 API source page | source_slide_index | export_page_index (0-based) |
|---|---:|---:|---:|
| Overview | 없음 | null | 0 |
| 원본 슬라이드 1 | 1 | 0 | 1 |
| 원본 슬라이드 3 | 3 | 2 | 3 |

`Annotations.overview/page_mapping`, PDF embedded `classnote-page-map.json` 및
Evidence Graph rendered node에 이 구분을 보존한다. source page audio는 이동하지 않는다.
PDF page label은 Overview/1/2/…이며 원본 TOC는 물리 offset +1로 옮긴다.

`overview.py`는 density와 무관한 전체 validated Rich Notes를 추출식으로 묶는다.
segment/quote/숫자/시간 근거를 재검증하며 새 사실/시험 힌트를 만들지 않는다.
최대 7개 페이지 주제, 강의 흐름, 실제 short notes/공식을 1페이지로 구성한다.
길면 완전한 short note 단위로 제한하고 presentation.compressed_knowledge_ids와
omitted_overview_knowledge_ids를 기록한다. 수식을 중간에서 잘라 다른 식으로 만들지 않는다.
전체 content는 JSON/상세 노트에 남는다. 근거가 없으면 insufficient_evidence 개요를 만든다.

신뢰도 .3 미만 정렬에는 확정 시간 링크를 표시하지 않는다. 이 기준은 기계적 신호이며
사람이 정렬을 검증했다는 뜻이 아니다. PDF의 슬라이드 링크는 내부 go-to, 실제 오디오
seek는 UI Overview 시간 버튼을 사용한다. 오프라인 PDF의 오디오 URL은 구현하지 않았다.

## 캐시와 재현

PDF/registry/mentions/transcript/alignment/model/prompt/target/focus/validation fingerprints를
구분한다. density/style/layout/overview/render 변경은 VLM을 다시 호출하지 않는다.
새 focus 정책/schema 변경으로 이전 VLM 결과는 stale 처리하며 조용히 섞지 않는다.
`export_density` key는 Overview renderer 소스도 포함한다. old annotations-only job은
이전 geometry를 유지하며 안전한 Overview 및 page mapping을 추가한다.

## 실행과 검증

```bash
# 실제 원본은 읽기 전용, 새 폴더에서 저장 후보 재검증 + 실제 SSD 8B
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/verify_slide_first.py \
  --job data/jobs/05a433631737 --output data/slide-first-runs/new-run --gpu
# 같은 산출물의 레이아웃/Overview만 갱신: 새 STT/LLM/VLM 호출 없음
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/verify_slide_first.py \
  --job data/jobs/05a433631737 --output data/slide-first-runs/new-run --resume
```

실제 검토: `data/slide-first-runs/real-reviewed/{before,after,same-evidence-baseline}.pdf`,
Overview/슬라이드3–6 PNG, contact-sheet, action-metrics, page-mapping-tests, quality-report.
원본 raw words와 실제 **exported PDF note word bbox**의 충돌도 검사한다.
이 검사는 raster text/semantic accuracy/사람 가독성을 모두 증명하지 않는다.
실측과 한계는 [annotation_quality_report.md](annotation_quality_report.md) 참조.
