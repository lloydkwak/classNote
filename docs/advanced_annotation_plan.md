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
