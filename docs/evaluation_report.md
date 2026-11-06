# 실제 강의 평가 기반 — 2026-11-06

## 범위와 결론

시작 commit `3bdca24` / `main`, 시작 작업 트리 clean. 기존 정렬·Rich Notes·플레이어를
재작성하지 않았다. 새 의존성이나 모델은 추가하지 않았다. 이 스프린트는 **평가 기반과
실패 재현**을 완성한다. 사람이 검증한 실제 강의 라벨이 없어 일반화 성능을 주장하지 않는다.

## 데이터셋

`backend/app/evaluation/schema.py`가 schema_version=1의 기준이다.
`samples/golden/evaluation.schema.json`과 `evaluation_example.json`은 JSON Schema와
직접 만든 synthetic 예제이며 개인 강의 원본은 포함하지 않는다.

기존 `alignment_truth.json`은 `{start,end,pages,note}` 잠정 추론 21개이고,
`p15.json` 등은 annotation action 예시다. 둘 다 사람 검증을 나타내는 metadata가 없으므로
verified gold로 취급하지 않는다. legacy import는 반드시 provisional/legacy_inference다.

데이터셋에는 ID/version/revision/split, 입력 식별자·SHA256, 페이지 구간(복수 페이지와
빈 목록=off-topic), 독립 transition 시점·revisit, 의미 단위·인용·시간·segment ID,
추출 항목과 의미 단위 사이의 사람 매칭(relevant/entailed/duplicate_of)을 저장한다.
`meaning_review_ranges`는 **그 구간의 의미 단위를 빠짐없이 열거했음을 검토한 범위**다.
단위 몇 개를 작성한 것만으로 강의 전체 recall의 분모가 완성되지는 않는다.

상태는 draft/provisional/uncertain/verified. verified에는 human source, 작성자, timezone이
있는 검토 시각이 필요하다. API 저장은 별도 사람 검증 확인도 요구한다. uncertain/draft는
항상 지표에서 제외되고 provisional은 명시적 flag를 줄 때만 포함된다. 서버는 검증자를
인증하지 않으므로 localhost에서 사용하는 개발 도구다.

라벨 저장은 `data/jobs/<id>/evaluation/labels.json`과 변경 불가 revision 파일을 만든다.
revision 충돌, 입력 hash 변경, 잘못된 페이지, 겹치는 구간은 거절한다. 기본 데이터 파일은
Git ignore 대상이다. 저장 lock은 단일 서버 프로세스용이다. 여러 worker로 실행하지 않는다.

## Inspector 사용

PowerShell에서 backend 서버를 켠다 (기존 `.env`를 편집할 필요 없음):

```powershell
cd backend
$env:EVALUATION_MODE = '1'
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

frontend 서버에서 `#/jobs/<job-id>/inspect`로 이동한다. 기존 정렬 차트에도 링크가 있다.
기능: PDF 페이지, 원본 오디오 controls, 시간별 전사문·seek, saved/current/hybrid/refined
예측 비교, 구간 라벨 추가, JSON 편집/저장/재로드, Rich Notes·탈락 후보 보기.
기존 라벨 수정·분할과 meaning_units/transitions/matches는 JSON 편집기로 제공한다.
verified로 바꾼 항목을 직접 검토한 후 체크박스를 선택해야 저장된다. 서버가 자동으로
verified를 붙이는 경로는 없다. production에서는 EVALUATION_MODE를 켜지 않는다.

## 반복 실행 CLI

backend에서 실행한다. 기본 출력은 기존 데이터를 덮어쓰지 않는 ignored 경로다.

```powershell
uv run python scripts/evaluate_lecture.py init --job <id>
uv run python scripts/evaluate_lecture.py import-legacy --job <id> --legacy ../samples/golden/alignment_truth.json
uv run python scripts/evaluate_lecture.py benchmark --job <id>
uv run python scripts/evaluate_lecture.py benchmark --job <id> --allow-provisional
uv run python scripts/evaluate_lecture.py benchmark --job <id> --dataset labels.json --glossary glossary.json --lexical-weight 0.25
uv run python scripts/evaluate_lecture.py benchmark --job <id> --notes-run ../data/evaluation-runs/<run-id>
uv run python scripts/evaluate_lecture.py status
uv run python scripts/evaluate_lecture.py smoke --job <id> --seconds 60
uv run python scripts/evaluate_lecture.py smoke --job <id> --seconds 60 --real
```

init/import는 기존 라벨이 있으면 덮어쓰지 않는다(import는 기존 alignment label이 있으면 거절).
`--dataset`은 같은 강의 ID와 파일 hash에 맞아야 한다. 결과에 dataset version/revision/hash,
config/glossary/variant, artifact hash, Git commit/dirty/code hash, 선언한 embedding 모델,
실행 시간이 남는다. 저장 similarity만으로 원래 모델 버전을 추정하지 않는다. 확인했다면
`--embedding-model`로 명시한다. report 파일은 자동으로 Git에 추가하지 않는다.

## 정렬 지표 정의와 실측

- exact/±1: 예측 구간과 정답 구간의 **시간 교집합**만 가중한다. 라벨 공백을 분모에 넣지 않는다.
- transition accuracy: 정답 from/to와 일치하는 예측 전환이 ±5초에 있는 비율(transition recall).
  예측 하나를 두 라벨에 재사용하지 않는다. 다중 페이지는 허용 집합, off-topic은 빈 집합이다.
- boundary MAE: 위 조건으로 매칭된 전환의 절대 시간 오차. 미매칭 전환 수와 tolerance를 함께 보고한다.
  따라서 MAE만 작다는 이유로 전환을 잘 찾았다고 판단하지 않는다.
- first page: 강의 시작부터 실제 첫 on-topic 구간까지 라벨 coverage가 있을 때만 비교한다.
- off-topic precision/recall, 명시된 revisit transition accuracy, confidence 4개 구간별 시간 가중 오류율.
- label count, verified count, labeled/evaluated seconds, 예측 timeline 대비 coverage를 함께 기록한다.
  이 coverage는 실제 음성 파일 전체 길이와 다를 수 있다.
- 분모가 없으면 0 대신 null. 정식 모드와 provisional 모드를 분리한다.

실제 `lecture-turbo-evaluation`, version `0.1.0`, revision 1:

| 모드 | 라벨 | 평가 초 | exact | ±1 |
|---|---:|---:|---:|---:|
| verified 기본 | 0 | 0 | null | null |
| provisional / saved | 21 | 2003.44 | 30.435% | 46.386% |
| provisional / current E5+Viterbi replay | 21 | 2003.44 | 48.241% | 66.359% |
| provisional / hybrid | 21 | 2003.44 | 48.241% | 66.359% |
| provisional / hybrid_refined | 21 | 2003.44 | 48.241% | 66.359% |

라벨 구간 합은 2019초, 예측과 겹치는 범위는 2003.44초(예측 timeline의 93.86%)다.
이전 문서의 49.082%와 다른 이유는 이전 스크립트가 **window midpoint 라벨로 window 전체를
채점**했기 때문이다. 이번 지표는 정확한 시간 교집합만 채점한다. 모델 성능이 변한 수치가 아니다.
transition/revisit/off-topic 정답과 강의 처음부터의 라벨은 없어 관련 recall·boundary·first-page는
null이다. provisional에서 off-topic precision=0은 라벨된 on-topic 구간을 off-topic으로 예측한
시간이 있음을 뜻하며 off-topic 정답이 없어서 recall은 null이다.

새 두 실험은 이 샘플에서 개선이 없어 **기본값에 적용하지 않았다**. hybrid는 저장된 semantic
score에 제목/본문 lexical overlap과 선택적 glossary를 더한다. refined는 저신뢰 전환만 ±10초
이내 segment 경계로 이동한다. 새로운 semantic embedding, 강제 순차 정렬, 새 모델은 없다.
같은 강의로 조정·평가했으므로 held-out/generalization 성능이 아니다.

원본 정렬·PDF·audio·STT 파일은 변경하지 않았다. 실제 결과는
`data/jobs/lecture-turbo/evaluation/report-1793933476006187000.json`(verified)과
`report-1793933478013829600.json`(provisional)에 있다.
최종 코드로 재실행한 보고서는 같은 디렉터리의 `sprint-verified.json`, `sprint-provisional.json`이다.

## Rich Notes 평가

`notes_metrics`는 사람이 매칭한 unit↔item을 기준으로 recall/검토된 항목 precision,
의미적 entailment 검토 결과, 상세 노트 보존 unit, 주요 unit PDF 반영, duplicate review,
반복 annotation ID, 거절 이유, 누락 span을 출력한다. 의미 매칭 후보는 시간 겹침과 텍스트
유사도이며 **자동 verified나 의미적 정답이 아니다**. 한국어/영어 paraphrase는 사람이 판단한다.

모든 추출 항목의 매칭 검토가 끝나지 않으면 extraction_recall=null이고 lower bound를
별도로 제공한다. precision/factuality는 검토된 분모 수를 표시한다. 의미 단위를 빠짐없이
확인한 review range와 실제 unit이 없으면 recall은 null이다. 인용 검사는 전체 전사문과
해당 timestamp 구간을 따로 검사한다. `extracted_item_retention`은 강의 coverage로 사용하지 않는다.

Rich extraction에 provider 호출 수(HTTP retry 횟수와 구분), cache hit, provider runtime,
SDK token usage events/합계, span별 accepted/rejected/empty/unavailable을 추가했다.
SDK가 usage를 주지 않았거나 예전 캐시라면 token/cost는 null이다. 실제 비용을 추정하지 않는다.
거절 후보는 detailed_notes.json에 보존해 원문과 비교할 수 있다. 개인 내용은 Git에 넣지 않는다.

## 실제 LLM smoke test

키 값은 출력하지 않고 존재 여부만 확인했다. Gemini 설정 키가 있고 DEMO_MODE=false여서
**실제 호출 1회**를 실행했다. 기존 job의 4.62–39.62초(35초), 저장 extraction/STT/alignment를
재사용했다. 이 입력의 저장 예측 페이지는 47이며 올바른 정답으로 확인된 값이 아니다.

| 항목 | 결과 |
|---|---|
| 실제 모델 | gemini-3.6-flash (응답 usage event 기준) |
| 외부 provider 호출 | 1 |
| provider 시간 / 전체 시간 | 8.383s / 9.755s |
| input / output / total tokens | 583 / 419 / 2049 (SDK가 보고한 값 그대로; total을 단순 합산하지 않음) |
| 검증 전 추출 후보 | 2 |
| 후보 유형 | definition 1, explanation 1 |
| 검증 통과 / PDF annotation | 0 / 0 |
| 탈락 이유 | source_quote_not_found 2건 |
| 실제 비용 | null (과금 내역 미조회) |
| 사람 의미 검토·recall·precision | 미실행 / null |

실제 호출 artifact: `data/evaluation-runs/20261106T024612849535/`.
초기 manifest의 outcome=complete는 실행 완료를 뜻했으나 통과 항목 0인 결과와 혼동 가능하여
CLI에 completed_no_grounded_items 상태를 추가했다. 같은 실제 응답 캐시를 재사용한
`20261106T025212620340/`에는 탈락 후보, 원본 hash, PDF를 보존했다(새 외부 호출 0).
`20261106T025214784757/`는 **mock**이며 explanation 1개, annotation 1개, 외부 호출 0이다.
mock 성공은 실제 LLM 품질의 증거가 아니다. PDF 생성 성공도 annotation 품질의 증거가 아니다.
`--notes-run`으로 smoke 결과를 원본 job의 데이터셋과 함께 평가할 수 있다. source job/hash를
확인하며 실제 job에 smoke 파일을 복사할 필요가 없다.

## 다음 오류 우선순위

1. **P0: 시작 페이지와 입력 정렬** — 첫 60~120초를 사람 검증하고 잘못된 페이지가 추출에 미치는 영향을 분리한다.
2. **P0: source_quote_not_found** — 보존된 탈락 후보와 STT를 비교한다. 구두점 정규화 범위를 넘어서는 인용 오류를 확인하고, 검증을 느슨하게 하지 않는 근거 회복을 설계한다.
3. **P1: 의미 단위 gold** — 정의/이유/예제를 빠짐없이 라벨링한 짧은 범위와 전체 item match review를 만든다.
4. **P1: 전환·재방문·off-topic gold** — 독립적인 boundary 시각 라벨을 확보한다. 기존 window 라벨에서 정답 transition을 자동 생성하지 않는다.
5. **P2: hybrid/glossary/refinement** — development에서만 조정하고 별도 test 강의에서 재평가한다. 현재 실험은 기본값 유지.

브라우저 수동 조작·실제 사람이 작성한 verified 데이터셋·실강의 의미적 사실성 평가는
아직 수행되지 않았다. API 회귀 테스트는 라벨 저장·재로드·충돌·명시적 검증 확인을 검증한다.

## 테스트 결과

- 수정 전: backend 352 passed (82.91s), frontend player 25 passed, production build passed.
- 수정 후: backend 369 passed (189.68s); 평가 관련 새 테스트 17개 포함.
- 마지막 notes-run 연결은 출처 일치/불일치 integration test로 별도 재검증했다.
- frontend player 25 passed, 최종 TypeScript/Vite build passed. 기존 bundle size warning은 유지.
- 중간 경계 테스트 fixture는 두 페이지 z-score가 항상 높은 confidence를 만드는 조건이라,
  실제 저신뢰 조건인 세 페이지 near-tie fixture로 교정했다. production 기준을 낮춰 통과시키지 않았다.
- `git diff --check` 통과. commit/push는 이번 스프린트에서 수행하지 않았다.
