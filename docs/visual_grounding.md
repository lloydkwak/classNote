# Phase C — Set-of-Mark visual grounding

> 2026-11-06 Phase D 업데이트: 현재 opt-in production notes/API/layout/export까지 연결했다.
> 아래 CLI/API 수준 설명은 Phase C 당시 범위다. 실제 통합 구조·필드·무효화·fallback은
> [Evidence Graph](evidence_graph.md), 실측/시각적 한계는
> [품질 보고서](annotation_quality_report.md)를 우선한다. 기본 경로는 여전히 기존 annotation이다.

## 구현 범위

`GPUWorkerProvider.select_targets(TargetRequest)`가 실제 Qwen3-VL-8B-Instruct NF4 worker를
재사용한다. 모델은 canonical element ID를 고르고, 좌표는 검증된 registry에서만 가져온다.
기존 page audio, Rich Notes/density, API, layout/export 기본 경로는 변경하지 않았다.
현재 연결 지점은 **VLM 환경의 Python API와 CLI**다. production notes/API/렌더러 연결은 Phase D다.

```text
원본 PDF (읽기 전용)
  → unrotated 임시 작업 복사본
  → 기존 extract_pdf / word·line·figure / 선택적 OCR
  → element registry + native character bbox + PDF/geometry hash
  → 원본 PNG / SoM PNG + crop·rotation·scale 변환
  → 기존 rich content adapter + speech segments + versioned mentions
  → SSD 8B worker.select_targets (한 번에 이미지 1개, batch 1)
  → JSON Schema → ID/page/type/text/evidence/confidence/ambiguity 검증
  → grounding JSON + 검토 PNG (production annotation 아님)
```

## 요소와 좌표

- 기존 `p1_b2`, `p1_l2`, `p1_w13` ID를 재사용한다. line text의 수식 분류는
  `formula_text_heuristic`으로 명시하며 수학적 구조 인식의 정답으로 취급하지 않는다.
- `∇L`처럼 붙어 있는 토큰의 `∇`는 원본 `get_text("rawdict")` 문자 bbox에서
  `p1_w14_c0` 같은 ID를 추가한다. OCR 단어를 비례 분할해 정확한 기호 좌표라고 주장하지 않는다.
- source=pdf/ocr를 보존한다. OCR 추정 symbol bbox는 거부한다. OCR 미설치 시 native
  PDF 및 실제 embedded image 영역만 쓰며, 이미지형 PDF에 가상의 text/formula ID를 만들지 않는다.
- numeric SoM 마커는 기존 `render_marked_page`의 선택적 인자로 생성한다. 주로 line/formula/figure에
  붙이고 word/character ID는 표와 parent 관계로 전달한다. 원본 PDF 파일은 저장하지 않는다.
- 모델 응답의 bbox 필드는 허용하지 않는다. 알 수 없는 ID나 숫자 마커 ID를 근처 대상으로 보정하지 않는다.

registry bbox는 **unrotated crop-local PyMuPDF 좌표를 페이지 크기로 나눈 0–1 값**이다.
raw PDF의 bottom-left 좌표와 같다고 가정하면 안 된다. 원본 `cropbox`, `rotation_matrix`도 저장한다.
W/H는 회전을 제거한 crop의 크기이며, 원본을 수정하지 않는 임시 복사본에서 기존 추출기를 재사용한다.

```text
normalized PDF point (u,v)
  → (u·W,v·H)
  → original page.rotation_matrix
  → render zoom
  → minus actual pixmap (x,y) origin
  = image/crop pixel point
```

`Viewport.pdf_normalized_to_pixels`에 이 affine transform을 저장한다. 역변환은
`pixels_to_pdf_box`가 담당한다. 잘못된/비어 있는 bbox, 이미지 밖 좌표, singular transform,
crop 밖의 visible ID를 거부한다. 0/90/180/270° 회전, 비영점 PDF cropbox, 부분 crop,
비정수 zoom을 실제 PyMuPDF render/search_for와 대조하는 테스트가 있다.

기존 geometry에서 수식 내부 `-`가 bullet로 삭제되는 오류를 고쳤다. 이에 따라 이후 word ID가
달라질 수 있으므로 **옛 mentions의 word ID를 새 registry와 섞지 않는다**.
mentions를 신뢰하려면 `mentions_registry_hash == registry_fingerprint(registry)`가 필요하다.
unversioned/stale mentions는 무시하고 JSON에 `mentions_ignored`로 표시한다.

## 검증과 fallback

- `GroundingItem.from_content_item`은 기존 rich `ContentItem`의 ID, quote, segment IDs,
  page/block IDs, time을 재사용한다. 원본 rich schema를 수정하지 않는다.
- quote는 실제 연속 segment에서 찾고, word timestamp가 있으면 해당 시간 안에서 다시 검증한다.
  없으면 segment 전체 시간을 포함해야 한다. 식의 +/- 변경을 기존 punctuation 정규화로 허용하지 않는다.
- target ID/page/visible viewport/kind, literal PDF text, 기존 block 관계, 높은 점수의 versioned
  timed mention을 교차검증한다. Greek symbol 대소문자는 임의로 바꾸지 않는다.
- 동일 기호가 반복되면 단일 위치를 식별하는 mention이 없는 한 ambiguous로 남기고 후보 ID를 보존한다.
- 낮은 confidence는 ambiguous, unsupported/unknown ID는 rejected, 대상 없음은 unmatched.
  이 경우 최종 target ID와 bbox는 null이다. 기존 서비스의 상세 노트를 유지할 수 있는 출력이다.
- 그림은 빈 native text 때문에 기존 rich-note figure block 연관을 요구한다. 이는 출처 연결이며
  그림 설명의 semantic entailment 검증이 아니다. confidence=0.8 같은 모델 값도 보정된 정확도 확률이 아니다.
- cache는 모델/revision/budget/prompt, 이미지 hash, registry, source segments/items/mentions,
  alignment hash, viewport, image mode에 따라 구별된다. 캐시 raw JSON도 현재 검증기로 다시 검사한다.
- schema 실패는 raw output과 metrics를 보존하고 accepted로 보정하지 않는다. 정상 CUDA context는
  다음 요청에 재사용한다. OOM/timeout/crash의 기존 child 종료·재시작 정책은 유지한다.

## 실행

모델은 **SSD `/root/classNote/models/huggingface`가 기본**이다. 새 모델/라이브러리 다운로드는 필요 없다.
host에서 실행한다. Codex sandbox는 GPU 또는 host cache를 보지 못할 수 있다.
출력 폴더가 이미 채워져 있으면 덮어쓰지 않으므로 새 경로를 사용한다.

```bash
# repo root, 기본값: 합성 한·영/Greek 수식 slide, original/SoM 양쪽 실제 비교
.venv-vlm/bin/python backend/scripts/ground_page.py --output data/grounding-runs/new-run

# 원본 강의는 읽기 전용. --evidence는 items/segments/mentions JSON
.venv-vlm/bin/python backend/scripts/ground_page.py \
  --pdf /absolute/path/slides.pdf --page 8 \
  --evidence /absolute/path/evidence.json --mode som \
  --output data/grounding-runs/lecture-p8

# 필요한 영역만: unrotated crop-local normalized 좌표
.venv-vlm/bin/python backend/scripts/ground_page.py \
  --pdf /absolute/path/slides.pdf --page 8 --evidence /absolute/path/evidence.json \
  --crop 0.1 0.2 0.7 0.6 --mode som --output data/grounding-runs/lecture-p8-crop
```

기본 cap: pixels 524288, input 4096, output 256; 실제 fixture input은 1177–1405 tokens.
긴 페이지가 budget을 넘으면 context를 잘라 내용을 잃지 않고 실패한다. crop/입력 범위를 줄여 재실행한다.
`--cache`를 명시해야 inference 캐시를 재사용한다. 기본 비교는 실제 uncached 호출이다.

evidence JSON은 다음 형태다. `items`에 기존 rich `ContentItem`을 넣어도 adapter로 읽는다.
`target_kind`는 word/symbol/formula/figure/any이며, 기존 ContentItem의 기본값은 any다.

```json
{
  "alignment_hash": "current-alignment-fingerprint",
  "items": [{
    "id": "knowledge_032", "page": 8,
    "content": "alpha controls the update size",
    "source_quote": "alpha controls the update size",
    "start": 10, "end": 14, "segment_ids": [0],
    "block_ids": ["p8_b2"], "target_kind": "word"
  }],
  "segments": [{"start": 10, "end": 14, "text": "alpha controls the update size"}],
  "mentions": [], "mentions_registry_hash": ""
}
```

실제 ID는 해당 PDF에서 생성한 `registry.json`에서 확인한다. 위 예시 ID를 복사해 정답이라고 가정하지 않는다.
결과는 `registry.json`, `page/original.png`, `page/som.png`, case별 result PNG,
`contact-sheet.png`, `report.json`에 저장된다. 녹음/실강의 내용이 들어갈 수 있어 모두 ignored `data/`에 둔다.

## 실제 실행 결과와 발견한 오류

최종: `data/grounding-runs/phase-c-06/report.json`, **14회 실제 8B 호출 / cache hit 0 / OOM 0**.
한 장의 직접 만든 slide, 7 cases × original/SoM이다. 사람이 작성한 verified gold가 아니다.

| case | 기대 대상 | original | SoM |
|---|---|---|---|
| gradient 단어 | p1_w4 | accepted | accepted |
| 식 내부 α | p1_w13 | accepted | accepted |
| 수식 전체 | p1_l2 | accepted | accepted |
| ∇L 내부 ∇ 한 글자 | p1_w14_c0 | accepted | accepted |
| loss curve 그림 | p1_b5 | accepted | accepted |
| 없는 β | 확정 대상 없음 | 잘못 선택한 α를 unsupported_target으로 거부 | 모델이 unmatched 반환 |
| 반복 θ | 두 후보/확정 없음 | 첫 θ 선택을 ambiguous로 처리 | 첫 θ 선택을 ambiguous로 처리 |

- JSON schema: original 7/7, SoM 7/7. positive target 5/5씩 registry ID와 일치.
- raw 모델 ID/abstention 기대 일치: original **5/7**, SoM **6/7**.
- 검증 후 ID/abstention 기대 일치: 양쪽 **7/7**. 이를 "모델 정확도 100%"로 보고하면 안 된다.
- device sampled peak **7,764,705,280 bytes = 7405 MiB ≈7.231 GiB**.
  generation mean original **1.793s**, SoM **1.772s**. 첫 load **9.667s**는 이미 읽힌 SSD cache 조건이다.
  첫 로딩을 original만 부담하므로 단순 wall mean으로 두 모드 속도를 비교하면 안 된다.
- assistant가 original/SoM 및 ∇/수식/그림/반복 θ 결과 PNG를 실제로 확인했다.
  ∇ bbox는 L을 제외하고, 그림 bbox는 caption을 제외한 실제 graph 영역에 대응한다.
  이 검토는 사람의 verified annotation 평가가 아니다.

보존한 실패 기록:

1. `phase-c-01`: 12/12 schema 실패. Markdown fence/배열 반환. raw JSON에서 잘못된 α 위치와
   formula line 대신 block ID를 고른 문제도 확인했다.
2. `phase-c-02`: system content를 문자열로 넘겨 설치된 multimodal processor에서 TypeError.
   list-content 형식으로 고치고 실제 local processor에서 검증했다.
3. `phase-c-03`: knowledge ID 복사 오류, unmatched alternatives 과다. raw 응답/검증 실패 보존.
4. `phase-c-04`: figure ID는 맞으나 caption을 빈 native figure text 대신 반환하여 거부.
5. `phase-c-05`: word/α/formula/figure 및 부정 case 검증 완료. `phase-c-06`에서 native ∇ 문자 ID를 추가 검증.

이 fixture는 prompt 수정에 사용했고 block/type 후보 제약이 강하다. 따라서 5/7→6/7의 차이를
SoM의 일반적 품질 향상으로 주장하지 않는다. 실제 강의, 독립 held-out slide, 여러 그림/복잡한 수식,
스캔 OCR 정확도, PDF 필기의 가독성/coverage는 아직 미평가다.

## 테스트 및 다음 작업

- backend 전체: **408 passed**, 39.27s (`FFMPEG_PATH=/usr/bin/ffmpeg`).
  기존 `.env`에 Windows ffmpeg 경로가 있어 최초 전체 실행은 음성 관련 4개 실패/400개 통과였다.
  `.env`를 편집하지 않고 테스트 process에서만 Ubuntu 경로를 지정해 해결했다.
- Phase C/geometry/VLM 관련: 51 passed. 신규 Phase C 테스트 26개 포함.
- frontend player 25 passed, production build passed (기존 bundle warning).

최종 unique-mention 검증 보강 후 관련 51개를 다시 통과했다(4.50s).
실제 14개 응답을 현재 validation으로 재검사한 `phase-c-06/revalidation-v2.json`은 결과 변경 0,
새 GPU 요청 0이다. 최종 host `nvidia-smi` 14:07:00 KST는 10 MiB/Xorg만 남아 모델 종료를 확인했다.

다음 우선순위: 실제 강의 입력/사람 target gold → source_quote/mentions와 복수 그림 모호성 검토 →
Phase D의 evidence/action provenance 및 별도 VLM worker entrypoint/API 연결 → layout/export 통합.
