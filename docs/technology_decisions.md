# Technology decisions — 2026-11-06

## Dependencies

새 라이브러리를 추가하지 않았다. 기존 e5/NumPy Viterbi, Pydantic, provider/cache,
PyMuPDF, deterministic layout, React 플레이어를 재사용한다. WhisperX, Docling,
PaddleOCR, BGE-M3, wavesurfer, KaTeX는 정확도·필요성 검증 전이므로 도입하지 않았다.
설치 크기·라이선스·GPU·추가 운영비 변화 없음. Rich extraction 호출은 기존 notes
호출을 대체하며, 120초 span당 요청하므로 이전 배치 방식보다 호출 수가 늘 수 있다.
실제 비용과 latency는 아직 측정하지 않았다.

## Alignment

후진 비용을 `base + (distance - 1) * per_page`로 확장했다. 기본 per_page=0.2.
이전 상수 비용은 멀리 뒤로 가는 것이 앞으로 가는 것보다 지나치게 저렴했다.
강의 시작 페이지는 고정하지 않고 재방문을 허용한다. e5, page-bias correction,
off-topic 판정, frozen live windows를 유지한다. `ALIGN_BACK_PENALTY_PER_PAGE=0`
설정으로 기존 비용을 재현할 수 있다. 단일 잠정 라벨에서 관측한 개선이며 일반화
증거가 없고, 첫 페이지 정확도도 해결되지 않았다. hybrid/glossary/boundary refinement는 후속 작업.

## Content and rendering

`rich_notes.py`는 의미 단위를 먼저 추출한다. `detailed_notes.json`이 상세 노트의
원본이며 PDF용 `short_note`와 같은 content ID를 가진다. 추가 요약 LLM 없이 추출된
content를 그대로 보여주어 두 번째 생성에서의 손실을 피한다. 섹션은 type으로
표현되며 UI는 페이지별 항목으로 표시한다. 수식 전용 렌더러는 아직 없다.

인용/숫자/블록 ID/시간 검사를 유지한다. 이 검사는 의미적 entailment 검증이 아니다.
`confidence`는 정렬 confidence이며 사실 정확도의 확률이 아니다. 검증 실패는
item ID/이유로 기록하고, provider 오류도 unavailable로 기록한다. 자동 근거 복구는
아직 없고 recovery_attempted는 provider/schema 재시도에만 해당한다.

Density는 importance와 페이지 높이 기반 공간 예산으로 선택한다. 고정 개수 상한은
rich 경로에서 제거한다. 기존 layout이 글을 줄일 수 있으나 상세 원문은 유지된다.
웹과 export는 동일한 consolidate/layout을 호출한다. 현재 mark는 기존 underline,
circle, bracket, arrow와 margin note만 사용한다. highlight/box/new action schema는 미구현.

## Cache and compatibility

- 추출 캐시: provider/model/전체 versioned prompt(발화·블록 포함). density와 독립.
- 상세 결과에 alignment/extraction hash를 기록한다. 변경되면 API는 409를 반환한다.
- PDF variant 키: content/extraction/PDF hash + density + font + planner version.
- page-audio API는 현재 alignment에서 파생하므로 오래된 page_audio.json을 제공하지 않는다.
- 예전 job은 기존 annotations/export를 제공한다. 상세 노트를 원하면 notes 단계부터 재실행.
- live는 기존 generate_span_notes 경로를 그대로 사용한다. rich notes는 오프라인 경로만 지원한다.
- v2 evidence는 유지하지만 새 action planner로 완성된 것은 아니다.

기존 데이터·키·캐시는 변경하지 않았다. 실제 강의 벤치마크는 저장 similarity만 읽는다.

## Evaluation sprint

평가 schema는 기존 Pydantic으로 정의하고 JSON Schema도 버전 관리한다. 별도 DB·UI
framework·embedding 모델을 추가하지 않았다. 개발 API는 명시적 환경 변수로 켜며
로컬 단일 프로세스에서 JSON revision을 저장한다. 사람이 확인하지 않은 legacy label은
provisional로만 가져온다. CLI 지표는 라벨/예측 구간 교집합을 사용한다.

lexical hybrid와 segment-boundary snapping은 평가 전용 옵션이다. 실강의 잠정 라벨에서
current 대비 개선이 없어 production 정렬에 적용하지 않았다. provider SDK 응답의 사용량을
기록하지만 cache hit에 과거 토큰을 신규 사용량으로 더하지 않고, 모르는 비용은 null로 둔다.

## Ubuntu RTX 3080 — Phase A/B

### 실제 적용

- Qwen **8B Instruct**를 고정하고 공식 revision
  `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`를 사용한다.
  [공식 model card](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)의
  `Qwen3VLForConditionalGeneration` / `AutoProcessor.apply_chat_template` API를 따른다.
- Transformers 4.57.6 / accelerate 1.12.0 / bitsandbytes 0.49.0 실제 설치와 API import 확인.
  [NF4/double quant 공식 API](https://huggingface.co/docs/transformers/quantization/bitsandbytes),
  FP16 compute, SDPA, batch=1. 외부 GGUF/projector나 비공식 양자화 checkpoint는 쓰지 않는다.
- 기존 torch 2.6.0+cu124 / torchvision 0.21.0+cu124를 별도 VLM venv에서 재사용.
  [공식 cu124 빌드](https://pytorch.org/get-started/previous-versions/)와 드라이버 550.163.01,
  compute capability 8.6에서 CUDA 접근 확인. backend lock은 변경하지 않았다.
- PyMuPDF는 기존 서비스 의존성을 유지하며 smoke PDF/PNG 렌더에만 사용한다.
  모델 결과 bbox는 diagnostic image 영역이다. 원본 PDF geometry를 덮어쓰지 않는다.
- provider는 lazy subprocess + thread lock + POSIX GPU lease를 사용한다. timeout/close 시
  종료하고 OOM 시 child를 새로 만들며 입력 축소 1회 재시도. 성공 schema만 캐시한다.
  API 통합 전 실험으로 opt-in CLI에서만 사용한다. 중앙 scheduler의 기존 STT/e5 통합은 미완료.
- 데이터/모델은 Git ignore. 다운로드는 FP16 원본 약 17.55GB이며 NF4는 로딩 시 적용한다.
  디스크 부족으로 이번에는 명시적으로 승인된 RAM tmpfs 캐시를 사용한다.

### 문서 도구 후보 — 문헌 검토, 설치/품질 비교 미실행

아래 선택은 classNote의 기존 PDF 좌표·OCR·free-space 구현과 단일 10GB GPU 제약에 따른
**후속 실험 우선순위**다. 어느 도구도 한국어/수식 정확도가 검증됐다고 주장하지 않는다.

| 후보 | classNote에서 검토할 역할 | 이번 결정 및 이유 |
|---|---|---|
| [Docling](https://github.com/docling-project/docling) | layout/reading order/table/formula, JSON provenance | Phase C 첫 실험 후보. 코드 MIT, 개별 모델 license 별도. 기존 geometry를 보완할 선택적 parser로 평가 |
| [MinerU](https://github.com/opendatalab/MinerU) | 복잡한 수식·표/이미지형 PDF | 미설치. 모델/runtime 및 추가 license 조건을 확인하고 별도 worker 비교 필요 |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 혼합 언어 OCR/문서 구조 | 미설치. 기존 RapidOCR/PP-OCR 한국어 경로와 중복. 원본 PDF text와 OCR 결과는 분리 유지 |
| [Surya](https://github.com/datalab-to/surya) | 다국어 OCR/layout/reading order | 보류. code와 model license가 다르고 새 GPU resident model을 추가하기 전 필요성 검증 필요 |
| [Marker](https://github.com/datalab-to/marker) | PDF→JSON/Markdown, 수식·표 추출 | 보류. 전체 변환기 교체보다 기존 좌표 시스템 보완이 우선. model license는 code와 별도 확인 필요 |
| [UniMERNet](https://github.com/opendatalab/UniMERNet) | 수식 crop→LaTeX | 수식 심볼 문제를 실제 재현한 뒤 선택적 worker로 검토. 식 전체 인식이 symbol 좌표 정답은 아님 |

[Microsoft SoM](https://github.com/microsoft/SoM)은 ID 마킹 아이디어만 참고하며 기존
`pdf_extract.py`의 marked image를 재사용할 계획이다. SoM 코드를 복사하지 않았다.
[SlideNote](https://github.com/Cat-blizzard/SlideNote)는 AGPL-3.0로 표시돼 코드 복사 없이
coverage 개념만 참고한다. [AfterLecture](https://github.com/wcboy/AfterLecture)는 MIT로
표시되지만 확장 PDF 구현은 기존 export부터 검토한다. 이들 참고 프로젝트의 소스 복사/설치는 하지 않았다.
OR-Tools도 layout의 실제 제약 문제를 확인한 뒤 필요할 때만 도입한다.

실측과 미완료 게이트는 `gpu_benchmark.md`, 사용법은 `ubuntu_vlm.md`를 따른다.
