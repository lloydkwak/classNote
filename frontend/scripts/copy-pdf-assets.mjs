// Copy font data into public/, so everything renders with no network at the venue.
import { cpSync } from 'node:fs'

for (const name of ['cmaps', 'standard_fonts']) {
  cpSync(`node_modules/pdfjs-dist/${name}`, `public/${name}`, { recursive: true })
}
// The backend's layout measures text with these same files, so line breaks match.
for (const name of ['Pretendard-Medium.otf', 'NanumPenScript-Regular.ttf']) {
  cpSync(`../backend/assets/fonts/${name}`, `public/fonts/${name}`)
}
