# Qwen3-VL 8B GPU benchmark — 2026-11-06

최신 환경: SSD cache `/root/classNote/models/huggingface`, 약 17GB, SSD 여유 약 57GB.
기존 `data/vlm-runs/ssd-smoke/report.json`은 completed_requires_visual_review, schema 2/2,
OOM 0, 첫/후속 wall 20.657/2.997s, device peak 8,009,023,488 bytes를 기록한다.
Phase C는 이 SSD cache를 재사용한다. 아래 tmpfs 성능은 초기 실험이며 SSD cold-load 값과 구분한다.

## 확인된 환경

| 항목 | 실제 값 |
|---|---|
| OS/kernel | Ubuntu 24.04.4 LTS / 6.14.0-37-generic |
| CPU/RAM | Intel Core i9-12900, 24 logical CPU / 62 GiB |
| GPU | NVIDIA GeForce RTX 3080, 10240 MiB, compute capability 8.6 |
| NVIDIA driver | 550.163.01 |
| driver CUDA / torch CUDA | 12.4 / 12.4 |
| Python | 3.12.3 |
| VLM torch / torchvision | 2.6.0+cu124 / 0.21.0+cu124 |
| Transformers | 4.57.6 |
| bitsandbytes / accelerate | 0.49.0 / 1.12.0 |
| VLM PyMuPDF / psutil | 1.26.7 / 7.2.2 |
| checkpoint | Qwen/Qwen3-VL-8B-Instruct |
| revision | 0c351dd01ed87e9c1b53cbc748cba10e6187ff3b |
| quantization / attention | NF4 double quant, FP16 compute / SDPA (실행 설정) |

샌드박스 안의 `nvidia-smi`는 exit=9, torch CUDA unavailable/device_count=0이었다.
승인된 **호스트 실행**에서는 CUDA available, GPU capability 8.6, free/total
10248585216/10488315904 bytes 확인. 물리 GPU나 드라이버가 고장난 것으로 판단하지 않는다.
호스트 preflight: `data/vlm-runs/host-preflight/report.json`.

backend는 기존 lock 그대로 설치했고 torch 2.15.0/CUDA 13 의존성이므로 이 GPU 측정에 쓰지 않는다.
시스템 torch/드라이버를 업그레이드하지 않았다.

## 다운로드

공식 Hugging Face manifest를 조회했다. 원본 weight 4 shards 합계 17,534,339,512 bytes,
processor/tokenizer/config 포함 약 17.55GB. 다운로드와 NF4 GPU resident 크기는 다르다.
시작 디스크 여유 약 18.18GB로 환경 설치까지 포함할 수 없어 명시적으로 승인된
`/dev/shm/classnote-hf` (32 GiB host tmpfs)에 다운로드한다. 재부팅하면 없어지는 RAM 캐시다.
Xet 전송이 정체돼 해당 다운로드 프로세스만 종료하고 HTTP로 재개했다.
기존 데이터·모델 캐시 삭제는 하지 않았다.

## 실제 추론 측정

실행 결과와 최종 게이트 상태는 아래 진행 기록에 추가한다. 아직 측정되지 않은
load/latency/VRAM/RAM/token 값은 미측정이며 preflight 성공을 inference 성공으로 보지 않는다.

smoke 절차: synthetic PDF 한 장을 960×540 PNG로 렌더 → 동일 이미지를 캐시 없이 두 번 요청.
제목·주요 텍스트·수식/그림 영역을 JSON으로 반환하고 Pydantic validation 수행.
production/실강의 품질과 구분하며 응답을 원본 PNG/PDF text와 비교한다.

측정 정의:

- `model_load_seconds`: processor/모델 load와 CUDA synchronize. 첫 요청에만 발생.
- `inference_seconds`: generate부터 CUDA synchronize까지. tokenization/load 제외.
- `wall_seconds`: 부모의 worker 시작/queue/통신/tokenization/load/generate를 포함.
- Torch allocated/reserved peak: 해당 요청 시작부터 load/generate를 포함한 allocator peak.
- device sampled peak/mean: `nvidia-smi` 0.2초 간격, 다른 프로세스/runtime 포함. 순간 peak 누락 가능.
- process peak RSS: worker lifetime ru_maxrss. tmpfs checkpoint의 RAM 사용량은 별도이며 RSS에 합산되지 않는다.
- worker close time: graceful 종료 최대 5초 후 필요 시 terminate/kill. unload API만의 latency는 아님.
- title literal match: PDF 텍스트에서 문자열 포함 여부. 수식/그림 정확성이나 semantic correctness는 사람 검토 필요.

## 재현

`docs/ubuntu_vlm.md` 참고. 모든 raw PDF/PNG/JSON은 ignored `data/vlm-runs/`에 보존한다.
모델/대형 artifact를 Git에 추가하지 않는다. 실제 OOM과 mock OOM을 구분한다.

## 실제 결과 — 정상 smoke

`data/vlm-runs/gpu-smoke-02/report.json`, 실제 요청 **2회**, cache hit **0**.
같은 synthetic page를 같은 worker로 처리했다. 모델은 실제 `is_loaded_in_4bit` 검사 통과.

| 지표 | 첫 요청 | 후속 요청 |
|---|---:|---:|
| model load | 9.562s | 0s (동일 worker 재사용) |
| generation latency | 3.864s | 3.125s |
| 전체 요청 wall | 15.273s | 3.138s |
| input / output tokens | 709 / 127 | 709 / 127 |
| output tokens/s | 32.864 | 40.643 |
| Torch peak allocated bytes | 7,240,175,616 | 6,674,891,264 |
| Torch peak reserved bytes | 7,482,638,336 | 7,482,638,336 |
| worker lifetime peak RSS bytes | 5,961,826,304 | 5,961,826,304 |
| schema valid | true | true |
| OOM | 0 | 0 |

- device sampled peak **8,009,023,488 bytes = 7.459 GiB** (7638 MiB).
  91 samples, sampled mean **4.762 GiB**, 두 요청과 close를 포함한 창이다.
  parent preflight CUDA context와 다른 GPU process도 포함한다.
- worker close **0.812s**. 정상 종료 후 메모리 반환은 최종 `nvidia-smi`로 별도 확인한다.
- system RAM sampled peak **22,507,597,824 bytes** (total−available, tmpfs cache/다른 process 포함).
  이 값과 worker RSS는 더해서 사용하지 않는다.
- 원본 render 960×540 → 명시적 입력 축소 682×384 → processor grid [1,24,42], patch=16,
  실제 vision 입력은 672×384. max pixels 262144, input cap 2048, output cap 512, batch=1.
- 이 짧은 입력의 warm throughput **0.319 page/s**. 일반 강의/긴 출력의 처리량으로 확대하지 않는다.
- load 시간은 RAM tmpfs에 이미 받은 checkpoint에서 측정했다. SSD cold-load 결과가 아니다.

## 실패를 포함한 검증 기록

1. sandbox preflight: GPU 접근 불가. 호스트 재실행으로 해결, driver 변경 없음.
2. `gpu-smoke-01`: 실제 8B 로딩 **15.433s**, generation **7.175s**, 605 input/119 output tokens,
   OOM 0. 그러나 bbox가 [588,200,945,880]처럼 0–1000 값이라 **schema 실패**.
   응답을 자동 보정해 통과시키지 않았다. 실패 JSON과 measurements를 보존했다.
3. 원인: Python model_validator의 범위 검사가 JSON Schema에는 드러나지 않았다.
   coordinate Field에 ge=0/le=1을 선언하고 prompt v2로 범위를 명시했다. 이 변경은 cache key도 바꾼다.
   `gpu-smoke-02`에서 2/2 schema 통과. 이는 syntax/범위 개선이며 의미적 품질 개선의 증거는 아니다.
4. `gpu-oom-recovery`: **의도적으로 실제 CUDA OOM 2회** 유도. 8B가 로드된 worker에서
   전체 VRAM 크기의 추가 할당을 요청하므로 CUDA가 거절한다. 다른 process에는 신호를 보내지 않는다.
   매번 child를 종료하고 새 8B child에서 pixels 131072 / input cap 1024 / output cap 256으로 재시도.
   **2/2 복구 및 JSON schema 통과**, 각 577 input/123 output tokens.
   재시도 model load 9.533/9.654s, generation 3.384/3.301s, output 36.349/37.266 tokens/s.
   모델 교체/CPU offload 없이 실행했다. 정상 smoke의 자연 발생 OOM과 이 의도적 probe를 구분한다.
5. timeout/hard crash/연속 두 OOM 실패/cache-only/restart는 별도로 CPU mock 테스트 13개에서 검증.

## 이미지와 PDF 정보의 비교 및 한계

코드가 만든 synthetic slide를 assistant가 PNG로 확인했다(사람의 verified 평가 아님).
정상 두 응답의 제목 `Gradient Descent`와 texts 3개는 원본 PDF text와 일치한다.
수식 `theta = theta - alpha * gradient`를 text로 읽었으나 **formula region은 생성하지 않았다**.
그림 bbox [0.62,0.32,0.98,0.88]는 graph를 대략 가리키지만 축/여백을 정확히 포함하지 않는다.
`Loss curve`를 x-axis label로 단정하는 설명도 남았다. 이 응답을 사실/좌표 gold로 쓰면 안 된다.

**Phase B의 단일 페이지 8B NF4 실행·측정 게이트는 통과했다.**
정확한 symbol/figure target, 한국어·이미지형·복잡 수식, 긴 강의 안정성, 실제 강의 A/B는 미측정.
Phase C의 ID grounding/좌표 검증 없이 production annotation으로 연결하지 않았다.

## 서로 다른 페이지 순차 실행과 종료 검증

`data/vlm-runs/gpu-multipage/report.json`: 두 장으로 만든 synthetic PDF를 같은 worker에서
batch 1로 p1→p2 처리. 제목 `Gradient Descent`/`Learning Rate`가 각각 원본 text와 일치,
schema **2/2**, OOM **0**. p1 load 9.668s, 전체 wall 14.876s; p2 load 0s,
전체 wall 3.436s (generation 3.423s, 709 input/139 output tokens).
worker close 0.577s, device sampled peak 7.459 GiB. 실제 긴 강의 스트레스 테스트는 아니다.

OOM probe까지 포함한 최대 device 표본 peak는 **8,013,217,792 bytes = 7642 MiB ≈7.463 GiB**.
정상 요청의 자연 발생 OOM은 없고, probe의 OOM은 의도적 2회다.
최종 호스트 `nvidia-smi` (12:49:11 KST)는 **10 MiB/10240 MiB**, Xorg만 남았고
VLM parent/worker가 사라졌음을 확인했다. 모델을 GPU에 상주시키지 않았다.

다운로드 최종 manifest **17,545,907,231 bytes**. 마지막 HTTP 병렬 재개만 **821.515s**이며
이전 Xet/HTTP 시도 시간을 포함한 전체 다운로드 시간으로 보고하지 않는다.

## Phase C — SSD 8B element selection

재다운로드 없이 `models/huggingface`를 사용. `data/grounding-runs/phase-c-06/report.json`:
actual GPU requests 14, cache hit 0, schema valid 14, OOM 0.
이미지 1개/batch 1, pixels cap 524288, input cap 4096, output cap 256.
입력 render 1440×810 → bound 965×543 → processor grid [1,34,60], 실제 vision 960×544.
실제 input 1177–1405 tokens, output 52–63 tokens. 전체 강의 context는 넣지 않았다.

- device sampled peak: **7,764,705,280 bytes (7405 MiB, 7.231 GiB)**.
- device sampled mean: 6,270,403,374.67 bytes (요청 사이 검증/렌더와 종료 포함).
- system RAM total−available sampled peak: 22,875,013,120 bytes, 다른 process/page cache 포함.
- 첫 모델 load: 9.667s, 이미 읽힌 SSD cache에서 측정한 값. cold-load가 아님.
- generation mean original 1.793s / SoM 1.772s. original이 첫 model load 비용을 부담하므로
  두 모드의 wall mean을 직접 비교해 속도 개선을 주장하지 않는다.

schema/ID/음성 검증 실패와 전체 이미지 결과는 `visual_grounding.md` 참고.
정확도는 GPU latency/VRAM 수치로 대신 평가하지 않는다.

Phase C 종료 확인: host `nvidia-smi` 14:07:00 KST, 10 MiB/10240 MiB,
Xorg만 남고 VLM worker 없음. SSD 가중치는 유지하고 GPU 메모리를 반환했다.
