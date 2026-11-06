# Ubuntu에서 서비스와 Qwen3-VL 재실행

현재 환경 업데이트 (2026-11-06): 모델은 SSD `/root/classNote/models/huggingface`에 있으며
`data/vlm-runs/ssd-smoke/report.json`으로 실제 추론 성공 확인. 아래 RAM tmpfs 설명은
초기 A/B 실험 기록이다. 현재 실행은 SSD 경로가 기본이며 모델을 다시 다운로드할 필요가 없다.

## Phase D: production opt-in 실행

모델 다운로드/환경 재설치는 필요 없다. 기존 `.env`와 provider 설정을 유지하고 Linux ffmpeg만
process에서 지정한다. 기본 STT CPU 경로를 권장하며 VLM은 기존 SSD `.venv-vlm`만 사용한다.

```bash
cd /root/classNote
# API 새 오프라인 job 기본값 활성화 (기본은 VISUAL_GROUNDING=0)
FFMPEG_PATH=/usr/bin/ffmpeg VISUAL_GROUNDING=1 \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

# 또는 개별 job에서만 활성화: 기존 API의 optional multipart field
curl -F 'pdf=@/path/to/slides.pdf' -F 'audio=@/path/to/lecture.wav' \
  -F 'visual_grounding=true' http://127.0.0.1:8000/api/jobs

# 기존 CLI: 현재 설정의 STT/LLM을 사용한다 (실제 서비스 호출/시간이 발생할 수 있음)
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/run_pipeline.py \
  --pdf /path/to/slides.pdf --audio /path/to/lecture.wav --visual-grounding

# 기존 job을 opt-in으로 바꿀 때는 display geometry를 다시 만들도록 extract부터
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/run_pipeline.py \
  --job-id JOB_ID --from-step extract --visual-grounding
# 이미 opt-in인 job의 prompt/validation/실패 재처리: STT/정렬을 다시 하지 않음
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/run_pipeline.py \
  --job-id JOB_ID --from-step notes
```

`VLM_PYTHON` 기본 `/root/classNote/.venv-vlm/bin/python`, `VLM_TIMEOUT_SECONDS` 기본180이다.
모델은 pinned 8B/NF4/SDPA, local_files_only이며 `models/huggingface`를 재사용한다.
출처/상태: 기존 `/annotations?density=balanced`, `/detailed-notes`, `/annotated.pdf?density=balanced`.
UI 업로드 toggle은 추가하지 않았으며 env 또는 API/CLI로 선택한다. Live mode에는 VLM을
추가하지 않는다. 상세 필드/실패/무효화 정책은 [Evidence Graph](evidence_graph.md) 참고.

### 합성 production 검증 재현

항상 **새 output 디렉터리**를 사용한다. 기존 사용자 job과 데이터를 덮어쓰지 않는다.
무음 WAV·STT·alignment·semantic fixture를 쓰고, 기본 실행의 grounding만 실제 8B다.

```bash
cd /root/classNote
# CPU mock end-to-end (일반 테스트와 동일하게 모델 다운로드/추론 없음)
backend/.venv/bin/python backend/scripts/verify_phase_d.py --mock-vlm \
  --output data/phase-d-runs/YOUR_MOCK_RUN
# 실제 backend → isolated VLM → notes/action → PDF export + PNG + density/resume 검증
backend/.venv/bin/python backend/scripts/verify_phase_d.py \
  --output data/phase-d-runs/YOUR_GPU_RUN
# 명시적 실제 warm timeout/cancel 및 CUDA allocation OOM/재시작 검증 (운영 job에 실행하지 않음)
backend/.venv/bin/python backend/scripts/verify_vlm_recovery.py \
  --run data/phase-d-runs/YOUR_GPU_RUN --output data/phase-d-runs/YOUR_RECOVERY_RUN

cd backend
FFMPEG_PATH=/usr/bin/ffmpeg .venv/bin/python -m pytest -q
cd ../frontend
PATH=/root/classNote/.cache/node/node_modules/node/bin:$PATH npm test
PATH=/root/classNote/.cache/node/node_modules/node/bin:$PATH npm run build
```

실측 산출물은 `gpu-e2e-03`과 `gpu-recovery-01`에 있다. 일반 pytest는 실제 GPU 결과와 별개다.
실행 후 annotated.pdf/preview.png 및 raw selection/validation JSON을 함께 확인한다.
자동 runner의 `completed_requires_visual_review`는 인간 gold 검토 완료를 뜻하지 않는다.

## 기존 서비스

Python 3.11+, uv, ffmpeg, Node >=22.13 필요. 기존 `.env`를 덮어쓰지 않는다.
처음 설정할 때만 `.env.example`을 복사하고 로컬에서 키를 입력한다.

```bash
cd backend
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# 별도 terminal
cd frontend
npm ci
npm run dev
```

현재 backend lock의 torch 2.15/CUDA 13 환경은 드라이버 550의 VLM 실행에 사용하지 않는다.
기존 mock backend 회귀는 이 lock 환경에서 검증했다. STT 기본값 CPU를 유지한다.

이 workspace에 설치한 도구로 재실행할 경우:

```bash
# repo root
.venv-tools/bin/uv sync --frozen --project backend --python /usr/bin/python3 --cache-dir .cache/uv
cd backend
.venv/bin/python -m pytest
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 별도 terminal, repo root
export PATH="$PWD/.cache/node/node_modules/node/bin:$PATH"
cd frontend
npm test
npm run build
```

## 독립 VLM 환경

서비스 API에서 직접 호출하지 않는 Phase B 실험이다. NVIDIA driver와 `nvidia-smi`가 정상이고
다른 inference worker가 GPU를 점유하지 않는 상태에서 실행한다. Codex sandbox에서는 장치가
숨겨져 있어 **호스트 실행 승인**이 필요하다. 드라이버를 재설치할 문제로 오해하지 않는다.

새 머신에서는 아래처럼 독립 환경을 만든다. 이 세션에서는 시스템에 이미 있던 동일한
torch/torchvision을 `venv --system-site-packages`로 재사용했으며 시스템 패키지를 바꾸지 않았다.

```bash
python3 -m venv .venv-vlm
.venv-vlm/bin/python -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
.venv-vlm/bin/python -m pip install -r backend/requirements-vlm.txt
```

공식 [PyTorch cu124 wheel](https://pytorch.org/get-started/previous-versions/)과 설치된 드라이버의
호환성을 확인한다. 시스템 CUDA toolkit만 보고 wheel 호환성을 판단하지 않는다.
위 환경은 이 세션의 검증 버전이다. 새 버전으로 자동 업그레이드하지 않는다.

## 진단 → 다운로드 → 실제 추론

아래는 repo root에서 실행한다. `--model-cache`는 용량이 충분한 디스크 경로로 바꿀 수 있다.
17.55GB 원본 가중치를 NF4로 로딩할 때 양자화하므로 다운로드 용량이 4-bit 크기가 되지 않는다.
최소 잔여 2GiB를 포함하는 사전 용량 검사가 있다. 실제 운영은 여유 30GB 이상을 권장한다.

```bash
.venv-vlm/bin/python backend/scripts/benchmark_vlm.py preflight --model-cache models/huggingface
HF_HUB_DISABLE_XET=1 .venv-vlm/bin/python backend/scripts/benchmark_vlm.py download --model-cache models/huggingface
.venv-vlm/bin/python backend/scripts/benchmark_vlm.py smoke --model-cache models/huggingface --output data/vlm-runs/synthetic-smoke
.venv-vlm/bin/python backend/scripts/benchmark_vlm.py smoke --model-cache models/huggingface --pdf /absolute/path/slides.pdf --page 1 --output data/vlm-runs/lecture-page-1
# 두 페이지를 같은 worker에서 순차 실행 (batch는 계속 1)
.venv-vlm/bin/python backend/scripts/benchmark_vlm.py smoke --model-cache models/huggingface --pdf /absolute/path/slides.pdf --page 1 --next-page 2 --output data/vlm-runs/two-pages
# 명시적 실제 OOM 복구 실험: 다른 GPU 작업이 없는 상태에서만 실행
.venv-vlm/bin/python backend/scripts/benchmark_vlm.py smoke --model-cache models/huggingface --exercise-oom --output data/vlm-runs/oom-probe
```

이 세션의 임시 RAM 캐시는 `--model-cache /dev/shm/classnote-hf`를 사용한다. 32GiB tmpfs가 있어도
실제 RAM 여유가 충분한지 먼저 `free -h`로 확인한다. tmpfs 공간은 프로세스 RSS에 포함되지 않는
별도 RAM 사용량이며 재부팅 시 사라진다. 이 환경에서 sandbox의 `/dev/shm`은 호스트와 다르다.

Xet 전송 정체로 HTTP 재개를 사용했다. HF cache는 재개할 수 있으며, 전송 프로세스를 동시에
여러 개 시작하지 않는다. `.incomplete` 파일은 삭제할 필요가 없다.

smoke는 PNG/PDF 및 `report.json`을 ignored `data/`에 저장한다. 같은 페이지를 실제 2회 호출하며
응답 캐시는 사용하지 않는다. 이미지 입력 1개, batch 1, NF4 double quant, FP16 compute, SDPA,
기본 max pixels 262144 / input tokens 2048 / output tokens 512 / timeout 300초.
필요하면 `--max-pixels 131072 --max-new-tokens 256`으로 낮춘다. 요청 context를 임의로 자르지 않는다.

## 실패·종료·캐시

- worker는 첫 요청에 모델을 로드하고 같은 provider의 순차 요청에서만 유지한다.
- `close()`/context manager가 프로세스·GPU lease를 해제한다. 요청 timeout은 child를 종료한다.
- CUDA OOM 시 해당 child를 종료하고 새 child에서 이미지/입출력 budget을 절반으로 낮춰 1회 재시도.
  실패하면 예외로 반환한다. 모델 변경·무제한 재시도·자동 CPU offload는 없다.
- 이 실험은 production 경로에 연결되지 않아 실패해도 기존 annotation/page audio를 바꾸지 않는다.
- VLM worker들은 같은 lease 경로를 공유해야 한다. 기존 STT/e5 worker의 lease 통합은 아직 없다.
  모든 GPU 작업의 중앙 스케줄링은 Phase C 통합 전에 완료해야 한다.
- 캐시는 image SHA/page/model revision/prompt/schema/budget/provider에 따라 구분된다.
  `DEMO_MODE=1`이면 cache miss에서 모델을 로드하지 않는다. cache hit의 과거 metrics는 신규 추론이 아니다.
- 본 smoke의 bbox는 검토용 image 좌표다. PDF annotation에 직접 사용하면 안 된다.
  `analyze_page`의 bbox는 계속 진단용이다. Phase C의 검증된 ID/좌표는 별도 `select_targets`로 제공한다.

GPU peak는 Torch allocated/reserved와 device 전체 `nvidia-smi` 표본 peak를 구별한다.
표본 주기 사이의 순간 peak는 포착하지 못할 수 있다. 첫 요청은 로딩 포함 wall time과 generation
time을 별도로 기록한다. RSS는 worker 프로세스의 lifetime peak다. 실제 결과는 `gpu_benchmark.md` 참고.

## Phase C 실행

SSD cache를 기본으로 사용하는 실제 ID 선택은 [visual_grounding.md](visual_grounding.md) 참고.
A/B를 다시 구현하거나 checkpoint를 다시 다운로드할 필요가 없다.

```bash
.venv-vlm/bin/python backend/scripts/ground_page.py --output data/grounding-runs/new-run
# 기존 .env에 Windows ffmpeg 경로가 있더라도 파일을 바꾸지 않고 회귀 검증 가능
cd backend
FFMPEG_PATH=/usr/bin/ffmpeg .venv/bin/python -m pytest -q
```

원본/SoM PNG, registry, raw response/검증 JSON, ID 선택 결과 PNG/contact sheet를 저장한다.
실강의는 `--pdf ... --page ... --evidence ...`를 사용한다. 기본 실행은 synthetic fixture이고
사람 verified 품질 평가가 아니다. 현재 production viewer/planner에는 자동 적용하지 않는다.
