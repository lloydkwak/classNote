# Slide-first + Lecture Overview 검증 — 2026-11-06

실제 강의 job `05a433631737`의 11페이지 PDF/37개 STT segments/기존 alignment와 저장된
Rich Notes 후보를 복사하여 사용했다. 지정된 `classNote.pdf`, `classNote (1).pdf` 파일명은
저장소에서 찾지 못했다. 이 이름의 두 파일을 직접 분석했다고 주장하지 않는다.
새 STT/정렬/의미 LLM 호출은 0이다. 원본 job의 모든 기존 파일 hash를 실행 전후 비교했다.

## 결과와 비교 범위

- 실제 saved before: validated 6개, 내부 텍스트 메모 0개, 원본 PDF 11페이지.
- 발화의 명시적 `zero/one` 표기만 숫자 0/1과 대조하도록 수정하고 저장 후보를 재검증:
  validated 11개. 기존 6개의 ID/content/short_note/source_quote는 보존했다.
- 1개 field 일반화 후보는 source_quote가 문장 중간에서 끝나 숫자 근거가 부족하여 계속 거절.
  quote validation을 풀어 통과시키지 않았다. 정렬 자체는 수정하지 않았다.
- 최종 실제 8B: 11개 요청, accepted 9 / ambiguous 2 / rejected 0 / unmatched 0.
  accepted 중 figure 1개는 block association만 확인되었으며 내부 기호 의미의 검증이 아니다.
- 선택/배치/렌더 텍스트 메모 11개, 내부 11 / margin 0, 직접 강조 8개 (선택 항목의 72.73%).
  이 비율은 semantic coverage나 target accuracy가 아니다. 사람 gold accuracy는 null.
- 실제 mark: bracket 2, check 2, highlight 1, box 1, underline 2, connector arrow 7.
  실제 강의 circle/standalone-arrow action은 0이다. circle 포함 7종의 PDF renderer contract는
  mock/native geometry 테스트로 별도 검증했으며 실제 8B 기호 선택 성공으로 과장하지 않는다.
- 같은 knowledge 11개/선택 action/target/font를 쓰는 `same-evidence-baseline.pdf`를 추가했다.
  현재 planner + v1 layout/renderer 비교이며 옛 코드 버전 전체를 재현한 결과는 아니다.

| 같은 근거 비교 | v1 layout/renderer | slide-first |
|---|---:|---:|
| 내부 메모 텍스트 | 0 | 11 |
| 외부 메모 텍스트 | 11 | 0 |
| native 텍스트 상자 가림 면적 (pt²) | 0 | 0 |
| stroke/native word 간섭 (word/path 접촉) | 25 | 0 |
| connector/native word 간섭 | 14 | 0 |
| annotation box 겹침 | 0 | 0 |
| connector 교차 | 0 | 0 |
| figure/caption 간섭 | 0 | 0 |
| 페이지 밖 | 0 | 0 |

connector 교차/텍스트 상자 가림은 원래 0이어서 개선됐다고 주장하지 않는다.
자체 target의 반투명 highlight 접촉 1건은 별도 집계하며 불투명 가림과 구분한다.
실제 exported note word bbox와 원본 native word bbox를 직접 대조한 충돌 0,
exported note word의 페이지 밖 0도 확인했다. raster 내부 글자/의미 정확도를 보장하는 지표는 아니다.

## 실제 이미지 검토 및 발견한 오류

Codex가 Overview, 슬라이드3/4/5/6, 3열 contact sheet와 브라우저 desktop/mobile 이미지를 열어
검토했다. 사람 평가자가 작성한 gold/사용성 점수와 구분한다.

- 3페이지: 마지막 결론 발화를 verbatim focus로 지정하여 `-z = -α - βj` native 식에 bracket.
  반복 z 대상 정의 항목은 ambiguous 상태와 일반 메모를 유지한다.
- 4페이지: 두 예제의 native 식에 check 및 connector, 내부 필기 두 개.
  중간 검토에서 수식 조각이 semantic 추출에서 제외되어 메모와 겹치는 오류를 실제 PNG에서
  발견했다. 배치에는 모든 raw native word를 별도 보호하고 PDF/SVG baseline/글꼴 높이를
  공유하도록 수정했다. 최종 PNG와 exported word bbox에서 충돌이 해소된 것을 확인했다.
- 5페이지: reflection에 투명 highlight. 그림 전체 원 제거. 그림의 w/γ/δ와 기존 z/α/β
  short note를 같은 뜻으로 취급할 수 없어 PDF의 figure 메모를 Detailed Notes 연결 안내로
  바꿨다. 원래 상세 설명·quote는 보존했다. vector/원점의 세밀한 geometry는 미검증이다.
- 6페이지: theorem에 box, 실제 native glyph로 결합한 인수분해 식에 bracket.
  field 일반화 후보 탈락과 페이지 alignment confidence 0은 여전히 audit/UI에 표시한다.
- 초기 Overview의 괄호/화살표가 아이콘처럼 보이는 font slot 오류를 발견하여 기존 CJK math
  fallback을 적용했다. Unicode 추출과 1페이지 압축/근거 부족/재export를 테스트했다.

## Overview/페이지/오디오

최종 12페이지 = Overview 1 + 원본 11장이다. 원본 텍스트 토큰, 순서, 페이지 크기/비율을
검사했다. UI Overview는 0이지만 source API page는 기존 1..11이며 source_slide_index는
0..10, export_page_index는 1..11이다. PDF embedded mapping과 graph render 참조도 일치한다.
기존 page_audio JSON을 복사본과 완전히 비교했고 5개 audio range의 source page→export index와
시간이 유지된다. density 세 모드/기존 job/다운로드/브라우저 seek를 검증했다.
시간 신뢰도가 낮은 3/6페이지에는 Overview에서 확인 필요를 표시한다.
PDF에는 원본 슬라이드로 이동하는 내부 링크가 있다. 오디오 seek는 UI 시간 버튼에서 제공하며
오프라인 PDF에 외부 audio URL을 넣는 기능은 없다.

## 실제 GPU 측정

기존 RTX3080 10GB / driver550.163.01 / CUDA12.4 / SSD cache 유지.
`.venv-vlm`: torch2.6.0+cu124, transformers4.57.6, bitsandbytes0.49.0, accelerate1.12.0.
checkpoint `Qwen/Qwen3-VL-8B-Instruct`, revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`,
NF4 double quantization / SDPA / batch1. 최종 fresh run:

- 새 실제 추론 11, cache hit 0, OOM 0, retry 0, JSON schema valid 11/11.
- load 9.762초, 평균 inference 2.647초 (2.030–3.521), grounding stage 45.084초.
- 장치 표본 peak 7.23GiB (7,764,705,280 bytes), torch allocated peak 6.74GiB,
  reserved 6.95GiB. 두 기준을 혼동하지 않는다.
- input tokens 1348–2331, output 69–101, 원본 이미지1080×810→제한836×627.
- 종료 후 nvidia-smi 10MiB, compute worker 없음 확인.
- 원인 분석을 위한 모든 prompt/validator revision 실행 합계는 새 추론71회 + cache6회다.
  `real-03`은 GPU11회 뒤 평가 스크립트 NameError로 실패했고 수정 후 새 폴더에서 검증했다.
  최종 결과와 실패한 중간 산출물을 구분한다. 모델 재다운로드/새 모델 설치는 없다.
- 최종 PDF 수정은 `--resume`으로만 수행했으며 VLM 추가 호출 0. 보고서의
  previous_gpu_sampling/grounding_telemetry는 최초 실제 GPU 실행의 역사적 측정이다.

## 테스트와 산출물

최종 backend **474 passed**, frontend **31 passed**, production build 통과.
Chromium **7 시나리오 통과, page error0**, 폭375/768/1280/1440 overflow 없음.
업로드/상태 mock 테스트와 실제 저장 강의 API/PDF/audio 탐색을 구분한다.
PyAV18.1.0 + faster-whisper1.2.1에서 실제 synthetic WAV decode 560000 samples 성공.
이 테스트는 실제 강의 STT를 다시 실행한 결과가 아니다. dependency sync/lock 재해결 없음.
기존 bundle size warning은 남는다. 기존 OOM/timeout/cancel 회귀는 mock이며 이번 단계에서
실제 OOM을 의도적으로 유발하지 않았다.

`data/slide-first-runs/real-reviewed/`:
`before.pdf`, `after.pdf`, `same-evidence-baseline.pdf`, `overview-preview.png`,
`slide3-preview.png`..`slide6-preview.png`, `contact-sheet.png`, `annotations.json`,
`annotation_actions.json`, `grounding_results.json`, `evidence_graph.json`,
`action-metrics.json`, `page-mapping-tests.json`, `report.json`, `quality-report.md`, `frontend/`.
`data/slide-first-runs/synthetic-mock/`은 7개 지식의 mock production end-to-end 결과다.
전체 강의 semantic coverage/human gold 정확도/새 STT·alignment 개선은 미검증이다.

다음 우선순위: 실제 3/6페이지 alignment gold, 잘린 field quote의 extraction 복구,
figure 내부 w/γ/δ/축·벡터의 검증 가능한 geometry, 긴 connector의 라우팅 품질과 사람 A/B.

---

## 이전 Phase E/D 기록

# Advanced annotation quality — Phase E (2026-11-06)

## Phase E 최종 검증

이번에는 실제 사용자 강의 job `6c094c2fe7cc`의 11페이지 PDF/녹음/37 STT segments/정렬/
기존 Rich Notes가 존재한다. 원본을 새 state 디렉터리로 복사하고 **동일한 저장 지식과
grounding**으로 v1/v2를 비교했다. STT/정렬/semantic extraction 모델을 다시 실행하지 않았다.
VLM factory 호출은 평가 runner에서 막았다. 새 실제 GPU 추론0, 새 실제 LLM 호출0이다.

### 산출물 및 시각 검토

- 실제 강의: `data/phase-e-runs/final-real/`.
- Phase D 합성 지식/grounding 재사용: `data/phase-e-runs/final-synthetic/`.
- 각 폴더에 `baseline.pdf`, `layout-v2.pdf`, `contact-sheet.png`, `annotations.json`,
  `layout-debug.json`, `metrics.json`, `report.json`, `evidence_graph.json` 저장.
- 합성 capacity probe: `final-synthetic/overflow.pdf`, `overflow-preview.png`.
- 0/90/180/270 및 crop의 독립 좌표 fixture: `data/phase-e-runs/rotation-review/`.
  실제 VLM 선택 검증이 아닌 수동 native-coordinate fixture임을 report에 명시했다.
- UI before: `data/phase-e-runs/e-01/frontend-before.png`, `workspace-before.png`.
- UI 최종: `data/phase-e-runs/frontend-final/`: landing desktop/mobile, processing, error,
  result desktop/mobile/768/1280, mobile notes/pages, report.json. 실제 Chromium으로 확인했다.

실제 강의의 3/4/5/6페이지, 합성 수식·기호·그림, overflow와 회전/crop PNG를 직접 확인했다.
3페이지 수식 유도 노트, 5페이지 그림 바깥 메모 및 거대한 원 제거를 확인했다.
5페이지의 기존 설명은 z/α/β를 사용하지만 그림 표기는 w/γ/δ다. **이 의미 불일치를 고쳤다고
주장하지 않는다.** 내부 기호를 확인하지 않은 figure block의 circle/connector는 생략하고
기존 설명/출처를 여백에 유지한다. UI에서도 세부 기호 의미 확인이 필요함을 알린다.

### 같은 입력의 기하학 비교

| 측정 | 실제 강의 v1 → v2 | 합성 v1 → v2 |
|---|---|---|
| 선택 action → 렌더 / provenance | 4→4 / 4→4 | 7→7 / 7→7 |
| action-to-render retention | 1.0→1.0 | 1.0→1.0 |
| 검증 target의 배치 | 1/1→1/1 (figure block) | 5/5→5/5 |
| annotation textbox / native text 가림 면적 | 0→0 | 0→0 |
| textbox overlap / out of page | 0→0 / 0→0 | 0→0 / 0→0 |
| connector 교차 | 0→0 (개선 주장 없음) | 1→0 |
| connector/native text 간섭 | 0→0 | 5→0 |
| ink/native text 간섭 | 2→0 | 13→0 |
| figure 영역 stroke 간섭 | 1→0 | 1→0 |
| native caption 간섭 | 0→0 | 2→0 |
| 명시적 connector 생략 | 0→1 | 0→1 |
| 페이지 수 / 원본 추출 텍스트 유지 | 11/11, 유지 | 1/1, 유지 |

미검증 figure 세부 대상을 가리키는 connector 1개씩을 의도적으로 생략했다.
모든 connector를 삭제해서 교차만 줄인 것이 아니다. 합성의 나머지 검증된 word/symbol/formula는
직접/routed connector, word underline, α circle, ∇ native underline, formula bracket을 사용한다.
∇ circle은 인접 glyph와 충돌하여 native bbox underline으로 안전하게 바꿨다.

이 수치는 native bbox 기반 collision proxy다. 래스터 이미지 내부 글씨와 사람의 가독성,
의미적 정확도를 전부 측정하지 않는다. `annotation_placement_rate`와 retention은 이미
선택된 item의 분모만 사용한다. 실강의 전체 semantic coverage/human accuracy가 아니다.

capacity probe는 기존 문장을 반복한 **합성 배치 용량 테스트**다. 12개 긴 메모를 전부 보존하고
원본 높이540pt에서 약4913.76pt 하단 band로 확장했다. overlap/out-of-bounds0이다.
추가 강의 지식12개를 추출했다는 뜻이 아니며, 이 길이에서는 향후 continuation page가 더 적합하다.
현재 구현은 margin+하단 band이며 별도 이어지는 노트 페이지와 OR-Tools solver는 미구현이다.

### 실제 누락 원인과 정렬 진단

| 페이지 | 저장 정렬 구간 | aligned segments | 검증된 지식 | 확인된 upstream 상태 |
|---|---|---:|---:|---|
| 3 | 1.30–77.94s | 10 | 1 | unsupported_number1, 최소 정렬 신호0.01984 |
| 4 | 78.28–108.38s | 5 | 0 | 숫자 예제 후보2개가 unsupported_number로 거절 |
| 5 | 112.20–148.71s | 5 | 1 | unsupported_number1, 세부 기호 의미 불일치 미해결 |
| 6 | 152.02–220.06s | 9 | 0 | 추출 ValueError/unavailable, 최소 정렬 신호0 |

시간은 현재 알고리즘의 저장 결과이며 사람 검증된 정답이 아니다. 페이지 정렬을 개선하지 않았다.
0.01984/0 같은 신호는 정확도 확률이 아니다. low-signal 연결은 UI에서 확정적으로 안내하지 않고
자동 page-follow도 해당 불확실 구간이나 off-topic 시간을 단정해서 이동하지 않는다.
4/6페이지는 v2에서도 필기를 임의 생성하지 않는다. UI loss audit에 근거 검증/추출 실패를 드러낸다.
영어 zero/one과 숫자 표기 등 숫자 검증의 false rejection 가능성은 후속 source-aware 검토 대상이며
이번에는 validator를 완화해 기존 거절을 accepted로 승격하지 않았다.

### 테스트와 성능

baseline backend432/frontend25. 최종 backend445(기존+새13), frontend28(기존25+파일검증3),
production build 통과. 실제 Chromium7개 시나리오 통과, page errors0, 375/768/1280/1440 가로 overflow0.
실제 multipart job 생성은 mock 공급자 API를 이용했고, 결과 PDF/오디오 제어는 실제 강의 복사본으로
확인했다. processing/error는 명시적 상태 fixture다. microphone/타 브라우저/스크린리더는 미검증이다.

실제 `decode_audio` WAV 회귀와 PyAV18.1.0 lock 고정 검증 통과. 환경 sync/모델 다운로드 없음.
CPU 시간은 각 `report.json.cpu_seconds`에 기록했다(계획+export 포함, 요청/개별 페이지 latency와 구분).
대표 반복에서 실강의 v2 약1.76s/전체11페이지, 합성 v2 약0.24s/1페이지였다. 모델 latency/VRAM을
새로 측정하지 않았다. Qwen3-VL8B NF4 설정/SSD cache는 그대로이며 Phase D 실측을 재사용한다.
Build의 기존 큰 bundle 경고와 lint의 React hooks/fast-refresh 경고는 남아 있으며 errors는 없다.

---

# 과거 검증 — Phase D (2026-11-06)

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

## Annotation Intelligence v3 / Overview v3 — 2026-11-06

**Experimental integration is implemented; the complete quality Definition of Done is NOT satisfied. Default remains legacy.** This section supersedes older claims about the Overview's visible metadata. The real lecture was reused; no new STT or alignment accuracy improvement is claimed.

### Reproducible inputs and artifacts

Start HEAD `5a3893c`, clean `main`. Read the real 11-page lecture job `05a433631737`, 37 transcript segments, approximately 4:45 audio. The same previously validated 11 Rich ContentItems were used for A/B/C. The source job is read-only; experiments copy required inputs into their own state. Exact filenames `classNote.pdf` / `classNote (1).pdf` were not found; no claim is made to have inspected those named files.

Final artifacts: `data/annotation-v3-runs/comparison-02/`:

- `baseline.pdf`, `ai-planner.pdf`, `visual-agent.pdf`: all 12 pages, unchanged original slide order after Overview.
- `overview-before.pdf` / `overview-before.png`: previous phase output; `visual_agent-overview.png`: new study synthesis.
- `{legacy,ai_planner,visual_agent}-slide{3,4,5,6}.png`, `comparison-contact.pdf`, `contact-{0..4}.png`.
- `annotations.json`, `quality-metrics.json`, `gpu-metrics.json`, `page-mapping-tests.json`, mode-specific tool/review JSON, and isolated job state/evidence graphs.
- `frontend/`: actual browser screenshots and `browser-report.json`. Upload/progress/error requests are intercepted; PDF/API/audio/density/style/download use the actual local backend and real copied lecture.

`comparison-01/` and `overview-render-before/` preserve intermediate outputs, including a reviewer-accepted but unsupported course-context phrase and an overly repetitive overflow layout. Those are not the final recommended PDFs.

### A/B/C measurements (detailed, natural ink)

| Measured quantity | A legacy | B AI planner | C visual agent |
|---|---:|---:|---:|
| Planned annotation rows | 11 | 12 | 13 |
| Rows with visible text/ink | 11 | 9 | 10 |
| Direct ink/connector rows | 10 | 8 | 9 |
| Canonical element-to-element relations | 0 | 3 | 4 |
| Distinct source knowledge rendered / 11 | 11 | 7 | 8 |
| Visible provenance retention | 100% | 100% | 100% |
| Export pages | 12 | 12 | 12 |

All source text was retained. Geometric original-text occlusion, annotation-box overlap, connector crossing and out-of-page counts are zero on these outputs under the existing evaluator. These metrics are **not** mathematical target accuracy or human readability. Intentional highlight contact and marks within their target figure are handled separately; raster diagram label/shaft interference is not comprehensively measured. Exact normalized native-text repetition was zero in all modes, so it proves no improvement in paraphrase redundancy.

The lower distinct-knowledge retention in B/C is a regression relative to A. Detailed Notes still retain all 11 items; missing marks are observable, not silently deleted content. More relation actions do not establish overall superiority. No human gold target score, true semantic coverage, omission severity or usefulness score was measured.

### Visual inspection (Codex inspection of rendered PNGs; no independent human rating)

- Slide 3: core definition and final inverse are boxed; a duplicate underline on the final result is weak decoration. Repeated real/imaginary subexpressions were rejected instead of arbitrarily selecting occurrences. Desired component-to-zero relation annotations remain incomplete.
- Slide 4: two native imaginary-term relations show opposite signs, plus the short supported note “All signs flipped”; the example is not rewritten as a long note. Real-component/cancellation coverage is incomplete. This is a visible change, not proof of correct lecture alignment.
- Slide 5: the two colored point centers are connected using pixel-derived canonical candidates with VLM confirmation and the existing figure association. No giant circle or z/α label was fabricated over the w/γ diagram. The added connector partly follows existing vectors and can be redundant. Word underlines were suppressed by hard collision constraints. Precise axes/origin/vector semantics are not generally solved.
- Slide 6: native `(-1)z` is highlighted, with a proof-step connector. Individual common-factor links/short notes can still fail repeated-subexpression validation. The field-generalization source remains incomplete in the validated Rich inventory; this work did not fix upstream extraction.
- Overview: a coherent study page now contains purpose, goals, concepts, exact formulas and both numeric examples, without source counts/times/alignment metadata. Greek names in validated equation fields render as Greek symbols. Long study output prioritizes whole cards at 10.5pt, keeping the introduction and core formulas/examples. Full synthesis remains in JSON and Detailed Notes. Some optional review cards and formula explanations are compressed; field-generalization coverage remains incomplete.

A model critique initially accepted an unsupported “linear algebra” introduction. Visual/source inspection found it; a separate source-domain guard and regression test now reject it, followed by a new synthesis. Model review acceptance alone is insufficient evidence of factual correctness.

### Actual providers and GPU measurements

Semantic plans and synthesis use the existing Gemini provider. During experiments the configured `gemini-3.6-flash` reached its daily quota, and actual later usage records show `gemini-3.1-flash-lite`. `gemini-2.5-pro` generation returned 404 (no longer available to new users); `gemini-3.1-pro-preview` returned quota exhaustion. Neither is claimed as a successful planner. No `.env` or model weights were changed to conceal this limitation.

Reference `dev-01` final grouped execution: **19 actual 8B attempts = 16 schema-valid responses + 3 invalid review responses**. Of valid responses, three visual plans were incomplete and were not accepted as complete plans. Selective fallback kept the validated semantic state. `comparison-02` reused these prepared checkpoints; it made zero new VLM calls. Earlier exploratory runs are separate from this reference, not included as fresh calls in the final replay.

- Qwen/Qwen3-VL-8B-Instruct revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`, NF4 double quantization, SDPA, batch 1, local SSD cache.
- Peak PyTorch allocated **6.743 GiB**, reserved **6.953 GiB**, across the 16 measured valid responses. This excludes a continuously sampled whole-device/CUDA-runtime VRAM peak.
- Mean generation latency **1.812 s**, valid responses only; model loads **9.734 / 9.647 / 9.855 s**. Failed-request timing/VRAM was not retained in the aggregate and is explicitly unmeasured.
- OOM 0 in this reference. Actual revision edits 0; reviewers returned accept for valid reviews. Selective remove/retarget/rollback and the two-round bound are tested with deterministic mocks, not proven effective by this real lecture.
- Schema/input-budget failures occurred during development and were retained as fallbacks. Input payloads were reduced to page-specific candidate IDs, one copy of evidence per knowledge, and small request groups; no source evidence is truncated to fit.
- Worker closed after experiments; an observed post-run `nvidia-smi` returned 10MiB / 0% (desktop process). Model download 0. Financial cost unknown; token usage is recorded rather than inventing a price.

### Regression and limitations

Final backend **509 passed**, frontend **31 passed**, production build passed (existing large-chunk warning). Additional Chromium: **6 scenarios passed**, page errors0; see the final browser report. Existing page/audio seek and Overview offset rules remain intact. Source/audio hashes and 12-page mapping checks passed; this does not establish real alignment accuracy. PyAV **18.1.0** + faster-whisper **1.2.1** decoded the existing WAV fixture to 560,000 samples; no dependency sync/lock rewrite was performed.

High-priority remaining work: restore B/C distinct-knowledge retention without loosening validation; disambiguate explicit math derivation roles; improve strict visual JSON/completeness; make reviewer corrections effective on known faults; reduce redundant graph/mark decoration; evaluate field-generalization and true source coverage with human gold. A read-only balanced annotation render measured about 7 seconds locally (profiling identifies connector routing); rendering performance remains a product limitation.
