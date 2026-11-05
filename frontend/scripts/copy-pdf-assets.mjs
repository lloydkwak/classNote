// Copy font data into public/, so everything renders with no network at the venue.
import { cpSync } from 'node:fs'

for (const name of ['cmaps', 'standard_fonts']) {
  cpSync(`node_modules/pdfjs-dist/${name}`, `public/${name}`, { recursive: true })
}
// The backend's layout measures text with this same file, so line breaks match.
cpSync('../backend/assets/fonts/NanumPenScript-Regular.ttf', 'public/fonts/NanumPenScript-Regular.ttf')
