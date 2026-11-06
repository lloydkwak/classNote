# Advanced annotation — Phase A/B, 2026-11-06

## Annotation Intelligence v3 / Overview v3 — 진행 계획 (2026-11-06)

시작 HEAD `5a3893c`, clean main. GPU3080 10GB/driver550.163.01/여유53GiB 확인.
기존 real-reviewed 산출물과 원본05a job을 재사용하며 새 모델/venv/의존성 설치는 하지 않는다.
기존 Overview는 short_note의 페이지별 결합이고 choose_action은 item당1개이므로,
whole-lecture synthesis와 여러 typed action을 갖는 독립 experimental 경로가 필요하다.

1. 기존 provider 기반 semantic planner, 원문 중복/숫자/공식/evidence 검증, native math subelement graph.
2. 내부 Annotation Tool API: allowlisted tools, ID/provenance/hash 검증, action store/idempotency/rollback.
3. 기존 isolated8B worker에 visual plan/review 작업을 추가. 한 이미지/batch1/lease/OOM 복구 재사용.
4. production notes→actions→layout/export 연결, multiple actions/knowledge graph 보존,
   legacy/ai_planner/visual_agent 선택 및 실패 fallback, density/style의 불필요한 재추론 방지.
5. 전체 강의 synthesis Overview와 metadata 없는 학습용 첫 페이지, 기존 source/export/audio mapping 유지.
6. 동일 saved transcript/alignment/knowledge의 A/B/C PDF, 실제 LLM/8B와 mock 구분,
   exported word collision 및 이미지 직접 검토, 회귀/문서/한계 기록.

아직 위 단계의 구현/검증은 완료되지 않았다. 자세한 상태는 codex_handoff 최상단을 갱신한다.


## 긴급 후속: Slide-first + Lecture Overview (구현/검증 완료 범위)

시작 `148e246` clean. 지정된 `classNote.pdf`, `classNote (1).pdf` 이름은 repo 전체 PDF 검색에서
찾지 못했다. 같은 원본 hash의 실제 job `05a433631737`, `23ce2281310c`에는 검증된 지식6개와
6페이지 설명이 존재한다. 이전6c job은4개/partial이다. 원본 대신 복사본으로 비교한다.

현재 원인: 내부 후보에0.8×page-width 페널티, 고정33% gutter, limited mark policy,
voiced math/숫자 표기와 native text의 불일치 및 일부 job의 grounding 미실행.
새 기본은 slide-first. native canonical ID 검증→의미별 action→내부 free-space 우선→margin fallback.
Overview는 validated Rich Notes 전부를 입력으로 density와 독립적으로 생성하고 첫 export page에
삽입한다. 기존1-based source page API는 유지하고 source_slide_index(0-based)와
export_page_index(overview0,source0→export1)를 별도 명시한다. audio index는 절대 이동하지 않는다.

근거 없는 거절 승격/임의 좌표 금지. 작은 표기 정규화는 정확한 source quote 재검증 및
negative/ambiguity 테스트로만 도입한다. 기존 GPU환경·PyAV18.1.0·데이터·모델은 보존한다.

## 후속 최종 상태

- 기존 production `grounded_notes → consolidate → layout_v2 → export_density`에 slide-first를 기본 적용.
  semantic action, native Symbol font 해석/분산 수식 glyph 결합, strict target validation 유지.
- 모든 native word를 layout-only obstacle로 보호. 실제 PDF에서 발견된 계산식 겹침과
  혼합 글꼴 baseline 문제 수정. margin은 fallback이며 이번 실제 11개 메모는 모두 내부 배치.
- density와 독립적인 source-validated Overview를 모든 export 첫 장에 삽입.
  source/API/audio 번호 보존, exported index 분리, legacy/density/재export 호환.
- 실제 최신 job은 저장 후보 재검증 6→11, 8B accepted9/ambiguous2, 직접 강조8.
  원본11→최종12장, 원본 bytes/content와 page audio 보존. 새 STT/정렬/의미 호출0.
- backend474/frontend31/Chromium7/build 통과. GPU 최종새호출11/OOM0/peak7.23GiB.
- 결과: `data/slide-first-runs/real-reviewed`. 비교/실패/한계는 품질 보고서 최상단 참조.
- 3/6페이지 정렬, 잘린 field quote, 그림 내부 label/벡터 grounding은 남는다.
  Phase F의 human gold와 실강의 semantic coverage를 완료했다고 주장하지 않는다.

## 이전 Phase E 상태 (역사 기록)


시작 HEAD `8345a5f`, clean main. 실제 회귀 job `6c094c2fe7cc`의11페이지 PDF/녹음/37segments를
찾았고 복사본에서 같은 저장 source/action의 v1/v2 PDF를 비교했다.

- Layout v2 opt-in: native occupied/free-space 후보, seeded ink, bounded A* routing,
  hard collision 배제, figure 세부 미확정 mark/connector 생략, 원본 크기 유지/확장 note band.
- density threshold를 유지하고 v2 physical-space 예산으로 고우선순위 메모를 삭제하지 않음.
  source/action/target/provenance 및 density/style/render cache 분리 유지.
- 실제 React 기본 UI 교체: classNote dark upload, 실제 status polling/retry,
  thumbnail/PDF/상세 노트, audio, density/style/download/zoom, 반응형. Live/Presentation 보존.
- PyAV18.1.0 pin + av만 변경한 lock, 실제 decode_audio WAV 회귀. 환경 sync 없음.
- 실제4개/합성7개 선택 필기·출처 보존. 합성 connector교차1→0, caption간섭2→0.
  실제 강의 그림 원 제거. 원본 추출 text/bytes/페이지 수 보존 확인.
- backend445, frontend28, Chromium7 scenarios, build 통과. lint 경고는 남음.
- artifact: `data/phase-e-runs/final-real`, `final-synthetic`, `frontend-final`, `rotation-review`.

실강의4페이지 unsupported_number2, 6페이지 추출 ValueError는 해결하지 않고 UI/audit로 드러낸다.
5페이지 z/α/β 설명과 w/γ/δ 그림의 의미 불일치도 미해결이다. 정렬 개선이나 실강의 semantic
coverage 개선을 주장하지 않는다. continuation pages/전역 solver/이미지 내부 geometry는 후속 범위다.

상세: [layout_v2.md](layout_v2.md), [frontend_design.md](frontend_design.md),
[기술 선정](phase_e_technology_decisions.md), [품질 보고서](annotation_quality_report.md).

---

아래는 Phase A–D 당시 계획/실측 기록이다.

## 현재 상태와 게이트

- 시작: clean `main`, `28b3aa3`. GitHub `ls-remote`도 동일 SHA 확인.
- Ubuntu 24.04.4, i9-12900, RAM 62 GiB, Python 3.12.3, Node 20.20.2.
- PCI: GA102 RTX 3080 (`10de:2206`). 커널 NVIDIA 550.163.01.
  초기 sandbox의 `nvidia-smi`/CUDA 접근은 실패했으나 승인된 호스트 실행은 정상.
  RTX 3080 10240 MiB, compute capability 8.6, CUDA 12.4, torch 2.6.0+cu124 확인.
- 디스크 시작 여유 약 18 GiB. 원본 checkpoint와 환경 설치에 부족할 수 있다.
- backend uv.lock 및 frontend package-lock 존재. uv/프로젝트 venv/node_modules 없음.
  Node 22+ 요구와 현재 Node 20의 차이도 baseline에서 확인한다.
- 기존 Page Audio/state machine, Rich Notes/density, grounding 검사, evaluation schema와
  Inspector 구현 확인. 테스트 실행 결과는 품질 보고서에 기록한다.
  과거 문서의 369/25 통과는 이번 환경에서 실행한 결과와 구분한다.
- `pdf_extract.py`에 word/line ID, OCR 출처, free-space, marked image가 이미 있다.
  registry/SoM를 중복 구현하지 않는다. 기존 layout/export/cache/resume 유지.

## 변경 계획 (구현 전 기록)

1. baseline 테스트 환경을 workspace 안에서 구성하고 기존 테스트/build 실행.
   기존 lock을 임의로 재해결하지 않는다. 실제 registry에 lock 버전이 없으면 실패 기록.
2. 격리된 VLM 환경 명세 및 preflight 작성: driver/CUDA/API/disk 진단,
   다운로드 전 공식 HF manifest 용량 검사, 결과 JSON 저장.
3. Phase B용 실험 provider와 별도 단일 inference worker 구현.
   Qwen3-VL-8B-Instruct 고정, NF4/double quant/SDPA/batch=1,
   lazy load, 입력/출력 budget, timeout/process cleanup, OOM 후 작은 입력 1회 재시도,
   cache와 deterministic mock. API에 자동 통합하지 않는다.
4. PDF 한 장 PNG 렌더→제목/텍스트/수식/그림 JSON 검사 smoke runner 및 측정 산출물.
   실제 호출 두 번(캐시 우회), GPU peak/load/latency/tokens/RAM 기록.
   합성 PDF도 synthetic으로 명시. 텍스트 일치 검사는 의미 정확성 평가와 구분.
5. worker 실패/timeout/OOM/cache/순차 실행을 mock으로 검증하고 기존 회귀 실행.
6. 실제 8B GPU smoke 통과 전 Phase C–F 구현·production 기본값 변경 보류.
   driver/device 접근 실패를 실제 모델 로딩 성공으로 보고하지 않는다.

## 이후 진행 순서

- C: 기존 geometry의 회전/crop 좌표 검증 → 문서 도구 비교 → element selection/SoM.
- D: 기존 rich evidence/provenance에 target registry/coverage 연결, 정렬 hash 무효화.
- E: layout free-space/collision 개선, 공간 부족 시 상세 노트·확장 PDF.
- F: 동일 실강의 A/B, 사람이 검토한 gold로 위치/내용/가독성 평가.

기존 원본 강의·키·캐시 삭제/변경, 모델 교체, 시스템 드라이버 설치/재부팅은 하지 않는다.
환경 제약을 해소한 뒤 실행할 절차와 미완료 항목을 handoff에 남긴다.

## Phase A 실행 결과

- GitHub main 일치 확인, 기존 문서/코드 인수 완료.
- `uv sync --frozen --python /usr/bin/python3`: 성공. lock 수정 없음.
  backend 환경은 torch 2.15.0/CUDA 13 의존성으로 VLM과 반드시 격리한다.
- backend: 380 passed (기존 369 + 새 VLM 11), 39.77s. sandbox TestClient 정지로
  host에서 실행. 원본 강의 없이 mock API/PDF/export/density/page-audio/평가 회귀 검증.
- frontend Node 20 테스트 시작 실패 후 workspace Node 22.23.4로 25 passed.
  Node 22 production build 성공(기존 bundle warning).
- VLM 별도 환경: torch 2.6.0+cu124, torchvision 0.21.0+cu124,
  transformers 4.57.6, accelerate 1.12.0, bitsandbytes 0.49.0.
  `Qwen3VLForConditionalGeneration`, `AutoProcessor`, NF4 config 실제 import 성공.
- 공식 checkpoint manifest 17.55 GB로 디스크가 부족하여 승인된 `/dev/shm/classnote-hf`
  (32 GiB tmpfs)에 다운로드. RAM 캐시이므로 재부팅 후에는 재다운로드가 필요하다.

Phase B 실제 측정과 게이트 상태는 `gpu_benchmark.md`를 기준으로 한다.

최종 회귀(추가 hard crash/invalid JSON telemetry 포함): **backend 382 passed, 36.14s**.

## Phase B 실행 결과 및 다음 게이트

- 공식 8B checkpoint 다운로드 완료(17,545,907,231 bytes), 실제 NF4 GPU 로딩 확인.
- 정상 same-page smoke 2/2 schema 통과. load 9.562s, 첫 요청 15.273s, 후속 3.138s,
  device sampled peak 7.459 GiB. 기본 262144 pixels/2048 input/512 output, batch 1.
- 실제 의도적 CUDA OOM 2회 → 새 8B worker의 축소 budget 추론 2/2 복구.
- 서로 다른 synthetic PDF 두 페이지 순차 추론 2/2 통과, p2에서 재로딩 없음.
- 최종 GPU 10 MiB/Xorg만 남음. worker/GPU lease 종료 확인.
- 첫 실제 시도는 0–1000 bbox로 schema 실패. 범위를 JSON Schema에 표현하여 교정했고
  실패 artifact를 보존했다. 현재도 formula region 누락/graph 설명·영역 오차는 남는다.

**A/B 실행 기반 완료, C–F 미완료.** C의 element-ID 검증과 좌표 변환부터 시작해야 한다.
이 clone에는 실강의 원본이 없어 실제 강의 A/B/사람 verified 품질 비교는 수행하지 못했다.
현재 VLM 진단 결과를 production 필기 좌표로 직접 사용하지 않는다.

## Phase C — SSD 환경 인수 및 구현 계획 (2026-11-06)

- 시작 `b6903b4`, clean main. A/B는 이미 commit/push되어 있고 재구현하지 않는다.
- SSD cache `models/huggingface`, 기존 `data/vlm-runs/ssd-smoke/report.json` 확인:
  completed_requires_visual_review, 실제 2회/schema 2/2, OOM 0. 모델 재다운로드 없음.
- 기존 word/line/block/figure ID, `extract_pdf`, `add_geometry`, `render_marked_page`를 재사용.
  회전된 PDF의 기존 추출은 rotated page 크기와 unrotated text 좌표가 섞일 수 있으므로
  원본을 수정하지 않는 unrotated 작업 복사본에서 geometry를 얻고 명시적 회전/crop transform을 저장.
- 독립 element registry에 PDF/OCR provenance와 geometry hash를 보존. 수식은 기존 line ID에
  deterministic type hint를 붙이며 모델이 새 기호/좌표를 생성하지 않게 한다.
- 기존 단일 GPU worker에 `select_targets`를 추가하고 선택은 registry의 canonical ID만 허용.
  음성 segment 인용/시간, PDF text, mentions/block 관계, page/viewport/confidence/ambiguity를 검증.
- 실제 8B 원본 이미지 vs SoM 이미지 비교 CLI와 선택 결과 PNG/JSON을 생성한다.
  합성 fixture의 기대 ID와 자동 비교는 사람 verified 품질 평가와 구분한다.
- 잘못된 ID/bbox/marker, 회전·crop·축소, 수식 기호, 이미지형/혼합 언어, cache 무효화,
  기존 VLM/API/page audio/Rich Notes 회귀 검증. production planner 기본값은 유지한다.

Phase C 실행 결과는 이후 기록하며 `.env`/기존 data/모델/lockfile과 Git 변경을 보존한다.

### Phase C 결과

- `app/grounding/` registry/좌표/선택 schema·검증, `GPUWorkerProvider.select_targets`,
  `scripts/ground_page.py` 구현. SSD 기본 경로 사용, 추가 모델 다운로드 없음.
- 기존 PDF/word/line/figure/SoM 렌더 재사용, 수식 내부 minus 누락 수정,
  compound `∇L` 내부 `∇` native character ID 추가. OCR/native 데이터 분리.
- 실제 최종 14회(original/SoM 각 7) 모두 JSON schema 통과, OOM 0.
  단어·α·수식·∇·그림 각 5개 positive ID는 양쪽에서 일치.
  없는 β와 반복 θ는 accepted 좌표 없이 거부/미매칭/모호함으로 남김.
- 모델 raw 기대 일치는 original 5/7, SoM 6/7, 검증 후 양쪽 7/7.
  prompt 개발에 사용한 작은 synthetic fixture이므로 일반 품질 향상으로 주장하지 않는다.
- 원본·SoM·선택 결과 PNG/JSON 실제 확인. 기록: `data/grounding-runs/phase-c-01`–`phase-c-06`.
- backend 408 passed (39.27s), 관련 51 passed, frontend 25 passed/build 통과.
  기존 Windows FFMPEG_PATH는 .env 보존 후 테스트 process에서만 Ubuntu 경로 지정.

Phase C core/실제 ID 선택 및 검토 경로 완료. production API/planner 연결, 실제 강의 gold와
복잡한 문서/OCR 품질 검증은 남아 있다. 상세 설계·재실행·실패 기록은 `visual_grounding.md` 참고.

## Phase D — production 통합 계획 (구현 전)

시작 `9a4b00a` clean main, GitHub main도 동일 SHA 확인. SSD 여유 61GiB.
현재 clone의 jobs/samples에서 실강의 PDF/audio를 찾지 못했다. 합성 녹음/전사/의미 추출과
실제 8B worker를 기존 run_pipeline에 연결해 PDF까지 검증한다. 실강의 품질로 보고하지 않는다.

1. 기본 off `VISUAL_GROUNDING`과 별도 interpreter IPC를 추가. 고정 entrypoint/JSONL,
   stdout 격리, job 내부 이미지 경로 검증, timeout/cancel/process-group cleanup.
2. notes 단계의 Rich ContentItem → Phase C grounding 요청. 페이지별 bounded 요청과 분할,
   source/registry/model/prompt/validation fingerprints로 resume와 stale 재검증.
3. versioned evidence graph/action/provenance를 저장하고, 기존 consolidate/layout에 accepted bbox만 전달.
   비확정 target은 임의 확정하지 않는다. VLM 장애는 기존 notes/planner fallback으로 기록.
4. source→knowledge→decision→action→render/PDF 연결. density/style 변경은 grounding 재사용.
   PDF/alignment/transcript 변경을 API에서도 검사. 과거 job/default/live 경로 보존.
5. 기존 렌더 mark(underline/circle/bracket/none)와 connector만 연결. rotation/crop을 display
   좌표로 변환하고 export 실제 배치를 검증. Phase E layout 재설계는 하지 않는다.
6. mock/IPC/실제 GPU 통합·PDF/PNG 검토·backend/frontend 회귀, 품질/실패/재현 문서 작성.

e5는 현재 코드에서 명시적으로 CPU. GPU STT는 VLM과 같은 lease 및 단계 종료 시 해제를 적용한다.
모델/환경/.env/기존 사용자 데이터는 수정·삭제하지 않는다. commit/push는 이번 요청 범위에 없다.

### Phase D 구현 및 검증 결과

- 기존 오프라인 `run_pipeline`에 기본 off flag와 isolated JSONL VLM worker를 연결했다.
  Rich ContentItem/Phase C adapter·registry·validator·GPU lifecycle을 재사용했다.
- versioned graph/action/provenance가 발화·지식·정렬·검증 target·render·PDF hash를 연결한다.
  accepted만 canonical bbox를 사용한다. ambiguity/rejection은 unanchored note,
  VLM 장애는 명시적 fallback이며 모든 상세 item을 보존한다.
- density/font/resume는 VLM 재추론 없이 처리한다. source 변경 시 stale mapping 금지,
  validation-only 변경 시 raw 응답 재검증, 부분 checkpoint 재사용도 테스트했다.
- 기존 layout/export mark를 연결하고 crop/rotation 원본 복사 및 실제 ink 좌표를 검증했다.
  v2 mentions의 registry stamp 및 display geometry도 지원한다.
- 실제 GPU production 통합 `gpu-e2e-03`: 7 추론, accepted5/rejected1/ambiguous1,
  actions7/render7, peak device7.23GiB, OOM0. PDF/PNG/JSON 직접 검토했다.
  실제 timeout/cancel 정리 및 OOM1회 후 축소 재시도도 별도 `gpu-recovery-01`에서 통과했다.
- backend432, frontend25, build 통과. 상세: `evidence_graph.md`, `annotation_quality_report.md`.

Phase D의 선택적 production 연결·합성 end-to-end/GPU 검증은 완료했다. 실강의 데이터가 없어
실강의 STT/추출/grounding 정확도와 인간 품질 평가까지 완료했다고 주장하지 않는다.
Phase E: connector 교차·figure circle/caption 간섭, free-space/overflow를 우선 개선.
Phase F: 동의된 실제 강의 입력·사람 gold 확보 후 동일 입력 A/B 및 coverage/정확도 측정.


## v3 integration checkpoint — 2026-11-06

Optional production modes legacy/ai_planner/visual_agent, capability tool API, native math parts,
page-scoped VLM plan/review and multi-action evidence/export are implemented. Whole-lecture study
synthesis replaces diagnostic Overview text. Defaults remain legacy until actual quality warrants
promotion. See `annotation_agent_v3.md` and `lecture_overview_v3.md`.

Actual same-input real-lecture A/B/C PDFs and source mapping tests are in
`data/annotation-v3-runs/comparison-02/`. Final backend509/frontend31/build passed, Chromium6
scenarios passed. Real8B reference19 attempts/16 schema-valid, OOM0; final replay itself made no
new model calls. Actual reviewer correction effectiveness remains unverified (zero edits).

**Full quality DoD not complete:** C distinct rendered knowledge8/11 versus baseline11/11,
ambiguous component relations still rejected, suppressed marks and redundant diagram connector,
incomplete field-generalization coverage, unchanged uncertain alignment. Detailed Notes retain all11.
Human gold and true semantic coverage are not available. Keep these gaps visible; do not replace
validation with guessed coordinates or count more actions as quality improvement.
