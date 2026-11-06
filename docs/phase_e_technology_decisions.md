# Phase E 기술 결정 — 2026-11-06

기존 React19/Vite8/TypeScript/react-pdf11, Python/PyMuPDF, Phase C/D registry·action·evidence를
유지했다. 새 production Python/Node 패키지는 없다. 모델/드라이버/torch 환경은 변경하지 않았다.
아래 링크의 공식 저장소/라이선스를 확인했고 외부 구현 코드를 복사하지 않았다.

| 후보 | 라이선스 / 출처 | 결정과 실제 근거 |
|---|---|---|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF#licensing) | AGPL-3.0 또는 별도 commercial | 기존 의존성 유지. native geometry, vector page copy, ink/font export, raster 비교 재사용. 새 라이선스 권한을 취득했다고 주장하지 않음 |
| [OR-Tools](https://github.com/google/or-tools#license) | Apache-2.0 | 미설치. 현 후보 점수/충돌 hard constraints + bounded A*로 두 회귀 자료를 처리. 복잡한 다중 배치 최적화가 필요해질 때 검토 |
| [Rough.js](https://github.com/rough-stuff/rough#license) | MIT | 아이디어 참고만. backend seed 기반 stroke가 이미 있어 JS에 두 번째 geometry 생성기를 넣으면 export 불일치 위험 |
| [perfect-freehand](https://github.com/steveruizok/perfect-freehand) | MIT | Python 의존성은 이전부터 존재하며 변경 없음. 새 압력 outline 엔진은 사용하지 않음. 약한 deterministic jitter로 충분 |
| [Excalidraw](https://github.com/excalidraw/excalidraw) | MIT | 미설치. 전체 editor는 제품 범위를 벗어남. 간결한 도형/connector UX만 참고 |
| [Xournal++](https://github.com/xournalpp/xournalpp) | GPL-2.0 | UX 참고만. C++/GTK 코드를 복사하거나 임베딩하지 않음 |
| [SlideNote](https://github.com/Cat-blizzard/SlideNote#license) | core AGPL-3.0 / dual license | source inventory 아이디어만 참고. Python 자체 loss audit 구현; AGPL 코드 복사 없음 |
| [lecture-to-notes](https://github.com/drpwchen/lecture-to-notes) | MIT | 기존 audio/state machine/provenance가 이미 있어 추가 파이프라인 도입 안 함 |
| [shadcn/ui](https://github.com/shadcn-ui/ui), [Radix](https://github.com/radix-ui/primitives/blob/main/LICENSE) | MIT | 미설치. 새 복잡한 dialog/editor 없음. native button/input/details와 aria 상태로 구현, Chromium에서 키보드/label 확인 |
| [Tailwind CSS](https://github.com/tailwindlabs/tailwindcss/blob/main/LICENSE) | MIT | 기존 CSS와 경쟁하는 build 체계 추가 대신 CSS tokens/공통 컴포넌트 사용 |
| [lucide](https://github.com/lucide-icons/lucide/blob/main/LICENSE) | ISC 및 포함 자산의 MIT 고지 | 미설치. 필요한 소수의 단순 SVG를 직접 작성 |
| [Zustand](https://github.com/pmndrs/zustand/blob/main/LICENSE) | MIT | 미설치. 기존 React hooks와 hash route/localStorage로 충분 |
| [Motion](https://github.com/motiondivision/motion) | MIT | 미설치. CSS hover/loading과 reduced-motion 지원으로 충분 |
| [Playwright](https://github.com/microsoft/playwright) | Apache-2.0 | 개발 검증에만 `.cache/browser` 설치. 실제 Chromium 업로드/API/audio/반응형/캡처 검증. 앱 runtime 의존성/lock에는 추가하지 않음 |

## 환경과 용량

baseline 실제 설치: PyAV18.1.0, faster-whisper1.2.1. `av==18.1.0`을 pyproject에 직접 고정했다.
`uv lock`은 환경 sync 없이 실행했고 av18/19 분기만18.1로 통합했다(4추가/36삭제).
Torch/CUDA 및 다른 패키지 버전은 바뀌지 않았다. `uv lock --check --offline`과 실제
`faster_whisper.audio.decode_audio` WAV 회귀가 통과했다. `uv sync`를 이번 작업에서 실행하지 않았다.

추가 runtime 설치 0. Chromium/Headless shell 약324MiB 다운로드와 Playwright/작은 ffmpeg
검증 자산은 repo의 ignored `.cache`에만 있다. 시스템 브라우저/라이브러리를 설치하지 않았다.
정확한 디스크 사용량은 최종 handoff에 기록한다. 시작 SSD 여유 약54GiB.

글꼴은 이미 번들된 Pretendard/Nanum Pen(SIL OFL)을 재사용한다. Natural Ink의 본문은
Nanum Pen, Clean/Highlighter는 기존 설정을 사용한다. 번들 Pretendard의 일부 연산자가
PDF에서 아이콘처럼 보이는 실제 문제를 재현해, `= + - −`와 Greek 문자는 기존 CJK fallback으로
분리했다. 새 폰트 다운로드 없음. PDF Unicode 추출과 PNG를 별도로 확인했다.

## 모델 호출 정책

Phase E layout/density/style은 저장된 source/action을 재사용한다. 평가 runner는 VLM factory를
호출하면 실패하도록 막았다. 새 STT/semantic/VLM 실제 호출 없이 동일 action의 v1/v2를 비교했다.
기존 Qwen3-VL8B/NF4/SDPA/SSD 캐시와 worker/OOM 정책은 보존한다.
