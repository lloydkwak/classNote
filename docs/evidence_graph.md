# Phase D — production evidence graph (2026-11-06)

기본 annotation 경로는 그대로다. 오프라인 job의 `visual_grounding=true`일 때만 기존
Rich Notes → Phase C ID 선택 → 기존 layout/export를 연결한다. graph DB, 새로운 의미 추출
LLM, 새 모델/문서 패키지는 추가하지 않았다. 실강의 원본은 이 clone에서 발견하지 못했다.

## 실행 경로

```text
run_pipeline: extract → caption → transcribe → window → align → page_audio → evidence
  → notes: extract_content / 기존 quote·숫자·시간 검증
    → prepare_grounding: 페이지 registry·SoM / ContentItem → GroundingItem
    → IsolatedVLMProvider: backend Python → .venv-vlm JSONL → GPUWorkerProvider
    → select_targets → backend에서 validate_selection 재실행
    → plan_grounded: 기존 density planner + accepted canonical geometry
  → consolidate: 기존 layout_page + 정밀 target bbox
  → export: 실제 PDF ink·margin text + provenance attachment
```

`app/pipeline/grounded_notes.py`가 통합 지점이다. 요청은 한 페이지의 지식 항목 하나와
관련 발화/정렬 windows만 포함한다. 이미지 하나, batch 1, 524288 pixels, input 4096 tokens,
output 512 tokens를 사용한다. 긴 입력은 잘라내지 않고 `InputBudgetExceeded`로 기록한다.
예산에 못 들어간 항목은 상세 노트와 기존 planner로 보존한다. 페이지 전체 의미 추출을
VLM에 다시 시키지 않으며, 한 item에 해당하는 원문을 임의로 요약하지 않는다.

## 스키마와 저장 파일

`app/evidence.py`: version 1 `EvidenceGraph`, `AnnotationAction`.
`app/models.py`: optional version 1 `AnnotationProvenance`를 Note/Annotation에 추가했다.
기존 JSON에 이 필드가 없어도 읽을 수 있다. 페이지는 1부터, segment index는 0부터다.

| 파일 (job 내부) | 실제 내용 |
|---|---|
| `job.json` | per-job feature flag, STT 옵션 |
| `detailed_notes.json` | 기존 ContentItem/검증 결과 + PDF/transcript/정렬/extraction hashes |
| `grounding_results.json` | sources/fingerprint, 페이지 registries, 항목별 요청·raw selection·재검증 decision·실패·telemetry |
| `annotation_actions[-density].json` | 선택 여부를 포함한 모든 지식 항목의 action; density 제외 항목도 보존 |
| `evidence_graph[-density].json` | recording/segment/alignment/knowledge/decision/element/action/render nodes와 edges |
| `annotated.pdf`, `annotated-<density>-<key>.pdf` | 실제 export; `.pdf.sha256`로 파일 손상 확인 |
| `vlm-stderr.log` | 모델/라이브러리 진단; stdout JSONL과 분리, 개발자용 로컬 파일 |

balanced graph/action은 suffix 없는 이름으로도 저장한다. raw 데이터는 기존 ignored `data/`
아래에만 둔다. API는 transcript 전체와 registry 전체를 추가로 반환하지 않는다.

Action 필드: `action_id`, `knowledge_id`, `evidence_id`, `source_segment_ids`, `source_quote`,
`timestamp_range`, `page`, `target_element_id|null`, `target_kind|null`, `grounding_status`,
`annotation_type`, `annotation_text`, `priority`, `density`, `registry_fingerprint`, `selected`,
`reason`, `provenance`. 지원 mark는 실제 renderer의 `underline/circle/bracket/none`이다.
기존 connector와 margin text를 사용한다. box/highlight/custom warning glyph/formula typesetting을
새로 구현했다고 주장하지 않는다. exam_hint의 기존 강조/색상은 유지한다.

확정된 symbol/figure는 circle, derivation/reasoning은 bracket, 그 외는 underline이다.
기존 Rich type·block relation·importance·space budget을 사용하며 새 keyword-only LLM planner는 없다.
긴 설명은 기존 상세 content에 남고 PDF에는 기존 `short_note`를 쓴다.

## 실제 연결 예시 (합성 입력, 실제 8B)

`data/phase-d-runs/gpu-e2e-03`의 저장 결과:

```text
job: phase-d-synthetic / recording.wav (silence fixture, STT/semantics fixtures)
segment 0, 0.0–4.0s: "gradient gives the ascent direction"
  → ContentItem concept_0ed6f5d1d9675ace_0
  → knowledge_bba3bf0f888c4884cc87a035
  → grounding_228d885b08a8dbd031aaf147 (accepted)
  → canonical p1_w4, registry
    162e420e4d550983e914fef937d8289cdb39a5a38364bf068c95631fd402bc09
  → action_66903b137b25d50ad8fcba19, underline + margin note
  → annotation.id == action_id → rendered_annotation node → annotated.pdf + sha256
```

실제 timestamp/원문/ID는 annotation API, PDF ink의 `info.content`, PDF에 첨부한
`classnote-evidence.json`에도 유지된다. 이 예시는 실강의 녹음의 인식 성공을 뜻하지 않는다.

Graph는 dangling edge와 중복 node ID를 거부한다. source segment가 없거나 비연속이면
worker 요청을 만들지 않는다. 단어 timing이 있으면 기존 Phase C quote/time 검증을 그대로 쓴다.
같은 지식의 ContentItem ID와 job/content 기반 evidence ID를 구분한다. ID만 같다고 원본
PDF가 같은 것으로 처리하지 않으며, canonical element와 registry fingerprint를 함께 검증한다.

## 좌표와 비확정 target

registry bbox는 Phase C의 **unrotated crop-local normalized** 좌표다. 모델 좌표는 받지 않는다.
accepted ID → registry element bbox → PDF points → 원본 rotation matrix → displayed normalized
geometry → 기존 layout → export gutter offset으로 변환한다. native character bbox도 이 경로다.
`extract_display_pdf`는 원본을 읽고 임시 unrotated 사본에서 기존 extractor를 호출한다.
v2 geometry/mentions에도 같은 변환을 사용하고 registry hash를 저장한다.

`ambiguous/rejected/unmatched`는 target bbox와 connector 없이 page note로 남는다.
후보와 raw 응답은 grounding manifest에 유지하며 가장 가까운 단어로 보정하지 않는다.
`unavailable`(GPU/JSON/예산/IPC 장애)은 기존 planner anchor를 사용할 수 있지만 canonical
ID는 null이며 `fallback_reason`이 붙는다. figure의 accepted block 연결은 그림 의미 전체를
검증했다는 뜻이 아니다. 상세 지식을 삭제하지 않는다.

## 캐시/재현/무효화

sources에는 PDF, segments, alignment, extraction, 지식 JSON, registry builder/parser 코드,
mentions, model/checkpoint/prompt/runtime/schema 및 validation 정책 fingerprint가 포함된다.
model은 Qwen3-VL-8B-Instruct revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`로 고정한다.
완료 resume는 worker를 시작하지 않고 raw selection을 재검증한다. 부분 resume는 동일 요청의
검증 checkpoint를 재사용한다. 수정된 입력은 기존 provider의 요청별 image/prompt/context cache를
통하므로 관련 없는 STT·LLM·VLM을 무조건 다시 실행하지 않는다.

| 변경 | 처리 |
|---|---|
| density | 상세 추출·grounding 재사용; action/layout/export만 갱신 |
| font/style/layout/action policy | grounding 재사용; action 또는 render revision/export key 변경 |
| validation 정책 | 저장 raw 응답을 현재 validator로 재검증; 재추론 불필요 |
| model/revision/prompt/runtime/schema | 기존 mapping stale; notes resume 시 해당 요청 재처리 |
| PDF/registry parser/mentions/knowledge | source mismatch; 기존 확정 mapping 재사용 금지 |
| transcript/alignment/extraction/PDF 변경 | 상세 노트 API/export에 409; 적절한 단계부터 CLI resume |

manifest mismatch만으로 API가 GPU를 호출하지 않는다. 기존 상세 데이터가 유효하면
target 없는 fallback으로 export할 수 있고, 상세 데이터도 stale이면 409로 중단한다.
지식/소스가 같은데 density만 달라진 경우 grounding manifest bytes가 유지됨을 테스트했다.
PDF export는 source/annotation/render code key와 파일 digest를 확인하며 JSON/PDF 임시 파일은
호출마다 구별한다. 입력 PDF는 항상 읽기 전용이다.

## IPC와 GPU lifecycle

`.venv-vlm/bin/python backend/scripts/vlm_worker.py --root <trusted-job>`만 기본 실행한다.
venv interpreter symlink를 resolve해서 system Python으로 바꾸지 않는다. JSON/파일 경로를
schema로 검사하고 job 밖의 파일 및 symlink escape를 거부한다. shell 실행을 사용하지 않는다.
프로토콜 FD를 복제하고 fd 1을 stderr로 돌려 native loader/하위 worker도 stdout을 오염시키지 않는다.
크기 제한 8MiB, queue/write/read deadline, cancellation event, 소유한 process group TERM/KILL,
wait/pipe close가 있다. timeout/cancel/child crash 후 GPU child가 남지 않도록 정리한다.
실패 로그는 발화 본문을 의도적으로 출력하지 않고 오류 종류/측정값만 protocol에 전달한다.
GPU worker의 raw model 출력 실패 기록은 로컬 개발 artifact로 취급한다.

GPUWorkerProvider의 lazy model·serialization·OOM 후 작은 입력 1회 재시도를 재사용한다.
오프라인 opt-in GPU STT도 같은 `data/gpu.lock`을 사용하며 단계 끝에 모델을 unload한다.
e5는 기존 코드에서 CPU다. 모든 legacy/live GPU 실행이 중앙 scheduler로 전환된 것은 아니다.
기본 live 경로는 VLM을 실행하지 않으며, 다른 프로그램이 점유한 GPU를 임의 종료하지 않는다.
이 환경에서 실제 Whisper GPU 추론은 실행하지 않았고 lease/unload는 mock으로 검증했다.

## API와 재실행

기존 `POST /api/jobs` multipart에 optional `visual_grounding=true`를 전달하거나
`VISUAL_GROUNDING=1`로 새 오프라인 job의 기본값을 선택한다. 기본값은 false다.
CLI는 `--visual-grounding` / `--no-visual-grounding`을 지원한다. 기존 job을 활성화할 때는
좌표계 일관성을 위해 `--from-step extract`부터 실행한다. 상세 예: [Ubuntu runbook](ubuntu_vlm.md).

기존 `annotations?density=...`는 optional note.provenance 및 root.grounding을 반환한다.
`detailed-notes`의 기존 items, quote, time은 유지한다. 추가 endpoint는 없다.
Viewer의 원문 펼치기에서 canonical ID 또는 비확정/fallback 상태를 볼 수 있다.
별도 upload UI toggle과 live visual grounding은 이번 범위에 포함하지 않았다.

검증 및 시각적 한계: [annotation quality report](annotation_quality_report.md).
