# Advanced annotation — Phase A/B, 2026-11-06

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
