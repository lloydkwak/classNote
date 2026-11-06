# Advanced annotation quality — Phase D (2026-11-06)

## 현재 검증 범위

production `run_pipeline`의 opt-in notes→실제 8B→action→PDF export 경로를 검증했다.
실강의 PDF/audio는 현재 clone에 없었다. 아래 Phase D 입력은 **합성 PDF/무음 WAV와
고정 STT·정렬·semantic provider**이며, Qwen3-VL NF4 호출만 실제 모델이다.
독립 사람 평가, 실강의 STT/내용 추출 정확도, semantic coverage와 대규모 A/B는 미검증이다.

### 실제 산출물

최종 통합 실행: `data/phase-d-runs/gpu-e2e-03/`.

- `annotated.pdf`, `preview.png`: 실제 production export 및 Poppler 렌더. PNG를 직접 확인했다.
- `evidence_graph.json`, `grounding_results.json`, `annotation_actions.json`, `metrics.json`, `report.json`.
- 원본/작업 데이터: 같은 폴더의 `fixture/`, `state/jobs/phase-d-synthetic/`.
- 실제 복구 검증: `data/phase-d-runs/gpu-recovery-01/report.json`.

| 측정 (최종 통합 run) | 결과 |
|---|---|
| 추출 후보 / 기존 근거 검증 통과 | 8 / 7; `source_quote_not_found` 1 |
| 근거 검증 pass rate (이 fixture 후보 분모만) | 87.5% |
| grounding 실제 시도 / accepted / rejected / ambiguous / unmatched | 7 / 5 / 1 / 1 / 0 |
| 생성 action / 선택 / 렌더 / distinct knowledge | 7 / 7 / 7 / 7 |
| 상세 노트 보존 | 검증된 7개 모두 보존; 거절된 추출 1개는 validation에 기록 |
| density 및 notes resume | 추가 worker 없이 재사용; manifest 동일 |
| OOM / 재시도 (정상 통합) | 0 / 0 |
| raw provider cache hit | 0; 완료 manifest resume는 별도로 추론 없이 재사용 |
| model load / 평균 generate | 9.832s / 2.192s |
| 첫 요청 / 후속 generate 범위 | 첫 generate 2.331s; 후속 1.998–2.490s (load 제외) |
| grounding 전체 / 검증 runner 전체 | 28.521s / 29.138s |
| sampled device VRAM peak / 평균 | 7,764,705,280 / 5,611,511,867.8 bytes (7.23 / 5.23 GiB) |
| torch peak allocated / reserved | 7,240,175,616 / 7,465,861,120 bytes |
| worker process peak RSS / system used RAM peak | 약 5.58 GiB / 23,668,539,392 bytes; system 값은 다른 프로세스·tmpfs 포함 |
| 모델 / 설정 | Qwen3-VL-8B-Instruct pinned revision; NF4 double, SDPA, batch 1 |
| 이미지 / 예산 | 1440×810 → 965×543; max pixels 524288, input 4096, output 512 |

숫자는 강의 전체 coverage나 인간 기준 정확도가 아니다. `lecture_semantic_coverage`와
`human_target_accuracy`는 null이다. 모든 item importance가 5인 작은 fixture이므로
minimal/balanced/detailed 렌더 수가 모두 7이다. 일반 자료에서 수량이 같다는 의미는 아니다.
개발 중 통합 실행 `gpu-e2e-01/02/03` 각각 실제 완료 추론 7회, 복구 run 완료 3회로
총 완료 추론 24회다. 복구 run에는 별도로 timeout/cancel 중단 요청 2개와 의도적 OOM 1개가 있다.

### 정확한 연결과 안전한 거절

| 항목 | 실제 canonical target / 처리 |
|---|---|
| gradient 정의 | `p1_w4`, underline |
| α 역할 | `p1_w13`, circle |
| parameter update 수식 | `p1_l2`, bracket |
| ∇ (∇L 내부) | `p1_w14_c0`, native character circle |
| Loss curve | `p1_b5`, figure circle + 설명; block 연관만 확인 |
| 슬라이드에 없는 β | `unsupported_target`, target null, page note |
| 반복 θ | `ambiguous_repeated_text`, target null, page note |

PNG 검토에서 단어 underline과 α/∇의 개별 강조, 수식 bracket, 그림 연결은 의도한 영역에 있다.
없는 β/반복 θ에 확정 mark/connector를 그리지 않았다. 원본 슬라이드를 지우거나 수정하지 않는다.
다만 **수식 주변 connector가 교차하고, 큰 figure circle이 캡션과 겹친다**. 이는 기존 layout의
한계다. Text occlusion/overlap/readability를 0 또는 개선율로 수치화하지 않았다. Phase E 대상이다.
figure 내용의 의미 정확성을 accepted block 연결만으로 입증하지 않았다.

회전 0/90/180/270 + crop은 별도 deterministic 테스트에서 원본 빨간 도형의 export 위치
(1 PDF point 이내), canonical word display geometry, 실제 PDF ink vertices와 provenance를 검증했다.
실제 GPU의 회전된 슬라이드 선택 정확도를 평가한 것은 아니다.

`source_quote_not_found`는 의도적으로 넣은 미발화 문장 1개다. 기존 검증을 완화하지 않았다.
이전 컴퓨터의 중요한 실제 항목 탈락은 원본이 없어 재현하지 못했다. grounding 단계에서
이미 검증된 item을 추가로 삭제하지 않는 것을 실패/예산/OOM mock 및 실제 export로 확인했다.

### 실제 GPU 복구와 회귀

실제 warm worker의 50ms timeout/cancel 각각 `worker_timeout`/`worker_cancelled`, 프로세스 정리 성공.
각 close 후 device memory는 10 MiB였다. 다음 새 worker로 정상 추론 성공.
전체 VRAM 크기의 allocation을 8B 로딩 상태에서 시도한 **명시적 benchmark OOM 1회**는
worker 재시작 및 pixels 262144/output 256/input 2048의 기존 축소 정책으로 복구됐다.
복구 run 종료 직후 13 MiB, 후속 확인 10 MiB. 다른 GPU 프로세스를 종료하지 않았다.
이 probe는 production에서 자동 활성화되지 않는다.

최종 backend **432 passed, 0 failed (44.40s)**: 이전 408 + 새 24 cases.
frontend **25 passed, 0 failed**, Node 22.23.4. TypeScript/Vite build passed (기존 큰 chunk 경고).
새 테스트는 source/registry/mentions/graph/action/실제 export, density/style/부분·완료 resume,
API 출처/시간·409·page audio, budget/OOM/VLM 장애 fallback, IPC interpreter/경로/timeout/cancel,
v2 versioned mentions, crop/rotation ink, STT lease/unload를 포함한다.
backend 일반 테스트의 모델은 mock이며 위 실제 GPU 보고서와 구분한다.
live 및 page playback의 기존 회귀가 통과했지만 실제 브라우저 오디오 이벤트는 수동 검증하지 않았다.

재현 명령과 스키마: [Ubuntu runbook](ubuntu_vlm.md), [Evidence Graph](evidence_graph.md).
Phase F 동일 **실강의** A/B 및 사람 정답은 아직 없다. 단순 모델 추가를 품질 개선 증거로 보고하지 않는다.

---

# 과거 기록 — Phase A/B

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
