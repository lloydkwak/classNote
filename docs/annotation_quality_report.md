# Advanced annotation quality — Phase A/B

## 보고 범위

이번 변경은 Qwen3-VL 8B 로컬 실행 기반이다. production annotation planner, layout,
export, 정렬 및 frontend 화면은 변경하지 않았다. 따라서 필기 위치/내용/가독성이
개선됐다고 주장하지 않는다. VLM 응답의 image bbox는 annotation 좌표로 사용하지 않는다.

현재 clone에는 golden JSON은 있으나 이전 컴퓨터의 실제 PDF/audio/job은 없다.
과거 문서의 실강의 지표를 이번 환경에서 재측정한 값으로 인용하지 않는다.
사람의 verified review, 동일 실강의 A/B PDF/contact sheet는 아직 없다.

## 검증

| 항목 | 실행 결과 | 의미와 한계 |
|---|---|---|
| 최종 backend 전체 회귀 | 382 passed, 36.14s | 기존 369 + VLM 13, mock과 임시 데이터, production 경로 유지 |
| 기존 backend + 초기 새 VLM 11 tests | 380 passed, 39.77s | host, lockfile 그대로. mock API, PDF export, page audio, Rich Notes, density, 평가 회귀 |
| 추가 crash/invalid JSON telemetry 포함 VLM tests | 13 passed, 2.94s | CPU mock. 실제 GPU OOM/VRAM 복구 검증은 아님 |
| 기존 player | 25 passed, Node 22.23.4 | 상태 머신 검증. 브라우저 이벤트/음성 재생 수동 검토와 구분 |
| production frontend build | passed | 기존 bundle size warning 유지 |
| VLM package 호환성 | `pip check` passed | 실제 모델 로딩/추론 검증과 구분 |
| smoke PDF→PNG | 960×540 synthetic 렌더 성공 | 원본 PDF text 추출 확인. 실제 슬라이드 품질의 증거 아님 |
| 실제 8B 추론 | `gpu_benchmark.md` 참조 | mock과 구분, 모델·hardware·입력·peak·latency 명시 |
| 실제 8B JSON smoke | 2/2 valid, OOM 0 | synthetic, cache bypass. 제목/주요 text 일치; formula region 누락 |
| 실제 CUDA OOM probe | 의도적 OOM 2회, 복구 2/2 | 같은 8B 새 worker + budget 축소. 자연 발생 OOM/전체 강의 안정성과 구분 |
| 실제 두 페이지 순차 추론 | p1/p2 2/2 schema 통과, 제목 일치, OOM 0 | 동일 worker 재사용. synthetic 2장으로 긴 강의 안정성을 주장하지 않음 |

mock VLM tests는 lazy load, 두 페이지 순차 요청, 동일 child 재사용, 종료 후 재시작,
동시 요청 직렬화, process 간 GPU lease, timeout 종료, hard crash 회수, OOM 작은 budget 재시도와
두 번째 OOM 실패, schema/bbox/NaN 거부, demo cache-only, 모델/이미지/페이지/budget별 cache
구분, 실패 결과 미캐시, 실패 JSON의 측정값 보존을 검증한다.

## 다음 평가 게이트

1. 실제 단일 페이지 JSON smoke와 사람이 볼 수 있는 원본 PNG/응답 비교.
2. 원본 PDF word/line/block ID registry를 재사용해 element 존재/페이지/좌표 검증.
   회전/crop/scale 변환, 이미지형·혼합 언어·복잡한 수식 fixture 추가.
3. SoM vs 원본 이미지 대조, 정확한 formula symbol/figure/word 대상 gold.
4. 기존 rich evidence에 target/action/render provenance 연결 및 alignment hash 무효화.
5. 동일 PDF/audio A/B 후 verified 의미 단위 coverage/groundedness/unsupported claim,
   target precision, occlusion/overlap/out-of-bounds와 실제 학생의 가독성 검토.

분모가 없거나 아직 실행하지 않은 metric은 null/미측정으로 남긴다. 추출 항목 보존율을
강의 전체 semantic coverage로 보고하지 않는다. 자동 literal-title 검사와 VLM self-confidence는
사람의 사실성·target 정확성 평가를 대체하지 않는다.

실제 출력에서 수식 영역 누락과 graph 설명/영역의 부정확성이 확인됐다.
따라서 **VRAM에 들어가고 JSON이 유효하다는 사실만으로 annotation 품질 개선을 주장하지 않는다**.
원본/응답과 첫 schema 실패는 `data/vlm-runs/gpu-smoke-01`, `gpu-smoke-02`에 보존한다.

## Phase C — 실제 element-ID grounding

`data/grounding-runs/phase-c-06/report.json`과 결과 PNG를 실제 확인했다. SSD의 기존 8B를
사용했고 모델 재다운로드는 없다. source=synthetic, human_verified=false.

| 지표 | original | SoM |
|---|---:|---:|
| 실제 요청 / schema valid | 7 / 7 | 7 / 7 |
| positive target ID 일치 | 5 / 5 | 5 / 5 |
| 모델 raw ID/abstention 기대 일치 | 5 / 7 | 6 / 7 |
| 검증 후 ID/abstention 기대 일치 | 7 / 7 | 7 / 7 |
| negative case 확정 연결 | 0 / 2 | 0 / 2 |

대상은 gradient 단어, 식 내부 α, 수식 전체, ∇L 내부 ∇, graph다. 없는 β는 원본 모드에서
α로 잘못 선택했으나 quote 검사가 거부했고, SoM 모드는 unmatched였다. 반복 θ는 양쪽 모두
첫 θ를 골랐으나 중복 검사로 ambiguous 처리하고 두 후보 ID를 보존했다.

모델 bbox를 사용하지 않아 ∇ 한 글자/수식 줄/graph가 원본 PDF의 서로 다른 native bbox에
연결되는 것을 결과 PNG에서 확인했다. 이는 assistant의 시각적 검토이며 사람 verified 평가는 아니다.

초기 Markdown/array JSON 실패, 잘못된 symbol/block ID, knowledge ID 오류, figure target_text 오류를
삭제하지 않고 이전 run에 보존했다. 세부 내역은 `visual_grounding.md`에 있다.
최종 backend 408 passed, frontend 25 passed/build 통과. OCR 정확도·실강의 A/B·semantic entailment·
PDF 필기의 최종 가독성 개선은 여전히 미측정이다. 이 작은 fixture는 prompt 개발에도 사용했다.
