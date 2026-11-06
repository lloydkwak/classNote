# Codex handoff — 2026-11-06

## 인수 상태

- CWD: `C:/Users/user/Desktop/classNote`, branch `main`, HEAD `5d04fc1`.
- 시작 시 tracked/staged/untracked 변경 없음. 로컬 origin/main과 ahead/behind 0/0 (fetch하지 않은 로컬 원격 참조 기준).
- Claude의 마지막 구현은 PR #7 페이지 오디오. Phase 2 부분 구현은 발견되지 않음.
- baseline: backend 347 passed (93.12s), frontend player 25 passed, production build passed (기존 chunk size warning).
- Windows PowerShell `npm.ps1` 서명 정책 때문에 `npm.cmd` 사용. uv cache, Node AppData, NumPy DLL 접근에 sandbox 제한이 있어 승인된 실행 사용.
- 실제 PDF/audio, golden fixtures, 여러 job/cache 존재. 개인 강의 내용은 문서에 복사하지 않음.
- `lecture-turbo` 저장 similarity 재생: duration-weighted exact 31.737%, ±1 page 46.144%, backward moves 7, first page 47. golden은 Claude가 전사문에서 추론한 잠정 라벨로 사람 검증 전임.

## 시작 시 구현 매트릭스

| 기능 | 구현 여부 | 실제 파일 | 테스트 여부 | 남은 문제 |
|---|---|---|---|---|
| Page Audio Mapping | Done | backend/app/pipeline/page_audio.py | test_page_audio.py 통과 | 정렬 자체의 오류 전파 |
| Page playback state machine | Done | frontend/src/playback.ts | 25 통과 | 실제 브라우저 검증 필요 |
| Page playback UI | Partial | ViewerScreen.tsx, LecturePlayerBar.tsx, useLecturePlayer.ts | build 통과 | hook/browser 재생 실패·속도 수동 검증 |
| Content Extraction | Not started | block_align.py는 짧은 노트만 생성 | 해당 없음 | 독립 의미 단위 추출 필요 |
| Detailed Notes | Not started | 없음 | 해당 없음 | 상세 노트/출처 링크 필요 |
| Annotation Density | Not started | consolidate.py 고정 상한 5 | 기존 consolidate 테스트 통과 | 공간 기반 선택 필요 |
| Grounding Validation | Partial | block_align.py | test_notes.py 통과 | quote/숫자 검사만, 거절 기록 없음 |
| Coverage Evaluation | Not started | architecture_audit.md 수동 측정 | 해당 없음 | 추출량과 실제 강의 coverage 구분 필요 |
| Page Alignment Quality | Broken | page_align.py | synthetic 테스트 통과 | 실제 강의 31.7%, 시작 47쪽 |
| STT Word Timing | Partial | stt.py, models.py | test_stt.py 통과 | locate_quote는 segment 경계 사용 |
| Document Understanding | Partial | pdf_extract.py, mentions.py | geometry/mentions 테스트 통과 | v2 planner와 연결 없음 |
| Live Mode Compatibility | Partial | live.py, PresentScreen.tsx | 24 live tests 통과 | rich notes 미연결 |

## 진행 기록

구현 및 최종 검증 결과는 아래에 추가한다. 커밋/푸시는 수행하지 않는다.

## 이번 구현

- `page_align.py`: 거리별 후진 비용, 환경 설정 추가. e5/Viterbi 유지. `compare_alignment.py`로 같은 저장 similarity를 재평가.
- `rich_notes.py`: 13종 의미 단위, 상세 content/짧은 annotation 분리, quote·숫자·block·timing 검증, 실패 사유 기록, provider별 캐시. word timing은 해당 인용이 단어 목록에 있으면 사용하고 segment 시간으로 fallback.
- `run.py`: 오프라인 notes 단계가 추출→검증→상세 저장→balanced annotation planning을 수행. density는 저장된 상세 결과만 재사용.
- `models.py`, `consolidate.py`: 호환 가능한 optional content_id 및 annotation planning 진단 추가. 기존 v1/live 경로 유지.
- API: `/detailed-notes`, `annotations?density=...`, `annotated.pdf?density=...`. 과거 job fallback 유지. 정렬/extraction이 바뀐 rich 결과는 409. page-audio는 현재 정렬에서 계산.
- `api.ts`, `ViewerScreen.tsx`, `App.css`: 상세 노트, 원문 펼치기, timestamp 이동, content 연결 강조, density 선택, 오류/빈 상태. 폼에 포커스가 있을 때 전역 방향키가 가로채지 않음.
- `test_rich_notes.py`: 내용 보존, 거절 사유, word timing, 캐시 재사용, density 무호출, PDF export, stale alignment 검증.
- 기존 테스트는 삭제하지 않았다. 고정 5개 상한 검사는 새 요구인 가변 밀도와 충돌하여 content ID 출처 연결 검사로 교체했다. 언어·기술 용어·길이·live·export 검사는 유지했다.

## 정렬 비교 (동일 lecture-turbo, 잠정 라벨)

| backward per-page cost | duration-weighted exact | ±1 page | backward moves | first page |
|---|---|---|---|---|
| 0 (이전) | 31.737% | 46.144% | 7 | 47 |
| 0.05 | 31.737% | 46.144% | 7 | 47 |
| 0.1 | 35.384% | 49.791% | 8 | 47 |
| 0.2 (새 기본값) | 49.082% | 66.738% | 5 | 5 |
| 0.4 | 50.609% | 68.265% | 3 | 5 |

0.2는 skip cost(0.4)보다 낮게 두어 재방문을 허용했다. 하나의 데이터에서 설정을
비교했으므로 위 숫자는 held-out 성능이 아니다. 첫 페이지 5도 여전히 잘못됐다.
window count accuracy, transition/boundary accuracy, off-topic precision/recall,
page-audio precision, runtime/memory는 아직 측정하지 않았다.

재현: backend에서 `.venv/Scripts/python.exe scripts/compare_alignment.py ../data/jobs/lecture-turbo/alignment.json ../samples/golden/alignment_truth.json`.
기존 job 파일은 읽기만 했으며 새 정렬로 덮어쓰지 않았다. 사용자가 결과를 적용하려면
`uv run python scripts/run_pipeline.py --job-id <id> --from-step align`로 파생물을 재생성한다.
예전 정렬을 유지하고 상세 노트만 만들려면 `--from-step notes`를 사용한다.

## Coverage와 검증 한계

기존 감사 문서의 16.8%는 인용 발화 비율이며 의미 단위 coverage가 아니다.
새 `extracted_item_retention`은 검증을 통과한 추출 항목이 상세 노트에 보존되는 비율이고
현 구조에서는 100%이다. 추출되지 않은 개념은 분모에 없으므로 강의 전체 coverage로
보고하면 안 된다. `lecture_semantic_coverage=null`, semantic entailment=false로 명시한다.
실제 LLM 호출/새 STT/e5 임베딩 재실행은 하지 않았다. mock fixture로 구조만 검증했다.
mock provider는 기존 언어 판별/축약을 재사용하며 번역하지 못하므로 실제 LLM coverage의
대용이 아니다. 인용 일치만으로 내용의 사실 정확성이 보장되지는 않는다.

## 현재 상태 매트릭스

| 기능 | 구현 여부 | 실제 파일 | 테스트 여부 | 남은 문제 |
|---|---|---|---|---|
| Page Audio Mapping | Done | page_audio.py, run.py | 기존 16개 및 stale 정렬 API 검사 | 실강의 매핑 정확도는 정렬에 의존 |
| Page playback state machine | Done | playback.ts | 25개 통과 | 브라우저 이벤트 검증 필요 |
| Page playback UI | Partial | ViewerScreen, LecturePlayerBar, useLecturePlayer | build 통과 | 실제 재생 실패/속도/모바일 수동 검증 |
| Content Extraction | Partial | rich_notes.py | mock/provider fixture 테스트 | 실제 LLM coverage·비용, 배치 최적화 |
| Detailed Notes | Partial | detailed_notes.json, ViewerScreen | API/integration 테스트 | 유형별 섹션·수식 표시, 실강의 평가 |
| Annotation Density | Partial | plan_annotations, density_annotations | 3모드/API/PDF 테스트 | 실제 공간 점유/가독성 평가 |
| Grounding Validation | Partial | rich_notes.py, block_align.py | 거절 사유/단어 시간 테스트 | 의미적 entailment·복구 미구현 |
| Coverage Evaluation | Partial | DetailedNotes.coverage | 보존 구조 테스트 | 사람 검증 의미 단위 분모 없음 |
| Page Alignment Quality | Partial | page_align.py, compare_alignment.py | 기존 회귀 통과 | 첫 페이지·hybrid·경계 개선 필요 |
| STT Word Timing | Partial | stt.py, rich_notes.py | 단어 timing fixture | 실제 STT timestamp 품질 미측정 |
| Document Understanding | Partial | pdf_extract.py, mentions.py | 기존 테스트 | 새 v2 action planner 미구현 |
| Live Mode Compatibility | Partial | live.py, PresentScreen | 기존 live 회귀 테스트 | rich extraction은 오프라인 전용 |

## 남은 우선순위

1. 사람이 확인한 시작/전환/재방문/off-topic 라벨 확보 후 hybrid matching과 경계 refinement 평가.
2. 실제 LLM을 사용한 의미 단위 coverage/entailment 비교, 잘못 생성되거나 미추출된 이유 분석.
3. quota 중단 시 남은 span 진단·자동 복구와 추출 배치 최적화. 현재 unavailable은 다음 notes 재실행 시 캐시를 이용해 재시도한다.
4. v2 word/figure 대상 action schema와 highlight/box 등 추가. 현재 기존 sketch만 사용.
5. 실제 브라우저에서 page playback, audio error, rate, 작은 화면, presentation 검증.

Stage A 완료. B는 실제 오류 재현·부분 개선, C는 mock 검증된 core,
D/E는 기존 renderer 기반 density·상세 노트 연결까지 부분 완료다.

## 최종 검증

- backend `uv run pytest`: **352 passed**, 89.49s (기존 347 + 새 5).
- 최종 planning 진단 추가 후 관련 integration/언어 회귀 6개 재실행: **6 passed**, 4.04s.
- frontend `npm.cmd test`: **25 passed**. `npm.cmd run build`: **passed** (기존 bundle 크기 warning 유지).
- `git diff --check`: passed. 파일 삭제·기존 강의 job 갱신·의존성 추가·commit/push 없음.
- 중간 전체 검사에서 이전 고정 5개 상한 assertion 하나 실패했다. 새 공간 기반 요구에 맞게 출처 ID 연결 검증으로 교체했고 나머지 기존 검사는 유지했다.
- 브라우저 E2E/실제 LLM/new STT/e5 inference는 미실행. 실제 강의 coverage 전후 비교는 측정할 근거가 없으므로 수치 보고하지 않는다.

최종 변경: backend core/API/model/cache 연결, frontend viewer/API/CSS, 정렬 비교
스크립트, 새 regression tests, `.env.example`, 본 문서와 technology decisions.

## 다음 스프린트: 재현 가능한 실강의 평가 기반 (2026-11-06)

이전 구현은 `3bdca24`로 commit/push되었다. 이번 시작은 그 commit의 clean `main`이며,
기존 rich notes와 page playback 구현은 재작성하지 않았다.

- 기준선 재확인: backend **352 passed (82.91s)**, frontend **25 passed**, build passed.
- 새 `backend/app/evaluation/`: schema v1, verified 출처 규칙, 시간 교집합 alignment 지표,
  사람 unit 매칭 기반 notes 지표, saved/current/hybrid/refined 실험, SHA256/Git provenance,
  로컬 라벨 revision 저장과 optimistic conflict 검증.
- `backend/app/api/evaluation.py`: EVALUATION_MODE=1에서만 켜지는 개발 API.
  `frontend/src/InspectorScreen.tsx`와 `#/jobs/<id>/inspect`: PDF/audio/transcript, 실험 예측,
  구간 라벨 및 전체 dataset JSON 편집/저장/재로드. verified 저장은 별도 사람 확인 필수.
- `backend/scripts/evaluate_lecture.py`: status/init/import-legacy/benchmark/smoke/schema.
- `samples/golden/evaluation.schema.json`, `evaluation_example.json`: 개인정보 없는 버전 관리 schema와 synthetic 예제.
- 기존 provider와 rich_notes에 SDK usage, cache hit, 호출/runtime, span 진단, 탈락 후보 보존을 추가했다.
  SDK retry의 HTTP 요청 수와 과금 비용은 미확인(null)이며 provider call과 구별한다.
- 실제 데이터는 ignored `data/`에만 저장. 원본 PDF/audio/alignment/STT를 덮어쓰지 않았다.

### 실제 평가 결과와 다음 우선순위

`lecture-turbo` 잠정 21개 라벨을 provisional/legacy_inference로 가져왔다(verified=0).
기본 모드 정확도=null. 명시적 provisional 평가의 시간 교집합 exact는 saved=30.435%,
current=48.241%, hybrid=48.241%, hybrid_refined=48.241%. 이전 midpoint 지표와 분모가 다르다.
새 방식은 개선을 입증하지 못해 production 기본값을 변경하지 않았다.

실제 Gemini **1회 호출**(35초 구간): 후보 2개(definition/explanation), 모두 인용 불일치로
탈락, 최종 상세 항목 0·annotation 0. input/output/total tokens=583/419/2049, 전체 9.755초.
이후 같은 응답의 캐시 재생(외부 호출 0)과 mock 결과(항목 1개)를 분리 저장했다.
캐시 재생을 신규 실제 LLM 호출로 보고하지 않는다. 사람 의미 검토와 실제 coverage는 미측정.

다음 P0는 첫 페이지/입력 정렬 검증과 source_quote_not_found 원인 확인이다. 검증 규칙은
완화하지 않았다. 이후 exhaustive meaning review range와 unit↔item 매칭, transition/revisit/
off-topic gold를 사람이 작성하고 별도 test 강의에서 실험을 반복한다.

전체 사용법, 지표 정의, 로컬 artifact 위치 및 제약은 `docs/evaluation_report.md`에 있다.
브라우저 E2E/실제 사람 verified 라벨 생성은 미실행이며 자동 verified 생성 경로는 없다.

최종 검증: backend **369 passed (189.68s)**, frontend player **25 passed**, production build
passed(기존 bundle warning), diff whitespace 검사 통과. notes-run 연결은 마지막 출처 검사
integration test로 별도 재검증했다. 이번 변경은 미커밋이며 새 의존성/기존 파일 삭제 없음.
