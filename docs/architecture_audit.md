# classNote 아키텍처 감사 (Phase 0)

작성 기준: `a95f124` (main, PR #6 머지 시점). 숫자는 추정이 아니라 `data/jobs/`의 실제 강의 job에서 측정한 값이다. 테스트 강의는 영어 슬라이드 73쪽, 한국어 음성 약 36분이다.

## 1. 현재 상태

| 항목 | 사실 |
|---|---|
| 테스트 | backend `pytest` 331개 통과 (67초). frontend `npm run build` 통과. frontend 단위 테스트는 없음 |
| annotation engine | 기본 v1. v2는 `ANNOTATION_V2.md` build order의 1단계만 구현됨 (`evidence` 단계: 단어·줄·여백 geometry와 `mentions.json`). 2~7단계(action schema, 잉크 엔진, planner)는 없음 |
| `mentions.json` | **필기 생성에 전달되지 않는다.** `run.py`가 쓰기만 하고 읽는 코드가 없다 |
| word timestamps | `segments.json`에 저장되지만 `block_align.locate_quote`는 segment 경계만 쓴다. 노트 시간은 segment 단위다 |
| 페이지 재생 | 없음. `<audio controls>` 하나와 `audio.currentTime = note.start`뿐이고, 재생은 끝 시점 없이 계속된다 |
| 페이지 단위 시간 데이터 | 없음. `alignment.json`의 window에서 매번 `pageAtTime`으로 역산한다 |
| live 모드 | `generate_span_notes`, `build_spans`, `consolidate`, `SlideSheet`에 의존. span마다 LLM 1회 |

## 2. 필기 부족의 실제 원인

`lecture-turbo` job의 LLM 응답을 캐시에서 재생해 측정했다. 발화 10,360자, span 33개다.

| 단계 | 결과 | 탈락 |
|---|---|---|
| LLM 원본 응답 | **25개** (span당 0.76개) | |
| quote 검증 (`quote_in_transcript`) | 24개 | 1개 |
| 숫자 검증 (`has_unsupported_number`) | 24개 | 0개 |
| `merge_duplicates` + 페이지당 5개 상한 | 24개 | 0개 |

`df166d990212` job도 같은 양상이다 (원본 14개, 숫자 검증 1개 탈락, 최종 13개).

**결론: 상한이나 검증이 아니라 LLM 단계에서 이미 내용이 사라진다.** 살아남은 필기가 인용한 발화는 전체의 16.8%다. 상한과 검증만 완화해도 필기량은 거의 늘지 않는다.

원인을 큰 순서로 정리하면 다음과 같다.

1. **프롬프트가 생성을 억제한다.** `NOTE_FIELDS_AND_RULES`의 "Only note information that is NOT already on the slide", "about 40 characters", "Only condense what the quote actually says"는 전부 줄이는 방향이다. 교수가 슬라이드 내용을 더 쉽게 풀어 설명한 부분은 규칙상 쓸 수 없다.
2. **필기 하나가 두 역할을 맡는다.** 복습용 내용과 슬라이드 위 표시가 같은 40자 필드다. 보존과 가독성을 함께 달성할 수 없는 구조다.
3. **전사 품질이 낮다.** 전문 용어가 음차·오인식되고("슬랩스토어" = silhouette score), 일본어 환각과 "멘토록, 멘토록" 반복이 있다. 프롬프트의 "Skip only parts that make no sense"로 많은 span이 비게 된다.
4. **정렬이 틀리면 LLM이 엉뚱한 슬라이드와 발화를 받는다.** 3절 참고. 슬라이드와 무관해 보이는 발화는 필기로 잘 이어지지 않는다.
5. **누락을 측정하지 않는다.** coverage 지표가 없고 탈락 사유는 `log.info`로만 남아 job 폴더에 기록되지 않는다.

그 밖의 정보 손실 경로:

- `layout_page`는 공간이 부족하면 가장 긴 노트를 80%씩 잘라 "…"를 붙인다. 원문은 `Annotation.note`에도 남지 않는다.
- 페이지당 5개 상한(`DEFAULT_MAX_NOTES_PER_PAGE`)은 현재는 잘 걸리지 않지만, 생성량이 늘면 바로 병목이 된다.
- 응답 JSON 오류가 두 번 나면 배치 전체(span 최대 12개)가 빠진다. 재실행으로 복구되지만 그때까지 내용이 없다.
- 시험 힌트에 시험 관련 단어가 없으면 `emphasis`로 낮춘다. 삭제가 아니므로 유지할 만하다.

## 3. 정렬 오류 (P0를 오염시키는 문제)

같은 강의의 세 job에서 시작 페이지가 47쪽, 45쪽, 72쪽으로 나오고, 1~3쪽이 강의 끝에 배정된다. 전사문을 직접 읽어 보면 실제 강의는 약 9쪽에서 38쪽까지 거의 순서대로 진행된다. 39쪽 이후는 다루지 않는다.

코드에서 확인한 원인:

- `viterbi`의 첫 window에 시작 페이지 제약이 없다. 어느 페이지에서 시작해도 비용이 0이다.
- `transition_penalties`의 되돌아가기 비용은 거리와 무관하게 1.0이다. 37쪽 앞으로 건너뛰면 약 15인데 37쪽 뒤로 가면 1이다. `CLAUDE.md`의 "heavily penalized"와 모순된다.
- `raw_page`가 대부분 38쪽이다. 한국어 발화가 38쪽의 짧은 제목 슬라이드에 끌려간다. 영어 슬라이드와 한국어 음차 발화의 임베딩 유사도가 약하고, 텍스트가 짧은 페이지가 유리하다.
- 페이지 텍스트가 영어라 한국어 음차 용어(`센트로이드`)와 lexical 신호가 연결되지 않는다.

평가: `samples/golden/alignment_truth.json`에 전사문을 읽고 라벨을 달았다 (제가 추론한 값이므로 사용자 확인 전까지는 참고용). 결과는 `docs/alignment_benchmark.md`에 기록한다.

## 4. 오디오 재생 구조

- `ViewerScreen.tsx`의 `playNote`가 `currentTime`을 옮기고 `play()`한다. 끝 시점이 없어 노트가 끝나도 계속 재생된다.
- 재생 상태와 페이지 탐색 상태가 `follow` 플래그와 `onTimeUpdate` 안에 섞여 있다.
- 필기가 없는 페이지에는 재생 수단이 없다.
- `getAnnotations`가 실패하면 뷰어 전체가 오류 상태가 된다. 즉 LLM이 실패하면 강의를 들을 수도 없다.
- 서버의 `FileResponse`는 HTTP Range를 지원해 seek가 가능하다. `audio.mp3`는 32 kbps mono라 구간 이동이 부담되지 않는다.

## 5. 파이프라인과 재실행

- 단계: extract, caption, transcribe, window, align, evidence(v2 전용), notes, consolidate, export. 각 단계가 JSON을 남겨 `--from-step`으로 재개할 수 있다.
- LLM 캐시 키는 `[provider, model, prompt]`이다. 프롬프트 전문이 키이므로 프롬프트를 고치면 자동으로 무효화된다.
- `consolidate` 이후는 LLM 호출이 없다. density 변경은 여기서부터 재실행하면 STT·정렬을 건드리지 않는다.
- Gemini 무료 등급은 모델당 하루 약 20회다. 배치와 캐시, `QuotaExhausted` 폴백이 이미 있다.
- 오래된 job은 `page_audio.json`이 없다. `alignment.json`에서 파생할 수 있다.

## 6. 문서와 코드의 차이

| 문서 | 실제 |
|---|---|
| `ANNOTATION_V2.md`가 `docs/annotation_prototype.py`를 읽으라고 함 | 파일이 저장소와 git 기록 어디에도 없음 |
| `CLAUDE.md`: 되돌아가기 "heavily penalized" | 거리 무관 1.0으로 오히려 싸다 |
| `README.md`: "Clicking a note plays that part" | 해당 구간만이 아니라 끝까지 계속 재생 |
| `ANNOTATION_V2.md` 2절: mentions를 모델에 전달 | 아직 전달되지 않음 |

## 7. 위험

- **정렬 정답이 약하다.** 라벨이 오인식된 전사문을 읽고 추론한 것이다. 개선 수치는 이 라벨 기준이며 사용자 확인이 필요하다.
- **전사 품질이 내용의 상한을 정한다.** 어떤 후처리도 오인식된 내용을 되살리지는 못한다. "확인 필요" 표시와 원문 음성 연결이 현실적인 대응이다.
- **무료 할당량.** 풍부한 추출은 응답이 길어져 배치 크기를 줄여야 하므로 호출 수가 늘 수 있다.
- **live 모드 회귀.** 공용 함수(`build_spans`, `consolidate`)를 바꾸면 영향을 받는다. live는 기존 경로를 유지한다.

## 8. 개선 계획

1. **Phase 1:** `page_audio.py`와 `page_audio.json`, API, 플레이어 상태 머신, 페이지 재생 UI. 필기 유무와 무관하게 동작.
2. **Phase 3 일부를 앞당김:** 정렬 수정. 페이지 재생의 정확도를 좌우하므로 필기 작업 전에 처리한다.
3. **Phase 2:** 내용 추출, 근거 복구, coverage, 상세 노트, density, 추출 결과에서 파생하는 PDF 필기.
4. **Phase 3~4:** word timing 활용, 슬라이드 단어 앵커, 노트 패널과 타임라인 다듬기, 기술 검토 문서.
