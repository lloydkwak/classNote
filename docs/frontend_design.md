# classNote 프런트엔드 — Phase E

## 참고와 독립 디자인

[Previzion](https://previzion.site/)을 실제 Chromium으로 열어 확인했다. 큰 타이포그래피,
넉넉한 여백, 차분한 dark surface, 적은 CTA를 참고했다. 로고·이미지·소스·상품 기능은 복제하지
않았다. classNote 로고와 아이콘/예시는 자체 SVG·HTML/CSS다. 계정·결제·커뮤니티 UI는 없다.

## 토큰과 컴포넌트

`premium.css`: bg#101116, surface#1b1d25, raised#252832, border#343744,
text#f6f6fa, muted#adb0be, primary#a696ff(어두운 글자), error#ffaaaa.
system sans-serif를 UI에 사용한다. Hero36–66px, 본문12–16px, 세부 상태9–12px.
가독성에 맞는 line-height와 `:focus-visible`, `prefers-reduced-motion`을 적용했다.
주요 text/background contrast는17.49:1, muted/card7.79:1, CTA7.32:1로 계산했다.
전체 WCAG/스크린리더 인증을 수행했다는 뜻은 아니다.

`ui.tsx`: Brand, 자체 SVG Icon, DensityControl, 안전한 preference 읽기/저장.
UploadScreen: 두 file card, drag/drop·선택·교체·삭제, extension/empty/size 검증,
실제 XHR upload percent, 하나의 생성 CTA, density 및 opt-in 정밀 위치 설정.
한쪽 파일만 있으면 CTA disabled + 이유가 있다. 파일명은 text로 출력한다.
100MiB PDF/1GiB 녹음 제한은 API의 임시 업로드 저장에서도 확인한다.

`JobScreen`: 실제 status polling, 현재 단계, 새로고침/hash 복귀, 네트워크 오류·재시도,
부분 결과 보기. 분석 percent는 만들지 않는다. 서버 exception 원문은 화면에 출력하지 않는다.
실패 job은 업로드를 유지한 retry API로 재실행한다. done이지만 incomplete인 job은 notes부터
재시도할 수 있다. 기존 cache/resume를 사용하며 새 업로드를 요구하지 않는다.

`ViewerScreen`: desktop 3열(썸네일/PDF/상세 노트), PDF 원본/필기 비교, zoom/fit,
density·pen style·v1/v2 선택, 다운로드, 발화 근거와 timestamp, 실제 audio/state machine 재사용.
low-confidence 정렬과 추출 실패를 사용자 언어로 알린다. 고급 Inspector 경로는 유지한다.
불확실하거나 off-topic인 시간에 자동으로 다음 페이지를 단정해서 이동하지 않는다.

`SlideSheet`: 서버가 만든 stroke를 그대로 SVG로 그린다. gutter와 output_height를 받아
확장 band까지 같은 viewBox에 표시한다. source PDF와 overlay가 같은 scale을 쓴다.
주석 텍스트는 키보드로 접근 가능한 투명 button도 제공한다. Math run은 일반 손글씨와 분리한다.
PDF와 SVG stroke vertices는 동일하고 font fallback의 시스템별 raster 차이는 남을 수 있다.

## 반응형

- 1440/1280: 썸네일·슬라이드·상세 노트 3열, 상단 density/다운로드, 하단 player.
- 768: 썸네일과 슬라이드, 상세 노트 tab. audio 유지.
- 375: PDF 중심 1열, 슬라이드/페이지/상세 노트 tabs, 별도 density control, 하단 player.
- zoom 시 PDF 영역 내부만 scroll; viewport 가로 overflow는 허용하지 않는다.
- Live/Presentation은 기존 컴포넌트를 LiveUploadScreen으로 보존하고 명시적 모드 진입 후에만
  준비한다. 일반 PDF 선택만으로 불필요한 live job을 만들지 않는다.

## 검증

실제 브라우저: `frontend/scripts/browser-check.mjs`.
`data/phase-e-runs/frontend-final/`에 landing desktop/mobile, processing, error,
result1440/1280/768/375, mobile notes/pages PNG 및 report.json을 저장하고 직접 확인했다.
7개 시나리오 통과. 실제 API의 multipart job 생성/PDF/기존 강의 audio를 검증했다.
업로드 job의 공급자는 mock이며 실제 STT/GPU 품질 검증이 아니다. processing/error 상태만
명시적으로 intercepted fixture이고 retry backend 동작은 Python 테스트에서도 확인했다.

before: `data/phase-e-runs/e-01/frontend-before.png`, `workspace-before.png`.
baseline 캡처를 위해 사용한 8011/5177 검증 서버는 일반 서비스와 분리했으며, 후속 검증은
실제 사용자 job 복사본만 사용한다. 공개 인터넷에 사용자 자료를 전송하지 않았다.

프런트 단위 tests: 기존 playback25 + file validation3 =28. Production build 통과.
실제 microphone 권한/Presentation fullscreen, Safari/Firefox, 스크린리더 수동 검토는 남아 있다.
