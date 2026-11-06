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
